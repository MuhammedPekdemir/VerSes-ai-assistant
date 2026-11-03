"""
Ajanın kullandığı araçlar: get_schema, run_query, analyze_trend.

Her araç JSON'a çevrilebilir bir sözlük döner. Hata durumunda {"ok": False, "error": ...}
döner ki ajan hatayı okuyup sorgusunu düzeltebilsin.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .charts import grafik_oner
from .sql_guard import SQLGuardError, baglan, calistir

KOLON_NOTLARI = {
    "urunler.liste_fiyati_tl": "Ocak 2025 liste fiyatı. Satış fiyatı için siparis_kalemleri.birim_fiyat_tl kullanın.",
    "musteriler.kayit_tarihi": "YYYY-MM-DD metni.",
    "siparisler.siparis_tarihi": "YYYY-MM-DD metni. Ay için substr(siparis_tarihi, 1, 7) kullanın.",
    "siparisler.durum": "'Teslim Edildi', 'Kargoda' veya 'İptal Edildi'. Ciro hesaplarında iptalleri hariç tutun.",
    "siparisler.teslim_tarihi": "Teslim edilmediyse NULL. Teslim süresi: julianday(teslim_tarihi) - julianday(siparis_tarihi).",
    "siparisler.toplam_tutar_tl": "Kalemlerin indirimli toplamı + kargo ücreti.",
    "siparis_kalemleri.birim_fiyat_tl": "O tarihteki satış fiyatı, indirim öncesi.",
    "siparis_kalemleri.indirim_orani": "0 ile 1 arası.",
    "siparis_kalemleri.tutar_tl": "adet * birim_fiyat_tl * (1 - indirim_orani). Kategori bazlı ciro için bunu kullanın.",
    "iadeler.iade_tutari_tl": "İade edilen kalemin tutar_tl değeri.",
}
ORNEK_DEGER_KOLONLARI = [
    ("kategoriler", "kategori_adi"), ("musteriler", "sehir"), ("siparisler", "durum"),
    ("siparisler", "odeme_turu"), ("siparisler", "kargo_firmasi"), ("iadeler", "iade_nedeni"),
]


def get_schema(db_path: Path) -> dict:
    con = baglan(db_path)
    try:
        tablolar = []
        for (ad,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY rowid"):
            kolonlar = []
            fk = {r[3]: f"{r[2]}.{r[4]}" for r in con.execute(f"PRAGMA foreign_key_list({ad})")}
            for _, kad, tip, notnull, _, pk in con.execute(f"PRAGMA table_info({ad})"):
                k = {"ad": kad, "tip": tip}
                if pk:
                    k["birincil_anahtar"] = True
                if kad in fk:
                    k["referans"] = fk[kad]
                if f"{ad}.{kad}" in KOLON_NOTLARI:
                    k["not"] = KOLON_NOTLARI[f"{ad}.{kad}"]
                kolonlar.append(k)
            satir = con.execute(f"SELECT COUNT(*) FROM {ad}").fetchone()[0]
            tablolar.append({"tablo": ad, "satir_sayisi": satir, "kolonlar": kolonlar})
        ornekler = {
            f"{t}.{k}": [r[0] for r in con.execute(f"SELECT DISTINCT {k} FROM {t} ORDER BY {k}")]
            for t, k in ORNEK_DEGER_KOLONLARI
        }
        ilk, son = con.execute("SELECT MIN(siparis_tarihi), MAX(siparis_tarihi) FROM siparisler").fetchone()
    finally:
        con.close()
    return {
        "ok": True,
        "veritabani": "SQLite (salt-okunur)",
        "tarih_araligi": {"ilk_siparis": ilk, "son_siparis": son},
        "para_birimi": "TL",
        "tablolar": tablolar,
        "olasi_degerler": ornekler,
        "ipuclari": [
            "Tarihler metin olarak tutulur; karşılaştırma için 'YYYY-MM-DD' kullanın.",
            "Ciro için iptal edilen siparişleri (durum = 'İptal Edildi') hariç tutun.",
            "İade oranı = iade edilen kalem sayısı / satılan kalem sayısı.",
        ],
    }


def run_query(db_path: Path, sql: str, max_rows: int = 500, timeout_s: float = 5.0) -> dict:
    try:
        r = calistir(db_path, sql, max_rows=max_rows, timeout_s=timeout_s)
    except SQLGuardError as e:
        return {"ok": False, "error": str(e), "sql": sql}
    return {
        "ok": True,
        "sql": r.sql,
        "columns": r.columns,
        "rows": r.rows,
        "row_count": len(r.rows),
        "truncated": r.truncated,
        "elapsed_ms": r.elapsed_ms,
        "chart": grafik_oner(r.columns, r.rows),
    }


# ---------------------------------------------------------------------------
# analyze_trend: iki dönemi karşılaştırır ve değişimin nereden geldiğini bulur.
# SQL burada sabit şablonlardan üretilir; kullanıcı girdisi yalnızca parametre olarak geçer.
# ---------------------------------------------------------------------------
METRIKLER = {
    "ciro": ("Ciro (TL)", "SUM(k.tutar_tl)"),
    "siparis_sayisi": ("Sipariş sayısı", "COUNT(DISTINCT s.siparis_id)"),
    "satis_adedi": ("Satış adedi", "SUM(k.adet)"),
    "iade_orani": ("İade oranı", "CAST(COUNT(i.iade_id) AS REAL) / COUNT(k.kalem_id)"),
    "ort_teslim_gun": ("Ortalama teslim süresi (gün)",
                       "AVG(julianday(s.teslim_tarihi) - julianday(s.siparis_tarihi))"),
    "ort_sepet": ("Ortalama sepet (TL)", "SUM(k.tutar_tl) / COUNT(DISTINCT s.siparis_id)"),
}
ORAN_METRIKLERI = {"iade_orani", "ort_teslim_gun", "ort_sepet"}
MIN_KALEM = 30
BOYUTLAR = {
    "kategori": "c.kategori_adi",
    "sehir": "s.sehir",
    "odeme_turu": "s.odeme_turu",
    "kargo_firmasi": "s.kargo_firmasi",
    "marka": "u.marka",
}
_FROM = """
FROM siparisler s
JOIN siparis_kalemleri k ON k.siparis_id = s.siparis_id
JOIN urunler u ON u.urun_id = k.urun_id
JOIN kategoriler c ON c.kategori_id = u.kategori_id
LEFT JOIN iadeler i ON i.kalem_id = k.kalem_id
WHERE s.durum != 'İptal Edildi' AND s.siparis_tarihi BETWEEN ? AND ?
"""


def _tarih(t: str) -> str:
    try:
        return date.fromisoformat(t).isoformat()
    except (TypeError, ValueError) as e:
        raise SQLGuardError(f"Geçersiz tarih: {t!r}. YYYY-MM-DD formatında olmalı.") from e


def _yuvarla(v):
    return None if v is None else round(float(v), 4)


def _donem(d: tuple[str, str], deger, metric: str) -> dict:
    gun = (date.fromisoformat(d[1]) - date.fromisoformat(d[0])).days + 1
    out = {"baslangic": d[0], "bitis": d[1], "gun_sayisi": gun, "deger": _yuvarla(deger)}
    if metric not in ORAN_METRIKLERI and deger is not None:
        out["gunluk_ortalama"] = _yuvarla(deger / gun)  # farklı uzunluktaki dönemleri karşılaştırmak için
    return out


def analyze_trend(db_path: Path, metric: str, donem_a_bas: str, donem_a_bit: str,
                  donem_b_bas: str, donem_b_bit: str, dimension: str | None = None, top_n: int = 5) -> dict:
    """
    Dönem A (önceki) ile dönem B'yi (sonraki) karşılaştırır.
    dimension verilmezse tüm boyutlara bakar ve en çok değişimi açıklayan kırılımları döner.
    """
    try:
        if metric not in METRIKLER:
            raise SQLGuardError(f"Bilinmeyen metrik: {metric!r}. Seçenekler: {', '.join(METRIKLER)}.")
        if dimension and dimension not in BOYUTLAR:
            raise SQLGuardError(f"Bilinmeyen boyut: {dimension!r}. Seçenekler: {', '.join(BOYUTLAR)}.")
        a = (_tarih(donem_a_bas), _tarih(donem_a_bit))
        b = (_tarih(donem_b_bas), _tarih(donem_b_bit))
        for ad, (bas, bit) in (("A", a), ("B", b)):
            if bas > bit:
                raise SQLGuardError(f"Dönem {ad} başlangıcı ({bas}) bitişinden ({bit}) sonra olamaz.")
    except SQLGuardError as e:
        return {"ok": False, "error": str(e)}

    etiket, ifade = METRIKLER[metric]
    con = baglan(db_path)
    try:
        def toplam(d):
            return con.execute(f"SELECT {ifade} {_FROM}", d).fetchone()[0]

        def kirilim(boyut_ifade, d):
            q = f"SELECT {boyut_ifade} AS grup, {ifade} AS deger, COUNT(k.kalem_id) AS n {_FROM} GROUP BY 1"
            return {g: (v, n) for g, v, n in con.execute(q, d)}

        ta, tb = toplam(a), toplam(b)
        # Dönemlerden birinde hiç satır yoksa SUM/AVG NULL döner; oranı hesaplama
        genel_oran = (tb / ta) if ta and tb is not None else None
        boyutlar = [dimension] if dimension else list(BOYUTLAR)
        kirilimlar = {}
        for boyut in boyutlar:
            da, db_ = kirilim(BOYUTLAR[boyut], a), kirilim(BOYUTLAR[boyut], b)
            satirlar = []
            for g in sorted(set(da) | set(db_)):
                va, na = da.get(g, (0, 0))
                vb, nb = db_.get(g, (0, 0))
                va, vb = va or 0, vb or 0
                if metric in ORAN_METRIKLERI:
                    if min(na, nb) < MIN_KALEM:
                        continue  # az örnekli gruplarda oranlar gürültülü
                beklenen = va * genel_oran if genel_oran is not None else va
                satirlar.append({
                    "grup": g,
                    "donem_a": _yuvarla(va),
                    "donem_b": _yuvarla(vb),
                    "fark": _yuvarla(vb - va),
                    "degisim_yuzde": _yuvarla((vb - va) / va * 100) if va else None,
                    # Genel eğilimden sapma: grup, toplamla aynı oranda değişseydi beklenen değere göre fark
                    "genel_egilimden_sapma": _yuvarla(vb - beklenen),
                })
            if metric in ORAN_METRIKLERI:
                satirlar.sort(key=lambda r: abs((r["donem_b"] - r["donem_a"] * genel_oran) / r["donem_a"]) if r["donem_a"] and genel_oran else 0, reverse=True)
            else:
                satirlar.sort(key=lambda r: abs(r["genel_egilimden_sapma"] or 0), reverse=True)
            kirilimlar[boyut] = satirlar[:top_n]
    finally:
        con.close()

    ozet = None
    adaylar = [(bo, rows[0]) for bo, rows in kirilimlar.items() if rows]
    if adaylar:
        if metric in ORAN_METRIKLERI:
            puan = lambda x: abs((x[1]["donem_b"] - x[1]["donem_a"] * genel_oran) / x[1]["donem_a"]) if x[1]["donem_a"] and genel_oran else 0
        else:
            # boyutlar arası karşılaştırmada sapmayı dönem A toplamına göre normalize etmeye gerek yok: aynı toplamın parçaları
            puan = lambda x: abs(x[1]["genel_egilimden_sapma"] or 0)
        bo, r = max(adaylar, key=puan)
        ozet = {"boyut": bo, **r}

    sonuc = {
        "ok": True,
        "metrik": metric,
        "metrik_etiketi": etiket,
        "donem_a": _donem(a, ta, metric),
        "donem_b": _donem(b, tb, metric),
        "degisim_yuzde": _yuvarla((tb - ta) / ta * 100) if ta and tb is not None else None,
        "en_belirgin_kirilim": ozet,
        "kirilimlar": kirilimlar,
    }
    bos = [ad for ad, deger in (("A", ta), ("B", tb)) if deger is None]
    if bos:
        sonuc["uyari"] = (f"Dönem {' ve '.join(bos)} için veri yok. "
                          "Veri seti Ocak 2025 ile Ekim 2026 arasını kapsar.")
    return sonuc
