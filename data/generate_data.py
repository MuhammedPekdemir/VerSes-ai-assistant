"""
VerSes - Sentetik Türk e-ticaret veri seti üretici.

Ocak 2025 - Ekim 2026 dönemini kapsayan, gerçekçi trendler ve bilerek
gömülmüş olaylar içeren bir SQLite veritabanı üretir.

Gömülü olaylar (değerlendirme setinde ajanın bulması beklenir):
  1. Black Friday (24-30 Kasım 2025): sipariş hacmi ~3 kat, ortalama indirim ~%25
  2. İzmir kargo krizi (Mart 2026): teslim süresi ~2 günden ~6 güne, iadeler artıyor
  3. Elektronik düşüşü (Haziran-Temmuz 2026): Elektronik satışları ~%40 düşük (tedarik sorunu)

Kullanım:
    python data/generate_data.py                  # data/voicebi.db üretir
    python data/generate_data.py --out x.db --orders 50000 --seed 7
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from faker import Faker

# ---------------------------------------------------------------------------
# Parametreler
# ---------------------------------------------------------------------------
START = date(2025, 1, 1)
END = date(2026, 10, 31)

YILLIK_BUYUME = 0.15        # sipariş hacminde yıllık büyüme
AYLIK_ENFLASYON = 0.025     # fiyatlarda aylık artış

BLACK_FRIDAY = (date(2025, 11, 24), date(2025, 11, 30))
BLACK_FRIDAY_CARPAN = 3.0
IZMIR_KRIZ = (date(2026, 3, 1), date(2026, 3, 31))
ELEKTRONIK_DUSUS = (date(2026, 6, 1), date(2026, 7, 31))
ELEKTRONIK_DUSUS_CARPAN = 0.60

# Bayram tarihleri (arife öncesi ~10 gün alışveriş artar)
BAYRAMLAR = [
    date(2025, 3, 30),  # Ramazan Bayramı 2025
    date(2025, 6, 6),   # Kurban Bayramı 2025
    date(2026, 3, 20),  # Ramazan Bayramı 2026
    date(2026, 5, 27),  # Kurban Bayramı 2026
]

AY_MEVSIMSELLIK = {1: 0.90, 2: 0.85, 3: 0.95, 4: 1.00, 5: 1.00, 6: 0.95,
                   7: 0.95, 8: 0.95, 9: 1.05, 10: 1.00, 11: 1.30, 12: 1.25}
GUN_CARPANI = [0.97, 0.97, 0.98, 0.98, 1.03, 1.05, 1.12]  # Pzt..Paz

# (il, ağırlık, normal teslim süresi ortalaması - gün)
SEHIRLER = [
    ("İstanbul", 30.0, 1.6), ("Ankara", 9.0, 1.9), ("İzmir", 7.0, 2.0),
    ("Bursa", 5.0, 1.9), ("Antalya", 4.5, 2.3), ("Kocaeli", 4.0, 1.7),
    ("Konya", 3.5, 2.4), ("Adana", 3.5, 2.6), ("Gaziantep", 3.0, 2.8),
    ("Mersin", 3.0, 2.6), ("Kayseri", 2.5, 2.5), ("Eskişehir", 2.5, 2.1),
    ("Samsun", 2.5, 2.6), ("Denizli", 2.0, 2.4), ("Sakarya", 2.0, 1.9),
    ("Tekirdağ", 2.0, 1.8), ("Diyarbakır", 2.0, 3.1), ("Trabzon", 1.8, 2.9),
    ("Muğla", 1.7, 2.6), ("Erzurum", 1.5, 3.3),
]

# kategori -> (baz ağırlık, iade oranı, ürün tipleri [(ad, min TL, max TL)], markalar)
KATEGORILER = {
    "Elektronik": (14, 0.05, [
        ("Kablosuz Kulaklık", 600, 4500), ("Akıllı Saat", 1500, 9000),
        ("Bluetooth Hoparlör", 700, 5000), ("Dizüstü Bilgisayar", 18000, 55000),
        ("Tablet", 6000, 25000), ("Powerbank", 400, 1500),
        ("Mekanik Klavye", 900, 4000), ("Oyuncu Mouse", 400, 2500),
        ("27 inç Monitör", 5000, 15000), ("Akıllı Telefon", 12000, 50000),
    ], ["Voltra", "Nexon", "Kuantum", "Pixelia", "Arcadia"]),
    "Moda": (22, 0.13, [
        ("Basic Tişört", 150, 450), ("Slim Fit Kot Pantolon", 450, 1300),
        ("Triko Kazak", 400, 1100), ("Spor Ayakkabı", 900, 3500),
        ("Kaşe Kaban", 1800, 5500), ("Elbise", 500, 2000),
        ("Deri Çanta", 700, 3000), ("Gömlek", 350, 1100),
    ], ["Mavera", "Lina", "Rüzgârgülü", "Urbanist", "Selvi"]),
    "Ev & Yaşam": (15, 0.06, [
        ("Nevresim Takımı", 500, 1800), ("Yemek Takımı", 900, 4000),
        ("Robot Süpürge", 6000, 22000), ("Kahve Makinesi", 1500, 9000),
        ("Masa Lambası", 300, 1200), ("Halı", 1200, 6000),
        ("Tencere Seti", 1000, 5000), ("Havlu Seti", 250, 900),
    ], ["Evim", "Lavanta Home", "Nordika", "Kalite Ev", "Asude"]),
    "Kozmetik": (13, 0.03, [
        ("Nemlendirici Krem", 150, 900), ("Parfüm", 600, 3500),
        ("Ruj", 120, 600), ("Şampuan", 80, 350), ("Güneş Kremi", 200, 800),
        ("Maskara", 150, 700), ("Serum", 250, 1300),
    ], ["Defne", "Rosalia", "Pure Skin", "Lumi", "Anka"]),
    "Spor & Outdoor": (9, 0.07, [
        ("Yoga Matı", 200, 800), ("Koşu Ayakkabısı", 1200, 4500),
        ("Dambıl Seti", 500, 2500), ("Kamp Çadırı", 1500, 7000),
        ("Bisiklet", 6000, 25000), ("Termos", 250, 900), ("Spor Çanta", 300, 1200),
    ], ["Zirve", "Atlas Sport", "Kondisyon", "Doruk", "Patika"]),
    "Kitap & Hobi": (8, 0.02, [
        ("Roman", 90, 350), ("Kişisel Gelişim Kitabı", 100, 350),
        ("Puzzle 1000 Parça", 200, 700), ("Kutu Oyunu", 400, 2000),
        ("Boya Seti", 150, 900), ("Okul Çantası", 400, 1500),
        ("Defter Seti", 80, 300),
    ], ["Lacivert Sayfa", "Sayfa", "Hobilan", "Kalem Kutusu", "Mozaik"]),
    "Anne & Bebek": (8, 0.04, [
        ("Bebek Bezi Paketi", 350, 900), ("Bebek Arabası", 4000, 20000),
        ("Mama Sandalyesi", 1500, 6000), ("Biberon Seti", 200, 900),
        ("Bebek Tulumu", 200, 700), ("Oto Koltuğu", 3000, 12000),
    ], ["Minik", "Bebeğim", "Pamuk", "Tontiş", "Kuzucuk"]),
    "Süpermarket": (11, 0.01, [
        ("Kahve 1 kg", 400, 1100), ("Zeytinyağı 2 L", 500, 1100),
        ("Deterjan 5 kg", 300, 800), ("Kuruyemiş Karışık", 200, 600),
        ("Çay 1 kg", 200, 450), ("Bulaşık Tableti 100'lü", 350, 900),
    ], ["Anadolu", "Bereket", "Tazelik", "Çiftlik", "Gurme"]),
}

N_URUN = 300
N_MUSTERI = 5000
KARGO_FIRMALARI = ["HızlıKargo", "Anadolu Lojistik", "Ekspres Kargo", "Marmara Kargo"]
ODEME_TURLERI = ["Kredi Kartı", "Banka Kartı", "Kapıda Ödeme", "Havale/EFT"]
ODEME_AGIRLIK = [0.68, 0.17, 0.10, 0.05]

IADE_NEDENLERI = ["Ürün beklentiyi karşılamadı", "Hasarlı ürün", "Yanlış ürün gönderildi",
                  "Vazgeçtim", "Beden/ölçü uymadı", "Geç teslimat"]


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------
def gunler():
    d = START
    while d <= END:
        yield d
        d += timedelta(days=1)


def icinde(d: date, aralik: tuple[date, date]) -> bool:
    return aralik[0] <= d <= aralik[1]


def ay_farki(d: date) -> int:
    return (d.year - START.year) * 12 + d.month - START.month


def fiyat_carpani(d: date) -> float:
    """Satıcılar fiyatı her ay başında günceller."""
    return (1 + AYLIK_ENFLASYON) ** ay_farki(d)


def kategori_agirliklari(d: date) -> dict[str, float]:
    w = {k: float(v[0]) for k, v in KATEGORILER.items()}
    if d.month in (5, 6, 7, 8):
        w["Spor & Outdoor"] *= 1.5
    if d.month in (11, 12, 1, 2):
        w["Moda"] *= 1.15  # kışlık
    if d.month == 9 or (d.month == 8 and d.day >= 20):
        w["Kitap & Hobi"] *= 2.0  # okula dönüş
    if d.month == 12:
        w["Ev & Yaşam"] *= 1.2
        w["Kitap & Hobi"] *= 1.3  # yılbaşı hediyesi
    for b in BAYRAMLAR:
        if 0 < (b - d).days <= 10:
            w["Moda"] *= 1.6
            w["Kozmetik"] *= 1.3
    if icinde(d, BLACK_FRIDAY):
        w["Elektronik"] *= 1.8
    if icinde(d, ELEKTRONIK_DUSUS):
        w["Elektronik"] *= ELEKTRONIK_DUSUS_CARPAN
    return w


BAZ_TOPLAM = sum(v[0] for v in KATEGORILER.values())


def gunluk_yogunluk(d: date) -> float:
    t = (d - START).days / 365.25
    lam = (1 + YILLIK_BUYUME) ** t
    lam *= AY_MEVSIMSELLIK[d.month] * GUN_CARPANI[d.weekday()]
    # kategori etkileri hacme de yansır (ör. Elektronik düşüşü toplam siparişi de düşürür)
    lam *= sum(kategori_agirliklari(d).values()) / BAZ_TOPLAM
    if icinde(d, BLACK_FRIDAY):
        lam *= BLACK_FRIDAY_CARPAN / (sum(kategori_agirliklari(d).values()) / BAZ_TOPLAM)
    if d.month == 12 and 20 <= d.day <= 31:
        lam *= 1.15
    return lam


# ---------------------------------------------------------------------------
# Üretim
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE kategoriler (
    kategori_id   INTEGER PRIMARY KEY,
    kategori_adi  TEXT NOT NULL
);
CREATE TABLE urunler (
    urun_id       INTEGER PRIMARY KEY,
    urun_adi      TEXT NOT NULL,
    kategori_id   INTEGER NOT NULL REFERENCES kategoriler(kategori_id),
    marka         TEXT NOT NULL,
    liste_fiyati_tl REAL NOT NULL          -- Ocak 2025 liste fiyatı; sonrası enflasyonla artar
);
CREATE TABLE musteriler (
    musteri_id    INTEGER PRIMARY KEY,
    ad            TEXT NOT NULL,
    soyad         TEXT NOT NULL,
    cinsiyet      TEXT NOT NULL,            -- 'Kadın' / 'Erkek'
    dogum_yili    INTEGER NOT NULL,
    sehir         TEXT NOT NULL,
    kayit_tarihi  TEXT NOT NULL             -- YYYY-MM-DD
);
CREATE TABLE siparisler (
    siparis_id     INTEGER PRIMARY KEY,
    musteri_id     INTEGER NOT NULL REFERENCES musteriler(musteri_id),
    siparis_tarihi TEXT NOT NULL,           -- YYYY-MM-DD
    sehir          TEXT NOT NULL,
    odeme_turu     TEXT NOT NULL,
    taksit_sayisi  INTEGER NOT NULL,
    kargo_firmasi  TEXT NOT NULL,
    durum          TEXT NOT NULL,           -- 'Teslim Edildi' / 'Kargoda' / 'İptal Edildi'
    teslim_tarihi  TEXT,                    -- teslim edilmediyse NULL
    kargo_ucreti_tl REAL NOT NULL,
    toplam_tutar_tl REAL NOT NULL           -- kalemler (indirim sonrası) + kargo
);
CREATE TABLE siparis_kalemleri (
    kalem_id       INTEGER PRIMARY KEY,
    siparis_id     INTEGER NOT NULL REFERENCES siparisler(siparis_id),
    urun_id        INTEGER NOT NULL REFERENCES urunler(urun_id),
    adet           INTEGER NOT NULL,
    birim_fiyat_tl REAL NOT NULL,           -- o tarihteki satış fiyatı (indirim öncesi)
    indirim_orani  REAL NOT NULL,           -- 0.00 - 1.00
    tutar_tl       REAL NOT NULL            -- adet * birim_fiyat * (1 - indirim)
);
CREATE TABLE iadeler (
    iade_id        INTEGER PRIMARY KEY,
    kalem_id       INTEGER NOT NULL REFERENCES siparis_kalemleri(kalem_id),
    siparis_id     INTEGER NOT NULL REFERENCES siparisler(siparis_id),
    iade_tarihi    TEXT NOT NULL,
    iade_nedeni    TEXT NOT NULL,
    iade_tutari_tl REAL NOT NULL
);
CREATE INDEX ix_siparis_tarih ON siparisler(siparis_tarihi);
CREATE INDEX ix_siparis_sehir ON siparisler(sehir);
CREATE INDEX ix_kalem_siparis ON siparis_kalemleri(siparis_id);
CREATE INDEX ix_kalem_urun ON siparis_kalemleri(urun_id);
CREATE INDEX ix_iade_siparis ON iadeler(siparis_id);
"""


