// VerSes ön yüzü: ElevenLabs ajanına bağlanır, client tool çağrılarını FastAPI'ye iletir,
// sonuçları transkriptte ve sağ paneldeki grafik / tablo / SQL görünümlerinde gösterir.
import { Conversation } from "https://cdn.jsdelivr.net/npm/@elevenlabs/client@1.28.0/+esm";

const $ = (id) => document.getElementById(id);
const ui = {
  status: $("status"), mic: $("mic"), transcript: $("transcript"), chatBody: document.querySelector(".chat-body"),
  form: $("text-form"), input: $("text-input"),
  chart: $("chart"), table: $("table"), sqlView: $("sql"), chartTitle: $("chart-title"),
  sqlBox: $("sql-box"), sqlMeta: $("sql-meta"), copySql: $("copy-sql"),
  wave: document.querySelector(".mic .wave"),
  startVoice: $("start-voice"), startText: $("start-text"), home: $("home"),
  landing: $("landing"), chat: $("chat"), drawer: $("drawer"), drawerClose: $("drawer-close"),
  heroBars: $("hero-bars"), heroRange: $("hero-range"),
};

let conversation = null;
let voiceMode = false;
// Ajanın son araç sonucu: show_chart bunu çizer. Paneldeki görünüm ayrı tutulur (shown), böylece
// kullanıcı eski bir grafiği açtığında ajanın bir sonraki show_chart çağrısı yanlış veriyi çizmez.
let last = null;   // { columns, rows, chart, title?, sql, meta }
let shown = null;  // panelde açık olan sonuç
let schemaCache = null;  // giriş ekranı da şemayı okur (sayılar için); ajan aynısını alır
let schemaNoted = false;

// LLM'e giden sonucu kısa tutmak için: grafik ve tablo tüm satırları kullanır, ajan ilk 100'ü görür
const LLM_ROW_LIMIT = 100;

// ---------------------------------------------------------------- biçimlendirme
const nf = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 });
const AYLAR = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"];
// Yıl, ay numarası ve kimlik kolonları sayı gibi görünür ama binlik ayraçla yazılmamalı (2025, "2.025" değil)
const RAW_COL = /(^|_)(yil|yıl|year|ay_no|id)$|_id$|^id_/i;
const MONEY_COL = /(_tl$|ciro|tutar|gelir|fiyat|sepet)/i;
const fmtCell = (v, col = "") => (typeof v === "number" ? (RAW_COL.test(col) ? String(v) : nf.format(v)) : v ?? "");
const esc = (v) => String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const isMonth = (v) => typeof v === "string" && /^\d{4}-\d{2}$/.test(v);
const isDay = (v) => typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v);
function dateLabel(v) {
  if (isMonth(v)) return `${AYLAR[+v.slice(5, 7) - 1]} ${v.slice(0, 4)}`;
  if (isDay(v)) return `${+v.slice(8, 10)} ${AYLAR[+v.slice(5, 7) - 1]} ${v.slice(0, 4)}`;
  return v;
}
const shortDate = (v) => (isMonth(v) ? `${AYLAR[+v.slice(5, 7) - 1]} ${v.slice(2, 4)}` : dateLabel(v));
// -12.33 → "−%12,33": Türkçede yüzde işareti sayıdan önce gelir
const pct = (v) => (v == null ? "hesaplanamadı" : `${v < 0 ? "−" : v > 0 ? "+" : ""}%${nf.format(Math.abs(v))}`);
// ciro_tl → "Ciro", ort_teslim_gun → "Ort teslim gun": KPI etiketinde ham kolon adı yerine
const prettyCol = (c) => {
  const t = String(c).replace(/_tl$/i, "").replace(/_/g, " ").trim();
  return t ? t.charAt(0).toLocaleUpperCase("tr") + t.slice(1) : String(c);
};
// Ajan SQL'i tek satır yazınca okunur olsun diye ana anahtar kelimelerden önce satır kır (tırnak içi hariç)
function prettySql(sql) {
  if (!sql || sql.includes("\n")) return sql;
  return sql.split(/('(?:[^']|'')*')/).map((part, i) => (i % 2 ? part : part.replace(
    /\s+(FROM|(?:LEFT |INNER |CROSS )?JOIN|WHERE|GROUP BY|HAVING|ORDER BY|LIMIT|UNION(?: ALL)?)\b/gi, "\n$1"))).join("");
}

