"""
Ajanın Türkçe cevabını doğru cevapla karşılaştırır.

Sesli ajan sayıları yuvarlayarak ve bazen yazıyla söyler ("yaklaşık 10 milyon lira",
"yüzde 68", "beş bin"). Bu modül metindeki sayıları çıkarır ve toleransla karşılaştırır.
"""

from __future__ import annotations

import re
import unicodedata

AYLAR = ["ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz",
         "ağustos", "eylül", "ekim", "kasım", "aralık"]

_BIRLER = {"sıfır": 0, "bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5, "altı": 6,
           "yedi": 7, "sekiz": 8, "dokuz": 9}
_ONLAR = {"on": 10, "yirmi": 20, "otuz": 30, "kırk": 40, "elli": 50, "altmış": 60,
          "yetmiş": 70, "seksen": 80, "doksan": 90}
_CARPANLAR = {"bin": 1e3, "milyon": 1e6, "milyar": 1e9}


def kucuk(s: str) -> str:
    """Türkçe kurallarıyla küçük harfe çevirir (İ -> i, I -> ı)."""
    return unicodedata.normalize("NFC", s.replace("İ", "i").replace("I", "ı")).lower()


def _tek_yazili(kelimeler: list[str]) -> float | None:
    """Kelime listesini (tümü sayı kelimesi olmalı) sayıya çevirir; değilse None."""
    toplam = grup = 0.0
    if not kelimeler:
        return None
    for k in kelimeler:
        if k in _BIRLER or k in _ONLAR:
            grup += _BIRLER.get(k, 0) + _ONLAR.get(k, 0)
        elif k == "yüz":
            grup = (grup or 1) * 100
        elif k == "bin":
            toplam += (grup or 1) * 1000
            grup = 0
        else:
            return None
    return toplam + grup


_SAYI_K = "|".join(sorted([*_BIRLER, *_ONLAR, "yüz"], key=len, reverse=True))
_VIRGUL_DESEN = rf"([a-zçğıöşü ]+?) virgül ((?:(?:{_SAYI_K})\b ?)+?)( milyar| milyon| bin)?(?=\W|$)"


def _virgul_cevir(m: re.Match) -> str:
    """'yüzde on iki virgül üç' içindeki 'on iki virgül üç' kısmını '12,3' yapar."""
    sol = m.group(1).split()
    # sol taraftaki en uzun sayı-kelimesi kuyruğunu bul
    i = len(sol)
    while i > 0 and (sol[i - 1] in _BIRLER or sol[i - 1] in _ONLAR or sol[i - 1] in ("yüz", "bin")):
        i -= 1
    tam, ondalik = _tek_yazili(sol[i:]), _tek_yazili(m.group(2).split())
    if tam is None or ondalik is None:
        return m.group(0)
    onek = " ".join(sol[:i])
    return f"{onek} {int(tam)},{int(ondalik)}{m.group(3) or ''} ".lstrip()


def _yazili_sayilar(metin: str) -> list[float]:
    """'beş bin iki yüz' gibi yazıyla söylenmiş sayıları bulur."""
    sonuc, toplam, grup, aktif, kelimeler = [], 0.0, 0.0, False, []

    def bitir():
        # tek başına "bir" çoğunlukla belirsizlik sıfatıdır ("bir kategori"), sayı sayma
        if aktif and kelimeler != ["bir"]:
            sonuc.append(toplam + grup)

    for kelime in re.findall(r"[a-zçğıöşü]+", metin):
        if kelime in _BIRLER or kelime in _ONLAR or kelime in _CARPANLAR or kelime == "yüz":
            kelimeler.append(kelime)
        if kelime in _BIRLER or kelime in _ONLAR:
            grup += _BIRLER.get(kelime, 0) + _ONLAR.get(kelime, 0)
            aktif = True
        elif kelime == "yüz":
            grup = (grup or 1) * 100
            aktif = True
        elif kelime in _CARPANLAR and aktif:
            toplam += (grup or 1) * _CARPANLAR[kelime]
            grup = 0
        elif kelime == "bin":  # "bin" tek başına = 1000
            grup, aktif = 1000.0, True
        else:
            bitir()
            toplam, grup, aktif, kelimeler = 0.0, 0.0, False, []
    bitir()
    return sonuc


def sayilari_bul(metin: str) -> list[float]:
    """Metindeki tüm sayıları döner: '10.009.484', '2,4 milyon', '%68', 'beş bin'."""
    m = kucuk(metin)
    # "otuz bir virgül sekiz milyon" -> "31,8 milyon": virgüllü yazılı sayıyı rakama çevir
    m = re.sub(_VIRGUL_DESEN, _virgul_cevir, m)
    sayilar = []
    desen = r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?|\d+(?:\.\d+)?)\s*(milyar|milyon|bin)?"
    for sayi, carpan in re.findall(desen, m):
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", sayi):
            deger = float(sayi.replace(".", "").replace(",", "."))  # Türkçe binlik ayraç
        else:
            deger = float(sayi.replace(",", "."))
        sayilar.append(deger * _CARPANLAR.get(carpan, 1))
    return sayilar + _yazili_sayilar(m)


def sayi_dogru_mu(cevap: str, beklenen: float, tolerans: float) -> bool:
    """Cevaptaki herhangi bir sayı beklenen değere göreli tolerans içinde mi?"""
    return any(abs(s - beklenen) <= tolerans * abs(beklenen) for s in sayilari_bul(cevap))


def yuzde_dogru_mu(cevap: str, beklenen: float, tolerans_puan: float) -> bool:
    return any(abs(s - beklenen) <= tolerans_puan for s in sayilari_bul(cevap))


def _sade(s: str) -> str:
    return re.sub(r"[^a-z0-9çğıöşü]", "", kucuk(s))


def metin_dogru_mu(cevap: str, beklenen: str) -> bool:
    """
    Beklenen ifade cevapta geçiyor mu? Boşluk ve noktalama yok sayılır ("Hızlı Kargo" = "HızlıKargo").
    Ürün adı gibi uzun ifadelerde kelimelerin üçte ikisinin geçmesi yeterli (ajan kodu söylemeyebilir).
    """
    if _sade(beklenen) in _sade(cevap):
        return True
    kelimeler = [k for k in re.findall(r"[a-z0-9çğıöşü]+", kucuk(beklenen)) if len(k) > 2]
    if len(kelimeler) >= 3:
        cevap_k = kucuk(cevap)
        return sum(k in cevap_k for k in kelimeler) >= 2 * len(kelimeler) / 3
    return False


def ay_dogru_mu(cevap: str, beklenen: str) -> bool:
    """beklenen 'YYYY-MM'; cevapta 'Kasım 2025' veya '2025-11' geçmeli."""
    yil, ay = beklenen.split("-")
    m = kucuk(cevap)
    return beklenen in m or (AYLAR[int(ay) - 1] in m and yil in m)


def puanla(soru: dict, beklenen, cevap: str) -> bool:
    tur = soru["tur"]
    if tur == "sayi":
        return sayi_dogru_mu(cevap, float(beklenen), soru.get("tolerans", 0.05))
    if tur == "yuzde":
        return yuzde_dogru_mu(cevap, float(beklenen), soru.get("tolerans_puan", 1.0))
    if tur == "ay":
        return ay_dogru_mu(cevap, str(beklenen))
    return metin_dogru_mu(cevap, str(beklenen))