def uret(out: Path, n_siparis: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    fake = Faker("tr_TR")
    Faker.seed(seed)

    kat_adlari = list(KATEGORILER)
    kat_id = {k: i + 1 for i, k in enumerate(kat_adlari)}

    # --- Ürünler -----------------------------------------------------------
    urunler = []  # (id, ad, kategori, marka, fiyat)
    kat_pay = np.array([KATEGORILER[k][0] for k in kat_adlari], dtype=float)
    urun_sayilari = np.maximum(15, np.round(kat_pay / kat_pay.sum() * N_URUN)).astype(int)
    urun_sayilari[np.argmax(urun_sayilari)] += N_URUN - urun_sayilari.sum()
    uid = 1
    for k, adet in zip(kat_adlari, urun_sayilari):
        _, _, tipler, markalar = KATEGORILER[k]
        for _ in range(adet):
            tip, lo, hi = tipler[rng.integers(len(tipler))]
            marka = markalar[rng.integers(len(markalar))]
            fiyat = float(np.exp(rng.uniform(np.log(lo), np.log(hi))))
            fiyat = round(fiyat / 10) * 10 - 0.01 if fiyat > 100 else round(fiyat, 2)
            model = f"{fake.random_uppercase_letter()}{rng.integers(100, 999)}"
            urunler.append((uid, f"{marka} {tip} {model}", k, marka, fiyat))
            uid += 1
    urun_by_kat = {k: [u for u in urunler if u[2] == k] for k in kat_adlari}
    # bazı ürünler daha popüler (Zipf benzeri)
    urun_pop = {k: rng.pareto(1.5, len(v)) + 0.2 for k, v in urun_by_kat.items()}
    urun_pop = {k: p / p.sum() for k, p in urun_pop.items()}

    # --- Müşteriler --------------------------------------------------------
    sehir_adlari = [s[0] for s in SEHIRLER]
    sehir_w = np.array([s[1] for s in SEHIRLER]); sehir_w /= sehir_w.sum()
    teslim_ort = {s[0]: s[2] for s in SEHIRLER}

    # %40'ı 2025 öncesi kayıtlı, kalanı dönem boyunca (büyüyerek) kayıt oluyor
    n_eski = int(N_MUSTERI * 0.4)
    eski = [date(2022, 1, 1) + timedelta(days=int(x)) for x in rng.integers(0, 1095, n_eski)]
    gun_sayisi = (END - START).days
    u = rng.random(N_MUSTERI - n_eski)
    yeni = [START + timedelta(days=int(gun_sayisi * x ** 0.85)) for x in u]
    kayitlar = sorted(eski + yeni)

    musteriler = []
    for i, kt in enumerate(kayitlar, start=1):
        cins = "Kadın" if rng.random() < 0.52 else "Erkek"
        ad = fake.first_name_female() if cins == "Kadın" else fake.first_name_male()
        musteriler.append((i, ad, fake.last_name(), cins, int(rng.integers(1962, 2007)),
                           sehir_adlari[rng.choice(len(sehir_adlari), p=sehir_w)], kt.isoformat()))
    kayit_arr = np.array([date.fromisoformat(m[6]).toordinal() for m in musteriler])
    aktivite = rng.lognormal(0, 1.0, N_MUSTERI)  # sadık / seyrek müşteriler
    aktivite_cum = np.cumsum(aktivite)

    # --- Günlük sipariş sayıları ------------------------------------------
    tum_gunler = list(gunler())
    lam = np.array([gunluk_yogunluk(d) for d in tum_gunler])
    lam *= n_siparis / lam.sum()
    gunluk_n = rng.poisson(lam)

    siparisler, kalemler, iadeler = [], [], []
    sid = kid = iid = 1
    for d, n in zip(tum_gunler, gunluk_n):
        kw = kategori_agirliklari(d)
        kp = np.array([kw[k] for k in kat_adlari], dtype=float); kp /= kp.sum()
        fc = fiyat_carpani(d)
        bf = icinde(d, BLACK_FRIDAY)
        uygun = int(np.searchsorted(kayit_arr, d.toordinal(), side="right"))
        for _ in range(n):
            # müşteri (kayıt tarihi siparişten önce olanlar arasından, aktiviteye göre)
            r = rng.random() * aktivite_cum[uygun - 1]
            m = musteriler[int(np.searchsorted(aktivite_cum[:uygun], r))]
            sehir = m[5]

            # kalemler
            n_kalem = rng.choice([1, 2, 3, 4], p=[0.60, 0.25, 0.10, 0.05])
            ana_kat = kat_adlari[rng.choice(len(kat_adlari), p=kp)]
            sip_kalemleri = []
            for j in range(n_kalem):
                k = ana_kat if (j == 0 or rng.random() < 0.5) else kat_adlari[rng.choice(len(kat_adlari), p=kp)]
                urun = urun_by_kat[k][rng.choice(len(urun_by_kat[k]), p=urun_pop[k])]
                adet = 1 if k not in ("Süpermarket", "Kozmetik") else int(rng.choice([1, 2, 3], p=[0.6, 0.3, 0.1]))
                fiyat = round(urun[4] * fc * rng.uniform(0.97, 1.03), 2)
                if bf:
                    ind = round(float(rng.uniform(0.15, 0.40)), 2)
                elif rng.random() < 0.25:
                    ind = round(float(rng.choice([0.05, 0.10, 0.15, 0.20])), 2)
                else:
                    ind = 0.0
                tutar = round(adet * fiyat * (1 - ind), 2)
                sip_kalemleri.append([kid, sid, urun[0], adet, fiyat, ind, tutar, k])
                kid += 1
            ara_toplam = sum(x[6] for x in sip_kalemleri)
            kargo = 0.0 if ara_toplam >= 400 * fc else round(49.99 * fc, 2)

            # ödeme
            odeme = ODEME_TURLERI[rng.choice(4, p=ODEME_AGIRLIK)]
            taksit = 1
            if odeme == "Kredi Kartı" and ara_toplam > 3000 * fc and rng.random() < 0.7:
                taksit = int(rng.choice([3, 6, 9, 12], p=[0.35, 0.35, 0.2, 0.1]))

            # kargo / teslim
            firma = KARGO_FIRMALARI[rng.choice(4, p=[0.35, 0.25, 0.25, 0.15])]
            ort = teslim_ort[sehir]
            if bf or (d.month == 12 and d.day >= 20):
                ort += 1.0  # yoğunluk gecikmesi
            if sehir == "İzmir" and icinde(d, IZMIR_KRIZ):
                ort = 6.0
            teslim_gun = max(1, int(round(rng.gamma(4.0, ort / 4.0))))
            teslim = d + timedelta(days=teslim_gun)

            if rng.random() < 0.02:
                durum, teslim_str = "İptal Edildi", None
            elif teslim > END:
                durum, teslim_str = "Kargoda", None
            else:
                durum, teslim_str = "Teslim Edildi", teslim.isoformat()

            siparisler.append((sid, m[0], d.isoformat(), sehir, odeme, taksit, firma, durum,
                               teslim_str, kargo, round(ara_toplam + kargo, 2)))

            # iadeler (gecikmeli teslimat iade olasılığını artırır)
            for kal in sip_kalemleri:
                kalemler.append(tuple(kal[:7]))
                if durum != "Teslim Edildi":
                    continue
                k = kal[7]
                p = KATEGORILER[k][1] * (1 + 0.6 * max(0, teslim_gun - 3))
                if bf:
                    p *= 1.3  # dürtüsel alım
                if rng.random() < p:
                    iade_t = teslim + timedelta(days=int(rng.integers(1, 15)))
                    if iade_t > END:
                        continue
                    if teslim_gun >= 5 and rng.random() < 0.6:
                        neden = "Geç teslimat"
                    else:
                        havuz = IADE_NEDENLERI[:5] if k == "Moda" else IADE_NEDENLERI[:4]
                        agirlik = [0.35, 0.15, 0.1, 0.15, 0.25] if k == "Moda" else [0.45, 0.2, 0.15, 0.2]
                        neden = havuz[rng.choice(len(havuz), p=agirlik)]
                    iadeler.append((iid, kal[0], sid, iade_t.isoformat(), neden, kal[6]))
                    iid += 1
            sid += 1

    # --- Yazma ---------------------------------------------------------------
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    con = sqlite3.connect(out)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO kategoriler VALUES (?,?)", [(kat_id[k], k) for k in kat_adlari])
    con.executemany("INSERT INTO urunler VALUES (?,?,?,?,?)",
                    [(u[0], u[1], kat_id[u[2]], u[3], u[4]) for u in urunler])
    con.executemany("INSERT INTO musteriler VALUES (?,?,?,?,?,?,?)", musteriler)
    con.executemany("INSERT INTO siparisler VALUES (?,?,?,?,?,?,?,?,?,?,?)", siparisler)
    con.executemany("INSERT INTO siparis_kalemleri VALUES (?,?,?,?,?,?,?)", kalemler)
    con.executemany("INSERT INTO iadeler VALUES (?,?,?,?,?,?)", iadeler)
    con.commit()
    con.close()
    print(f"{out}: {len(urunler)} ürün, {len(musteriler)} müşteri, {len(siparisler)} sipariş, "
          f"{len(kalemler)} kalem, {len(iadeler)} iade")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="VerSes sentetik veri üretici")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "voicebi.db")
    ap.add_argument("--orders", type=int, default=30000)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    uret(a.out, a.orders, a.seed)