// Büyük tutarları sesli cevaptaki gibi kısalt: 12.255.096 → "12,3 mn"
function compact(v) {
  const a = Math.abs(v);
  if (a >= 1e9) return `${nf.format(+(v / 1e9).toFixed(1))} mr`;
  if (a >= 1e6) return `${nf.format(+(v / 1e6).toFixed(1))} mn`;
  if (a >= 1e4) return `${nf.format(+(v / 1e3).toFixed(0))} bin`;
  return nf.format(v);
}

function setStatus(text, state = "idle") {
  ui.status.textContent = text;
  ui.status.dataset.state = state;
}

// ---------------------------------------------------------------- aşama göstergesi
// idle | listening | thinking | tool | speaking: başlıktaki adım şeridi ve mikrofon dalgası bunu izler
let stage = "idle";
const ORDER = ["listening", "thinking", "tool", "speaking"];
function setStage(s) {
  stage = s;
  document.body.dataset.stage = s;
  const at = ORDER.indexOf(s);
  document.querySelectorAll(".pipeline li").forEach((li) => {
    const i = ORDER.indexOf(li.dataset.step);
    li.classList.toggle("active", i === at);
    li.classList.toggle("done", at > 0 && i < at);
    if (i === at) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
  });
}

function scrollToEnd() {
  ui.chatBody.scrollTop = ui.chatBody.scrollHeight;
}

function addMsg(role, text) {
  ui.transcript.querySelector(".hint")?.remove();
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  // Ses modelinin duygu etiketlerini ([happy], [slow] gibi) ekranda gösterme
  el.textContent = role === "ai" ? String(text).replace(/\[[a-z ]{2,20}\]\s*/gi, "").trim() : text;
  ui.transcript.append(el);
  scrollToEnd();
  return el;
}

async function api(path, body) {
  try {
    const res = await fetch(path, body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok) {
      // FastAPI doğrulama hataları (422) detail listesi döner; ajana okunur bir mesaj ver
      const detail = data?.detail;
      const msg = Array.isArray(detail) ? detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ") : detail;
      return { ok: false, error: `Sunucu hatası (${res.status})${msg ? ": " + msg : ""}` };
    }
    return data ?? { ok: false, error: "Sunucudan geçersiz cevap geldi." };
  } catch (e) {
    return { ok: false, error: `Sunucuya ulaşılamadı: ${e.message || e}` };
  }
}

