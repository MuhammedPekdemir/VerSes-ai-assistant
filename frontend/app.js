// VerSes ön yüzü: ElevenLabs ajanına bağlanır, client tool çağrılarını FastAPI'ye iletir,
// sonuçları transkript, grafik ve SQL kutusunda gösterir.
import { Conversation } from "https://cdn.jsdelivr.net/npm/@elevenlabs/client@1.28.0/+esm";

const $ = (id) => document.getElementById(id);
const ui = {
  status: $("status"), mic: $("mic"), micLabel: $("mic-label"), mode: $("mode"),
  transcript: $("transcript"), form: $("text-form"), input: $("text-input"),
  chart: $("chart"), table: $("table"), chartTitle: $("chart-title"),
  sqlBox: $("sql-box"), sqlMeta: $("sql-meta"),
  orbs: [...document.querySelectorAll(".orb-canvas")],
  startOrb: $("start-orb"), startText: $("start-text"), home: $("home"),
  landing: $("landing"), chat: $("chat"), drawer: $("drawer"), drawerClose: $("drawer-close"),
};

let conversation = null;
let voiceMode = false;
let last = null; // { columns, rows, sql, chart }
let view = "chart";

// LLM'e giden sonucu kısa tutmak için: grafik ve tablo tüm satırları kullanır, ajan ilk 100'ü görür
const LLM_ROW_LIMIT = 100;

// ---------------------------------------------------------------- yardımcılar
const nf = new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 2 });
const fmt = (v) => (typeof v === "number" ? nf.format(v) : v ?? "");
// Kolon adları ve değerler ajanın yazdığı SQL'den gelir; innerHTML'e girmeden önce kaçışla
const esc = (v) => String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function setStatus(text, state = "idle") {
  ui.status.textContent = text;
  ui.status.dataset.state = state;
}

// ---------------------------------------------------------------- aşama göstergesi
// idle | listening | thinking | tool | speaking: küre animasyonu ve işlem hattı bunu izler
let stage = "idle";
const STAGE_TEXT = {
  idle: () => (conversation ? "Hazır" : "Bağlı değil"),
  listening: () => "Dinliyorum…",
  thinking: () => "Düşünüyorum…",
  tool: () => "Veriye bakıyorum…",
  speaking: () => "Cevaplıyorum…",
};
const ORDER = ["listening", "thinking", "tool", "speaking"];
function setStage(s, toolLabel) {
  stage = s;
  document.body.dataset.stage = s;
  ui.mode.textContent = STAGE_TEXT[s]();
  const at = ORDER.indexOf(s);
  document.querySelectorAll(".pipeline li").forEach((li) => {
    const i = ORDER.indexOf(li.dataset.step);
    li.classList.toggle("active", i === at);
    li.classList.toggle("done", at > 0 && i < at && s !== "listening");
  });
}

