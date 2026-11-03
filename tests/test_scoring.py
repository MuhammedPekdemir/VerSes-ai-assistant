from eval.scoring import metin_dogru_mu, puanla, sayilari_bul


def test_sayilari_bul():
    assert 10_009_484 in sayilari_bul("Ekim cirosu 10.009.484 TL.")
    assert 2_400_000 in sayilari_bul("Yaklaşık 2,4 milyon lira.")
    assert 68 in sayilari_bul("Siparişlerin yüzde 68'i kredi kartıyla.")
    assert 5000 in sayilari_bul("Toplam beş bin müşterimiz var.")
    assert 472 in sayilari_bul("Ankara'da dört yüz yetmiş iki müşteri var.")
    assert 1 not in sayilari_bul("En çok ciroyu bir kategori yapıyor.")


def test_puanla():
    assert puanla({"tur": "sayi", "tolerans": 0.05}, 10009483.95, "Yaklaşık 10 milyon lira.")
    assert not puanla({"tur": "sayi", "tolerans": 0.05}, 10009483.95, "Yaklaşık 12 milyon lira.")
    assert puanla({"tur": "yuzde", "tolerans_puan": 1.5}, 68.2, "Yüzde 68 civarında.")
    assert puanla({"tur": "ay"}, "2025-11", "En yoğun ay Kasım 2025'ti.")
    assert puanla({"tur": "sayi", "tolerans": 0.1}, 2.118, "Ortalama 2,1 gün sürüyor.")


def test_metin():
    assert metin_dogru_mu("En çok Hızlı Kargo kullanılıyor.", "HızlıKargo")
    assert metin_dogru_mu("En çok İSTANBUL'dan sipariş geliyor.", "İstanbul")
    assert metin_dogru_mu("En çok satan Urbanist Basic Tişört.", "Urbanist Basic Tişört X346")
    assert not metin_dogru_mu("En çok Ankara'dan.", "İstanbul")


def test_virgullu_yazili_sayilar():
    from eval.scoring import sayilari_bul
    assert sayilari_bul("yaklaşık otuz bir virgül sekiz milyon lira") == [31_800_000.0]
    assert sayilari_bul("yüzde on iki virgül üç") == [12.3]
    assert sayilari_bul("ortalama beş virgül seksen dört gün") == [5.84]