// ---------------------------------------------------------------- client tools
const clientTools = {
  async get_schema() {
    setStage("tool");
    // Şema oturum boyunca değişmez; ajan tekrar isterse sunucuya gitmeden aynısını ver
    if (!schemaCache) {
      const note = addMsg("tool", "Şema okunuyor…");
      const r = await api("/tools/get_schema");
      if (!r.ok) { note.textContent = "Şema okunamadı"; note.classList.add("err"); return JSON.stringify(r); }
      schemaCache = r;
      note.remove();
    }
    if (!schemaNoted) { schemaNoted = true; addMsg("tool", `Şema okundu: ${schemaCache.tablolar.length} tablo`); }
    return JSON.stringify(schemaCache);
  },

  async run_query({ sql } = {}) {
    setStage("tool");
    const note = addMsg("tool", "Sorgu çalışıyor…");
    const r = await api("/tools/run_query", { sql: sql ?? "" });
    if (!r.ok) {
      note.textContent = "Sorgu hata verdi, ajan düzeltiyor";
      note.classList.add("err");
      last = null;
      return JSON.stringify(r);
    }
    const meta = `${nf.format(r.row_count)} satır${r.truncated ? " (sınırda kesildi)" : ""} · ${r.elapsed_ms} ms`;
    last = { columns: r.columns, rows: r.rows, chart: r.chart, sql: prettySql(r.sql), meta };
    note.textContent = `Sorgu tamam: ${meta}`;
    const { chart, ...forLlm } = r;
    if (r.rows.length > LLM_ROW_LIMIT) {
      forLlm.rows = r.rows.slice(0, LLM_ROW_LIMIT);
      forLlm.note = `Ekranda ${r.rows.length} satırın tamamı var; burada ilk ${LLM_ROW_LIMIT} satır gösterildi.`;
    }
    forLlm.suggested_chart = chart?.type;
    return JSON.stringify(forLlm);
  },

  async analyze_trend(params = {}) {
    setStage("tool");
    const note = addMsg("tool", "Trend analizi yapılıyor…");
    const r = await api("/tools/analyze_trend", params);
    if (!r.ok) {
      note.textContent = "Trend analizi hata verdi";
      note.classList.add("err");
      last = null;
      return JSON.stringify(r);
    }
    const genel = pct(r.degisim_yuzde);
    note.textContent = `Trend analizi tamam: genel değişim ${genel}`;
    // En belirgin boyutun kırılımını grafiğe hazırla
    const boyut = r.en_belirgin_kirilim?.boyut;
    if (!boyut) { last = null; return JSON.stringify(r); }
    const a = `${dateLabel(r.donem_a.baslangic)} – ${dateLabel(r.donem_a.bitis)}`;
    const b = `${dateLabel(r.donem_b.baslangic)} – ${dateLabel(r.donem_b.bitis)}`;
    const rows = (r.kirilimlar[boyut] || []).map((k) => [k.grup, k.donem_a, k.donem_b, k.degisim_yuzde]);
    const BOYUT_AD = { kategori: "Kategori", sehir: "Şehir", odeme_turu: "Ödeme türü", kargo_firmasi: "Kargo firması", marka: "Marka" };
    last = {
      columns: [BOYUT_AD[boyut] || boyut, `Önce (${a})`, `Sonra (${b})`, "Değişim %"],
      rows,
      chart: { type: "bar", x: BOYUT_AD[boyut] || boyut, y: [`Önce (${a})`, `Sonra (${b})`] },
      title: `${r.metrik_etiketi}: ${(BOYUT_AD[boyut] || boyut).toLocaleLowerCase("tr")} kırılımı`,
      sql: `-- analyze_trend aracı (sabit şablonlu SQL)\nmetrik : ${r.metrik}\nönce   : ${r.donem_a.baslangic} – ${r.donem_a.bitis}\nsonra  : ${r.donem_b.baslangic} – ${r.donem_b.bitis}\nboyut  : ${params.dimension || "hepsi"}`,
      meta: `Genel değişim ${genel}`,
    };
    return JSON.stringify(r);
  },

  show_chart({ title, chart_type = "auto", x, y } = {}) {
    if (!last) return "Gösterilecek sonuç yok. Önce run_query çağır.";
    const spec = { ...last.chart };
    if (chart_type && chart_type !== "auto") spec.type = chart_type;
    if (x && last.columns.includes(x)) spec.x = x;
    if (y) {
      const ys = (Array.isArray(y) ? y : String(y).split(",")).map((s) => s.trim()).filter((c) => last.columns.includes(c));
      if (ys.length) spec.y = ys;
    }
    addResult({ data: last, spec, title: title || last.title || "Sonuç" });
    return "Grafik hazırlandı; kullanıcı sohbetteki 'Grafik hazır' butonuyla sağ panelde açabilir.";
  },
};

// ---------------------------------------------------------------- sonuç kartları ve sağ panel
// Her show_chart sohbete bir kart ekler; tıklayınca o sonuç sağ panelde açılır.
const KIND = { kpi: "Sonuç hazır", line: "Grafik hazır", bar: "Grafik hazır", table: "Tablo hazır" };