function addMsg(role, text) {
  ui.transcript.querySelector(".hint")?.remove();
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  // Ses modelinin duygu etiketlerini ([happy], [slow] gibi) ekranda gösterme
  el.textContent = role === "ai" ? String(text).replace(/\[[a-z ]{2,20}\]\s*/gi, "").trim() : text;
  ui.transcript.append(el);
  ui.transcript.parentElement.scrollTop = ui.transcript.parentElement.scrollHeight;
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

function showSql(text, meta = "") {
  ui.sqlBox.firstElementChild.textContent = text;
  ui.sqlMeta.textContent = meta;
}

// ---------------------------------------------------------------- client tools
const clientTools = {
  async get_schema() {
    setStage("tool", "Şema · tabloları öğrenir");
    addMsg("tool", "Şema okunuyor…");
    return JSON.stringify(await api("/tools/get_schema"));
  },

  async run_query({ sql }) {
    setStage("tool", "SQL · veritabanını sorgular");
    const note = addMsg("tool", "Sorgu çalışıyor…");
    showSql(sql, "çalışıyor…");
    const r = await api("/tools/run_query", { sql });
    if (!r.ok) {
      note.textContent = "Sorgu hata verdi, ajan düzeltiyor.";
      note.classList.add("err");
      showSql(sql, r.error);
      last = null;
      return JSON.stringify(r);
    }
    last = { columns: r.columns, rows: r.rows, sql: r.sql, chart: r.chart };
    note.textContent = `Sorgu tamam: ${r.row_count} satır`;
    showSql(r.sql, `${r.row_count} satır${r.truncated ? " (kesildi)" : ""} · ${r.elapsed_ms} ms`);
    renderTable();
    const { chart, ...forLlm } = r;
    if (r.rows.length > LLM_ROW_LIMIT) {
      forLlm.rows = r.rows.slice(0, LLM_ROW_LIMIT);
      forLlm.note = `Ekranda ${r.rows.length} satırın tamamı var; burada ilk ${LLM_ROW_LIMIT} satır gösterildi.`;
    }
    forLlm.suggested_chart = chart.type;
    return JSON.stringify(forLlm);
  },

  async analyze_trend(params) {
    setStage("tool", "Trend · değişimi açıklar");
    const note = addMsg("tool", "Trend analizi yapılıyor…");
    const r = await api("/tools/analyze_trend", params);
    if (!r.ok) {
      note.textContent = "Trend analizi hata verdi.";
      note.classList.add("err");
      last = null;
      return JSON.stringify(r);
    }
    note.textContent = "Trend analizi tamam";
    // En belirgin boyutun kırılımını grafiğe hazırla
    const boyut = r.en_belirgin_kirilim?.boyut;
    if (!boyut) {
      last = null;
      showSql(`analyze_trend(${r.metrik})`, `genel değişim %${fmt(r.degisim_yuzde)} · belirgin kırılım yok`);
      return JSON.stringify(r);
    }
    const rows = (r.kirilimlar[boyut] || []).map((k) => [k.grup, k.donem_a, k.donem_b, k.degisim_yuzde]);
    const a = `${r.donem_a.baslangic} – ${r.donem_a.bitis}`;
    const b = `${r.donem_b.baslangic} – ${r.donem_b.bitis}`;
    last = {
      columns: [boyut, `Önce (${a})`, `Sonra (${b})`, "Değişim %"],
      rows,
      sql: `analyze_trend(${r.metrik}: ${a} → ${b}, boyut: ${params.dimension || "hepsi"})`,
      chart: { type: "bar", x: boyut, y: [`Önce (${a})`, `Sonra (${b})`] },
      title: `${r.metrik_etiketi}: ${boyut} kırılımı`,
    };
    showSql(last.sql, `genel değişim %${fmt(r.degisim_yuzde)}`);
    renderTable();
    return JSON.stringify(r);
  },

  show_chart({ title, chart_type = "auto", x, y }) {
    if (!last) return "Gösterilecek sonuç yok. Önce run_query çağır.";
    const spec = { ...last.chart };
    if (chart_type && chart_type !== "auto") spec.type = chart_type;
    if (x && last.columns.includes(x)) spec.x = x;
    if (y) {
      const ys = (Array.isArray(y) ? y : String(y).split(",")).map((s) => s.trim()).filter((c) => last.columns.includes(c));
      if (ys.length) spec.y = ys;
    }
    addResult({ data: last, spec, title: title || last.title || "Sonuç", sql: ui.sqlBox.textContent, meta: ui.sqlMeta.textContent });
    return "Grafik hazırlandı; kullanıcı sohbetteki 'Grafik hazır' butonuyla sağ panelde açabilir.";
  },
};

// ---------------------------------------------------------------- sonuç butonları ve sağ panel
// Her show_chart sohbete bir "Grafik hazır" butonu ekler; tıklayınca o sonuç sağ panelde açılır.
const results = [];
const KIND = { kpi: ["◎", "Sonuç hazır"], line: ["📈", "Grafik hazır"], bar: ["📊", "Grafik hazır"], table: ["▦", "Tablo hazır"] };

function addResult(snap) {
  results.push(snap);
  const [ico, label] = KIND[snap.spec.type] || KIND.bar;
  const btn = document.createElement("button");
  btn.className = "result-btn";
  btn.innerHTML = `<span class="ico">${ico}</span><span><b>${label}</b><small></small></span>`;
  btn.querySelector("small").textContent = `${snap.title} · görmek için tıkla`;
  btn.addEventListener("click", () => openResult(snap, btn));
  ui.transcript.append(btn);
  ui.transcript.parentElement.scrollTop = ui.transcript.parentElement.scrollHeight;
}

function openResult(snap, btn) {
  document.querySelectorAll(".result-btn.active").forEach((b) => b.classList.remove("active"));
  btn?.classList.add("active");
  document.body.classList.add("drawer-open");
  ui.drawer.setAttribute("aria-hidden", "false");
  last = snap.data;
  showSql(snap.sql, snap.meta);
  renderTable();
  // panel açılma animasyonu bitince çiz ki Plotly doğru genişliği alsın
  setTimeout(() => {
    try { renderChart(snap.spec, snap.title); } catch (e) { console.error(e); setView("table"); }
  }, 320);
}

function closeDrawer() {
  document.body.classList.remove("drawer-open");
  ui.drawer.setAttribute("aria-hidden", "true");
  document.querySelectorAll(".result-btn.active").forEach((b) => b.classList.remove("active"));
}
ui.drawerClose.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// ---------------------------------------------------------------- ekranlar
function showScreen(name) {
  document.body.dataset.screen = name;
  ui.landing.hidden = name !== "landing";
  ui.chat.hidden = name !== "chat";
  if (name === "landing") closeDrawer();
}

// ---------------------------------------------------------------- görselleştirme
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
const PALETTE = ["#7b93ff", "#ffa94d", "#69db7c", "#f783ac", "#3bc9db", "#b197fc", "#ffd43b", "#a9e34b"];

function renderChart(spec, title) {
  ui.chartTitle.textContent = title;
  setView("chart");
  const { columns, rows } = last;
  const col = (name) => columns.indexOf(name);
  ui.chart.innerHTML = "";

  if (spec.type === "kpi") {
    const ys = spec.y?.length ? spec.y : columns.filter((_, i) => typeof rows[0]?.[i] === "number");
    ui.chart.innerHTML = `<div class="kpi">${ys.map((c) =>
      `<div><strong>${esc(fmt(rows[0][col(c)]))}</strong><span>${esc(c)}</span></div>`).join("")}</div>`;
    return;
  }
  if (spec.type === "table" || !spec.x || !spec.y?.length || !window.Plotly) {
    setView("table");
    return;
  }

  let traces;
  if (spec.type === "line" && spec.series && col(spec.series) >= 0) {
    // Uzun formatı (ay, kategori, değer) her kategori için ayrı çizgiye çevir
    const groups = new Map();
    for (const r of rows) {
      const g = r[col(spec.series)];
      if (!groups.has(g)) groups.set(g, { x: [], y: [] });
      groups.get(g).x.push(r[col(spec.x)]);
      groups.get(g).y.push(r[col(spec.y[0])]);
    }
    traces = [...groups].map(([name, d], i) => ({ ...d, name, type: "scatter", mode: "lines", line: { color: PALETTE[i % 8], width: 2 } }));
  } else {
    traces = spec.y.map((c, i) => ({
      x: rows.map((r) => r[col(spec.x)]),
      y: rows.map((r) => r[col(c)]),
      name: c,
      type: spec.type === "line" ? "scatter" : "bar",
      mode: spec.type === "line" ? "lines+markers" : undefined,
      marker: { color: PALETTE[i % 8] },
      line: { color: PALETTE[i % 8], width: 2 },
    }));
  }

  const text = cssVar("--text"), grid = cssVar("--border");
  Plotly.newPlot(ui.chart, traces, {
    margin: { t: 10, r: 10, b: 60, l: 70 },
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: text, family: "system-ui, sans-serif" },
    xaxis: { gridcolor: grid, automargin: true, type: "category" },
    yaxis: { gridcolor: grid, separatethousands: true, automargin: true },
    separators: ",.",
    barmode: "group",
    showlegend: traces.length > 1,
    legend: { orientation: "h", y: -0.2 },
  }, { responsive: true, displayModeBar: false });
}