// Kart üzerindeki küçük önizleme: verinin gerçek şekli (çizgi ya da çubuk), süs değil
function thumb(spec, data) {
  const { columns, rows } = data;
  if (spec.type === "kpi") {
    const i = columns.findIndex((_, j) => typeof rows[0]?.[j] === "number");
    return `<span class="k">${i >= 0 ? esc(compact(rows[0][i])) : "—"}</span>`;
  }
  const yi = columns.indexOf(spec.y?.[0]);
  if (spec.type === "table" || yi < 0) {
    return `<svg viewBox="0 0 52 32" aria-hidden="true">${[4, 12, 20, 28].map((y) =>
      `<line x1="0" x2="52" y1="${y}" y2="${y}" stroke="var(--rule-strong)" stroke-width="1.5"/>`).join("")}</svg>`;
  }
  // Seri kolonu varsa (uzun format) aynı x'leri topla
  let vals;
  if (spec.series && columns.includes(spec.series)) {
    const xi = columns.indexOf(spec.x), sums = new Map();
    for (const r of rows) sums.set(r[xi], (sums.get(r[xi]) || 0) + (+r[yi] || 0));
    vals = [...sums.values()];
  } else vals = rows.map((r) => +r[yi] || 0);
  vals = vals.slice(0, spec.type === "line" ? 60 : 14);
  const max = Math.max(...vals, 0) || 1, min = Math.min(...vals, 0);
  const sy = (v) => 31 - ((v - min) / (max - min || 1)) * 29;
  if (spec.type === "line") {
    const pts = vals.map((v, i) => `${(i / Math.max(vals.length - 1, 1)) * 52},${sy(v).toFixed(1)}`).join(" ");
    return `<svg viewBox="0 0 52 32" aria-hidden="true"><polyline points="${pts}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
  }
  const w = 52 / vals.length;
  return `<svg viewBox="0 0 52 32" aria-hidden="true">${vals.map((v, i) => {
    const h = Math.max(2, 31 - sy(Math.max(v, 0))); // küçük değerler nokta gibi kaybolmasın
    return `<rect x="${(i * w + w * 0.15).toFixed(1)}" y="${(31 - h).toFixed(1)}" width="${(w * 0.7).toFixed(1)}" height="${h.toFixed(1)}" rx="1" fill="var(--s1)"/>`;
  }).join("")}</svg>`;
}

function addResult(snap) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "result-btn";
  btn.innerHTML = `<span class="thumb">${thumb(snap.spec, snap.data)}</span>
    <span class="txt"><b>${KIND[snap.spec.type] || KIND.bar}</b><small></small></span><span class="go">Aç →</span>`;
  btn.querySelector("small").textContent = snap.title;
  btn.addEventListener("click", () => openResult(snap, btn));
  ui.transcript.append(btn);
  scrollToEnd();
}

let lastOpener = null;
function openResult(snap, btn) {
  const wasOpen = document.body.classList.contains("drawer-open");
  document.querySelectorAll(".result-btn.active").forEach((b) => b.classList.remove("active"));
  btn?.classList.add("active");
  lastOpener = btn;
  shown = snap;
  ui.drawer.inert = false;
  document.body.classList.add("drawer-open");
  ui.chartTitle.textContent = snap.title;
  ui.sqlMeta.textContent = snap.data.meta || "";
  ui.sqlBox.firstElementChild.textContent = snap.data.sql || "";
  renderTable(snap.data);
  setView("chart");
  ui.chart.innerHTML = "";
  // panel açılma animasyonu bitince çiz ki Plotly doğru genişliği alsın
  setTimeout(() => {
    if (shown !== snap) return; // bu arada başka bir sonuç açıldı
    try { renderChart(snap.spec, snap.data); } catch (e) { console.error(e); setView("table"); }
  }, wasOpen ? 0 : 320);
  ui.chartTitle.focus({ preventScroll: true });
}

function closeDrawer() {
  if (!document.body.classList.contains("drawer-open")) return;
  document.body.classList.remove("drawer-open");
  ui.drawer.inert = true;
  document.querySelectorAll(".result-btn.active").forEach((b) => b.classList.remove("active"));
  if (lastOpener?.isConnected) lastOpener.focus({ preventScroll: true });
}
ui.drawerClose.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

ui.copySql.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(ui.sqlBox.textContent);
    ui.copySql.textContent = "Kopyalandı";
  } catch {
    ui.copySql.textContent = "Kopyalanamadı";
  }
  setTimeout(() => { ui.copySql.textContent = "Kopyala"; }, 1500);
});

// ---------------------------------------------------------------- ekranlar
function showScreen(name) {
  document.body.dataset.screen = name;
  ui.landing.hidden = name !== "landing";
  ui.chat.hidden = name !== "chat";
  if (name === "landing") closeDrawer();
  window.scrollTo(0, 0);
}

// ---------------------------------------------------------------- görselleştirme
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const series = () => [1, 2, 3, 4, 5, 6, 7, 8].map((i) => cssVar(`--s${i}`));

function renderChart(spec, data) {
  const { columns, rows } = data;
  const col = (name) => columns.indexOf(name);
  ui.chart.innerHTML = "";
  if (!rows.length) { ui.chart.innerHTML = `<div class="kpi"><div class="big"><span>Bu sorgu sonuç döndürmedi.</span></div></div>`; return; }

  if (spec.type === "kpi") {
    const ys = spec.y?.length ? spec.y : columns.filter((_, i) => typeof rows[0]?.[i] === "number");
    // 1 satırlık sonuçtaki metin kolonları (ör. en çok satan şehir) sayının bağlamıdır; atma
    const ctx = columns.map((c, i) => [c, rows[0][i]]).filter(([c, v]) => !ys.includes(c) && v != null && typeof v !== "number");
    ui.chart.innerHTML = `<div class="kpi">
      ${ctx.length ? `<p class="ctx">${ctx.map(([c, v]) => `${esc(prettyCol(c))}: <b>${esc(dateLabel(v))}</b>`).join(" · ")}</p>` : ""}
      ${ys.map((c) => {
        const v = rows[0][col(c)];
        return `<div class="big"><strong>${esc(fmtCell(v, c))}${MONEY_COL.test(c) && typeof v === "number" ? "<small>TL</small>" : ""}</strong><span>${esc(prettyCol(c))}</span></div>`;
      }).join("")}</div>`;
    return;
  }
  // x verilmemişse ilk metin kolonunu kullan (ör. ajan kpi önerisini "bar" ile ezdiyse)
  if (!spec.x || col(spec.x) < 0) spec = { ...spec, x: columns.find((_, i) => typeof rows[0][i] !== "number") };
  if (spec.type === "table" || !spec.x || !spec.y?.length || !window.Plotly) { setView("table"); return; }

  const pal = series();
  const xs = rows.map((r) => r[col(spec.x)]);
  const timeAxis = xs.every((v) => isMonth(v) || isDay(v));
  // Çok noktalı zaman ekseninde kısa etiket (Oca 26), az noktada tam (Oca 2026)
  const xLabel = (v) => (!timeAxis ? v : new Set(xs).size > 8 ? shortDate(v) : dateLabel(v));
  // Uzun kategori adları ya da çok sayıda kategori: yatay çubuk daha okunur
  const horizontal = spec.type === "bar" && !timeAxis &&
    (xs.length > 8 || xs.some((v) => String(v).length > 12));

  let traces;
  if (spec.type === "line" && spec.series && col(spec.series) >= 0) {
    // Uzun formatı (ay, kategori, değer) her kategori için ayrı çizgiye çevir
    const groups = new Map();
    for (const r of rows) {
      const g = r[col(spec.series)];
      if (!groups.has(g)) groups.set(g, { x: [], y: [] });
      groups.get(g).x.push(xLabel(r[col(spec.x)]));
      groups.get(g).y.push(r[col(spec.y[0])]);
    }
    // 8'den fazla seri olursa renk tekrarlanmasın: ilk 7 ayrı, kalanı gri tek bir "Diğer" tonunda
    traces = [...groups].map(([name, d], i) => ({
      ...d, name: String(name), type: "scatter", mode: "lines",
      line: { color: i < 8 ? pal[i] : cssVar("--muted"), width: 2 },
      hovertemplate: "%{y:,.0f}<extra>%{fullData.name}</extra>",
    }));
  } else {
    const many = rows.length > 24;
    traces = spec.y.map((c, i) => {
      const vals = rows.map((r) => r[col(c)]);
      const t = {
        name: c,
        type: spec.type === "line" ? "scatter" : "bar",
        marker: { color: pal[i % 8] },
        hovertemplate: `%{${horizontal ? "x" : "y"}:,.2~f}<extra>${esc(c)}</extra>`,
      };
      if (spec.type === "line") {
        t.mode = many ? "lines" : "lines+markers";
        t.line = { color: pal[i % 8], width: 2 };
        t.marker = { color: pal[i % 8], size: 8, line: { color: cssVar("--surface"), width: 2 } };
      }
      if (horizontal) { t.orientation = "h"; t.x = vals; t.y = xs.map(xLabel); }
      else { t.x = xs.map(xLabel); t.y = vals; }
      return t;
    });
  }

  const ink2 = cssVar("--ink-2"), rule = cssVar("--rule"), ink = cssVar("--ink");
  const valueAxis = { gridcolor: rule, zerolinecolor: cssVar("--rule-strong"), tickformat: "~s", automargin: true, tickfont: { family: cssVar("--mono"), size: 11 } };
  const catAxis = { type: "category", automargin: true, showgrid: false, linecolor: cssVar("--rule-strong"), tickfont: { size: 12 } };
  Plotly.newPlot(ui.chart, traces, {
    margin: { t: 8, r: 8, b: 8, l: 8 },
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: ink2, family: cssVar("--sans"), size: 12 },
    xaxis: horizontal ? valueAxis : { ...catAxis, nticks: 12 },
    yaxis: horizontal ? { ...catAxis, autorange: "reversed" } : valueAxis,
    separators: ",.",
    barmode: "group", bargap: 0.3, bargroupgap: 0.08, barcornerradius: 3,
    hovermode: spec.type === "line" ? "x unified" : "closest",
    hoverlabel: { bgcolor: ink, bordercolor: ink, font: { color: cssVar("--bg"), family: cssVar("--sans") } },
    showlegend: traces.length > 1,
    legend: { orientation: "h", x: 0, y: 1.02, yanchor: "bottom", font: { size: 12, color: ink2 } },
  }, { responsive: true, displayModeBar: false });
}

function renderTable(data) {
  const { columns, rows } = data;
  const numeric = columns.map((_, i) => rows.some((r) => typeof r[i] === "number"));
  const head = columns.map((c, i) => `<th class="${numeric[i] ? "num" : ""}">${esc(c)}</th>`).join("");
  const body = rows.slice(0, 500).map((r) =>
    `<tr>${r.map((v, i) => `<td class="${typeof v === "number" ? "num" : ""}">${esc(fmtCell(v, columns[i]))}</td>`).join("")}</tr>`).join("");
  ui.table.innerHTML = rows.length
    ? `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
    : `<p style="padding:16px" class="muted">Sorgu sonuç döndürmedi.</p>`;
}

function setView(v) {
  ui.chart.hidden = v !== "chart";
  ui.table.hidden = v !== "table";
  ui.sqlView.hidden = v !== "sql";
  document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.view === v)));
  if (v === "chart" && window.Plotly && ui.chart.querySelector(".js-plotly-plot, .plot-container")) Plotly.Plots.resize(ui.chart);
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setView(t.dataset.view)));
setView("chart");

// ---------------------------------------------------------------- oturum
let starting = null;
function start(textOnly) {
  starting ??= openSession(textOnly).finally(() => { starting = null; });
  return starting;
}

function errText(m) {
  if (m == null) return "bilinmeyen hata";
  if (typeof m === "string") return m;
  return m.message || m.reason || JSON.stringify(m);
}

async function openSession(textOnly) {
  setStatus("Bağlanıyor…");
  const s = await api("/api/session");
  if (!s.ok) throw new Error(s.error);
  const auth = s.signed_url ? { signedUrl: s.signed_url } : { agentId: s.agent_id };
  let conv = null; // onDisconnect, startSession bitmeden de çağrılabilir
  conv = await Conversation.startSession({
    ...auth,
    textOnly,
    clientTools,
    onConnect: () => setStatus(textOnly ? "Bağlı · yazılı" : "Bağlı · sesli", "connected"),
    onDisconnect: () => {
      if (conversation !== conv && conversation !== null) return; // eski oturumun geç gelen kapanışı
      conversation = null; setVoiceUi(false); setStatus("Bağlı değil"); setStage("idle");
    },
    onError: (m) => { setStatus("Hata", "error"); addMsg("tool err", `Hata: ${errText(m)}`); },
    onMessage: ({ message, role, source }) => {
      const who = (role || source) === "user" ? "user" : "ai";
      addMsg(who, message);
      if (who === "user") setStage("thinking");
      else if (!voiceMode) setStage("idle"); // sesli modda konuşma bitince onModeChange dinlemeye döndürür
    },
    onModeChange: ({ mode }) => {
      if (mode === "speaking") setStage("speaking");
      else if (voiceMode && stage !== "thinking" && stage !== "tool") setStage("listening");
    },
  });
  conversation = conv;
  return conv;
}