function renderTable() {
  if (!last) return;
  const head = last.columns.map((c) => `<th>${esc(c)}</th>`).join("");
  const body = last.rows.slice(0, 500).map((r) =>
    `<tr>${r.map((v) => `<td class="${typeof v === "number" ? "num" : ""}">${esc(fmt(v))}</td>`).join("")}</tr>`).join("");
  ui.table.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function setView(v) {
  view = v;
  ui.chart.hidden = v !== "chart";
  ui.table.hidden = v !== "table";
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === v));
  if (v === "chart" && window.Plotly && ui.chart.querySelector(".js-plotly-plot, .plot-container")) Plotly.Plots.resize(ui.chart);
}
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setView(t.dataset.view)));

// ---------------------------------------------------------------- oturum
let starting = null;
function start(textOnly) {
  starting ??= openSession(textOnly).finally(() => { starting = null; });
  return starting;
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
    onConnect: () => setStatus(textOnly ? "Bağlı (yazılı)" : "Bağlı (sesli)", "connected"),
    onDisconnect: () => {
      if (conversation !== conv && conversation !== null) return; // eski oturumun geç gelen kapanışı
      conversation = null; setVoiceUi(false); setStatus("Bağlı değil"); setStage("idle");
    },
    onError: (m) => { setStatus("Hata", "error"); addMsg("tool err", `Hata: ${m}`); },
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

function setVoiceUi(on) {
  voiceMode = on;
  ui.mic.classList.toggle("live", on);
  ui.mic.setAttribute("aria-label", on ? "Konuşmayı bitir" : "Sesli konuşmayı başlat");
  ui.micLabel.textContent = on ? "Konuşmayı bitir" : "Konuşmaya başla";
  setStage(on ? "listening" : "idle");
}

async function toggleVoice() {
  try {
    if (voiceMode) { await conversation?.endSession(); setVoiceUi(false); return; }
    if (conversation) await conversation.endSession(); // yazılı oturumu kapat, sesliye geç
    await navigator.mediaDevices.getUserMedia({ audio: true });
    await start(false);
    setVoiceUi(true);
  } catch (e) {
    setStatus("Hata", "error");
    addMsg("tool err", `Mikrofon veya bağlantı hatası: ${e.message || e}`);
  }
}

ui.mic.addEventListener("click", toggleVoice);
ui.startOrb.addEventListener("click", () => { showScreen("chat"); toggleVoice(); });
ui.startText.addEventListener("click", () => { showScreen("chat"); ui.input.focus(); });
ui.home.addEventListener("click", async () => {
  if (conversation) await conversation.endSession();
  setVoiceUi(false);
  showScreen("landing");
});

async function sendText(text) {
  if (!text.trim()) return;
  try {
    if (!conversation) await start(true);
    addMsg("user", text); // yazılan mesaj sesli modda da transkripte düşmez, kendimiz ekleriz
    conversation.sendUserMessage(text);
    setStage("thinking");
  } catch (e) {
    setStatus("Hata", "error");
    addMsg("tool err", `Bağlantı hatası: ${e.message || e}`);
  }
}

ui.form.addEventListener("submit", (e) => {
  e.preventDefault();
  sendText(ui.input.value);
  ui.input.value = "";
});
ui.transcript.addEventListener("click", (e) => {
  if (e.target.classList.contains("chip")) sendText(e.target.textContent);
});

// ---------------------------------------------------------------- ses küresi
// Sesli modda yarıçap mikrofon (dinlerken) ya da ElevenLabs ses çıkışı (konuşurken) seviyesine göre değişir.
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
let level = 0;
function volume() {
  if (!conversation || !voiceMode) return 0;
  try {
    return stage === "speaking" ? conversation.getOutputVolume() : stage === "listening" ? conversation.getInputVolume() : 0;
  } catch { return 0; }
}
function drawOrb(t) {
  level += (Math.min(1, volume() * 2.2) - level) * 0.18;
  for (const cv of ui.orbs) if (cv.offsetParent) paintOrb(cv, t);
  requestAnimationFrame(drawOrb);
}
function paintOrb(cv, t) {
  const ctx = cv.getContext("2d");
  const W = cv.width, c = W / 2;
  ctx.clearRect(0, 0, W, W);
  const breathe = reduceMotion ? 0 : Math.sin(t / 900) * 0.02;
  const base = W * 0.30 * (1 + breathe + level * 0.22);
  const hue = { idle: 228, listening: 228, thinking: 262, tool: 262, speaking: 210 }[stage];

  // dış halkalar: konuşurken/dinlerken sesle büyür
  for (let i = 3; i >= 1; i--) {
    const r = base * (1 + i * (0.10 + level * 0.12));
    ctx.beginPath(); ctx.arc(c, c, r, 0, Math.PI * 2);
    ctx.fillStyle = `hsla(${hue}, 90%, 70%, ${0.05 + level * 0.06})`;
    ctx.fill();
  }
  // gövde
  const g = ctx.createRadialGradient(c - base * 0.35, c - base * 0.4, base * 0.1, c, c, base);
  g.addColorStop(0, `hsl(${hue + 20}, 95%, 82%)`);
  g.addColorStop(0.55, `hsl(${hue}, 85%, 64%)`);
  g.addColorStop(1, `hsl(${hue + 30}, 70%, 42%)`);
  ctx.beginPath(); ctx.arc(c, c, base, 0, Math.PI * 2); ctx.fillStyle = g; ctx.fill();

  // düşünürken dönen yay
  if (stage === "thinking" || stage === "tool") {
    const a = reduceMotion ? 0 : t / 380;
    ctx.beginPath(); ctx.arc(c, c, base * 1.16, a, a + Math.PI * 0.6);
    ctx.strokeStyle = `hsla(${hue}, 95%, 80%, .9)`; ctx.lineWidth = W * 0.02; ctx.lineCap = "round"; ctx.stroke();
  }
}
requestAnimationFrame(drawOrb);
setStage("idle");

// Test ve değerlendirme betikleri için
window.voicebi = { clientTools, sendText, get last() { return last; } };