async function endConversation() {
  const c = conversation;
  conversation = null;
  if (c) { try { await c.endSession(); } catch { /* zaten kapalı */ } }
}

function setVoiceUi(on) {
  voiceMode = on;
  ui.mic.classList.toggle("live", on);
  ui.mic.setAttribute("aria-pressed", String(on));
  ui.mic.setAttribute("aria-label", on ? "Sesli konuşmayı bitir" : "Sesli konuşmayı başlat");
  setStage(on ? "listening" : "idle");
}

let toggling = false;
async function toggleVoice() {
  if (toggling) return; // çift tıklamada iki oturum açılmasın
  toggling = true;
  try {
    if (voiceMode) { await endConversation(); setVoiceUi(false); setStatus("Bağlı değil"); return; }
    if (starting) await starting.catch(() => {});
    // Önce mikrofon iznini al: izin yoksa açık yazılı oturum boşuna kapanmasın.
    // Akışı hemen kapat; SDK kendi akışını açar, bizimki açık kalırsa tarayıcı mikrofonu kullanımda gösterir.
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    await endConversation(); // yazılı oturum açıksa kapat, sesliye geç
    await start(false);
    setVoiceUi(true);
  } catch (e) {
    setStatus("Hata", "error");
    const izin = {
      NotAllowedError: "Mikrofon izni verilmedi. Adres çubuğundan izin verebilir ya da yazarak sorabilirsin.",
      NotFoundError: "Mikrofon bulunamadı. Bir mikrofon bağlayabilir ya da yazarak sorabilirsin.",
      NotReadableError: "Mikrofon başka bir uygulama tarafından kullanılıyor.",
    }[e?.name];
    addMsg("tool err", izin || `Mikrofon veya bağlantı hatası: ${errText(e)}`);
  } finally {
    toggling = false;
  }
}

ui.mic.addEventListener("click", toggleVoice);
ui.startVoice.addEventListener("click", () => { showScreen("chat"); toggleVoice(); });
ui.startText.addEventListener("click", () => { showScreen("chat"); ui.input.focus(); });
ui.home.addEventListener("click", async () => {
  await endConversation();
  setVoiceUi(false);
  setStatus("Bağlı değil");
  showScreen("landing");
});

async function sendText(text) {
  text = text.trim();
  if (!text) return;
  try {
    if (!conversation) await start(true);
    addMsg("user", text); // yazılan mesaj sesli modda da transkripte düşmez, kendimiz ekleriz
    conversation.sendUserMessage(text);
    setStage("thinking");
  } catch (e) {
    setStatus("Hata", "error");
    addMsg("tool err", `Bağlantı hatası: ${errText(e)}`);
  }
}

ui.form.addEventListener("submit", (e) => {
  e.preventDefault();
  sendText(ui.input.value);
  ui.input.value = "";
});
ui.transcript.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) sendText(chip.textContent);
});

// ---------------------------------------------------------------- mikrofon dalgası
// Sesli modda düğmenin içindeki beş çubuk mikrofon (dinlerken) ya da ajanın sesi (konuşurken) ile oynar.
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
let level = 0;
function volume() {
  if (!conversation || !voiceMode) return 0;
  try {
    return stage === "speaking" ? conversation.getOutputVolume() : stage === "listening" ? conversation.getInputVolume() : 0;
  } catch { return 0; }
}
function drawWave(t) {
  level += (Math.min(1, volume() * 2.4) - level) * 0.25;
  if (voiceMode && ui.wave.offsetParent) {
    const ctx = ui.wave.getContext("2d"), W = ui.wave.width, H = ui.wave.height;
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = getComputedStyle(ui.mic).color;
    const bw = W * 0.07, gap = W * 0.05, n = 5, x0 = (W - (n * bw + (n - 1) * gap)) / 2;
    for (let i = 0; i < n; i++) {
      const phase = reduceMotion ? 0 : Math.sin(t / 160 + i * 1.3);
      const busy = stage === "thinking" || stage === "tool" ? 0.25 + 0.12 * phase : 0;
      const h = H * Math.max(0.1, Math.min(0.62, 0.1 + level * (0.55 + 0.2 * phase) * [0.6, 0.85, 1, 0.85, 0.6][i] + busy));
      const x = x0 + i * (bw + gap), y = (H - h) / 2;
      ctx.beginPath();
      ctx.roundRect ? ctx.roundRect(x, y, bw, h, bw / 2) : ctx.rect(x, y, bw, h);
      ctx.fill();
    }
  }
  requestAnimationFrame(drawWave);
}
requestAnimationFrame(drawWave);
setStage("idle");

// ---------------------------------------------------------------- giriş: canlı veriden aylık ciro
const HERO_SQL = `SELECT substr(siparis_tarihi, 1, 7) AS ay, ROUND(SUM(toplam_tutar_tl)) AS ciro
FROM siparisler WHERE durum != 'İptal Edildi' GROUP BY 1 ORDER BY 1`;

let heroRows = null;
function drawHero(rows, animate = true) {
  heroRows = rows;
  const box = ui.heroBars;
  // SVG'yi kutunun gerçek piksel boyutunda çiz: ölçekleme yazıları esnetmesin
  const W = Math.max(280, box.clientWidth), H = Math.max(180, box.clientHeight), top = 34, bottom = 40, n = rows.length;
  const max = Math.max(...rows.map((r) => r[1])) || 1;
  const step = W / n, bw = Math.max(4, step * 0.62);
  const peak = rows.reduce((p, r, i) => (r[1] > rows[p][1] ? i : p), 0);
  const y = (v) => top + (H - top - bottom) * (1 - v / max);
  let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" aria-hidden="true">`;
  rows.forEach(([ay, v], i) => {
    const x = i * step + (step - bw) / 2, yy = y(v);
    svg += `<g class="col" data-i="${i}"><rect x="${i * step}" y="0" width="${step}" height="${H - bottom}" fill="transparent"/>`
      + `<rect class="bar${i === peak ? " peak" : ""}" style="--i:${i}" x="${x.toFixed(1)}" y="${yy.toFixed(1)}" width="${bw.toFixed(1)}" height="${(H - bottom - yy).toFixed(1)}" rx="2"/></g>`;
    const m = +ay.slice(5, 7);
    if (m === 1 || i === 0) svg += `<text class="tick year" x="${(i * step + step / 2).toFixed(1)}" y="${H - 8}" text-anchor="middle">${ay.slice(0, 4)}</text>`;
    if (m % 3 === 1 && step * 3 > 34) svg += `<text class="tick" x="${(i * step + step / 2).toFixed(1)}" y="${H - 24}" text-anchor="middle">${AYLAR[m - 1]}</text>`;
  });
  svg += `<line class="base" x1="0" x2="${W}" y1="${H - bottom + 0.5}" y2="${H - bottom + 0.5}"/>`;
  // Zirve ayı doğrudan etiketlenir: metin mürekkep renginde, çubuk vurgu renginde
  const px = peak * step + step / 2, anchor = px > W * 0.75 ? "end" : px < W * 0.25 ? "start" : "middle";
  svg += `<text class="peak-label" x="${px.toFixed(1)}" y="${(y(rows[peak][1]) - 12).toFixed(1)}" text-anchor="${anchor}">${dateLabel(rows[peak][0])} <tspan class="v">${compact(rows[peak][1])} TL</tspan></text>`;
  svg += "</svg>";
  box.innerHTML = svg;
  box.setAttribute("aria-label", `Aylık ciro, ${dateLabel(rows[0][0])} ile ${dateLabel(rows[n - 1][0])} arası. En yüksek ay ${dateLabel(rows[peak][0])}: ${compact(rows[peak][1])} TL.`);
  ui.heroRange.textContent = `${dateLabel(rows[0][0])} – ${dateLabel(rows[n - 1][0])}`;
  if (animate) requestAnimationFrame(() => box.classList.add("ready"));

  // Üzerine gelince ay ve tutar: SVG ölçeklendiği için konumu sütunun ekrandaki yerinden al
  const tip = document.createElement("div");
  tip.className = "tip"; tip.hidden = true; box.append(tip);
  box.querySelectorAll(".col").forEach((g) => {
    g.addEventListener("pointerenter", () => {
      const i = +g.dataset.i, bar = g.querySelector(".bar").getBoundingClientRect(), b = box.getBoundingClientRect();
      tip.innerHTML = `<b>${dateLabel(rows[i][0])}</b>${nf.format(rows[i][1])} TL`;
      tip.style.left = `${bar.left - b.left + bar.width / 2}px`;
      tip.style.top = `${bar.top - b.top}px`;
      tip.hidden = false;
    });
    g.addEventListener("pointerleave", () => { tip.hidden = true; });
  });
}

function drawHeroPlaceholder() {
  // Sunucu kapalıysa düz bir iskelet göster; sahte veri çizme
  ui.heroBars.classList.add("empty");
  ui.heroBars.innerHTML = `<svg width="100%" height="100%" aria-hidden="true"><line class="base" x1="0" x2="100%" y1="85%" y2="85%"/></svg>`;
  ui.heroBars.setAttribute("aria-label", "Grafik için sunucuya ulaşılamadı");
  $("hero-note").textContent = "Sunucuya ulaşılamadı. Uygulamayı uvicorn ile başlattığından emin ol.";
}

async function loadLanding() {
  const [q, s] = await Promise.all([api("/tools/run_query", { sql: HERO_SQL }), api("/tools/get_schema")]);
  if (q.ok && q.rows.length) drawHero(q.rows); else drawHeroPlaceholder();
  if (s.ok) {
    schemaCache = s;
    const n = Object.fromEntries(s.tablolar.map((t) => [t.tablo, t.satir_sayisi]));
    for (const dd of document.querySelectorAll("[data-fact]")) {
      const k = dd.dataset.fact;
      const v = k === "sehir" ? s.olasi_degerler?.["musteriler.sehir"]?.length : n[k];
      if (v != null) dd.textContent = nf.format(v);
    }
  }
}
loadLanding();
// Pencere boyutu değişince giriş grafiğini yeni genişlikte yeniden çiz (animasyonsuz)
let heroW = 0;
new ResizeObserver(([e]) => {
  const w = Math.round(e.contentRect.width);
  if (heroRows && w && w !== heroW) { heroW = w; drawHero(heroRows, false); }
}).observe(ui.heroBars);

// Test ve değerlendirme betikleri için
window.voicebi = { clientTools, sendText, get last() { return last; } };
