# VerSes Veri Seti

Sentetik bir Türk e-ticaret veri seti. Ocak 2025 ile Ekim 2026 arasını kapsar ve `generate_data.py` tarafından üretilir. Seed 42 sabit olduğu için her çalıştırmada aynı veri çıkar.

```bash
pip install -r requirements.txt
python generate_data.py                 # data/voicebi.db
python generate_data.py --orders 50000  # daha büyük versiyon
```

## Özet

| Tablo | Satır | İçerik |
|---|---|---|
| `kategoriler` | 8 | Elektronik, Moda, Ev & Yaşam, Kozmetik, Spor & Outdoor, Kitap & Hobi, Anne & Bebek, Süpermarket |
| `urunler` | 300 | Ürün adı, kategori, marka, Ocak 2025 liste fiyatı |
| `musteriler` | 5.000 | Ad, soyad, cinsiyet, doğum yılı, şehir, kayıt tarihi |
| `siparisler` | 30.090 | Tarih, şehir, ödeme türü, taksit, kargo firması, durum, teslim tarihi, toplam tutar |
| `siparis_kalemleri` | 48.206 | Ürün, adet, birim fiyat, indirim oranı, tutar |
| `iadeler` | 3.255 | İade edilen kalem, tarih, neden, tutar |

Tarihler `YYYY-MM-DD` formatında metin olarak tutulur, tutarların hepsi TL'dir. Marka ve kargo firması adları uydurmadır.

## Trendler

- **Büyüme:** Sipariş hacmi yıllık yaklaşık %15 büyür. Ocak-Ekim döneminde 2026, 2025'e göre %10,6 yukarıda; aradaki farkı Elektronik düşüşü yiyor.
- **Enflasyon:** Fiyatlar her ay yaklaşık %2,5 artar, ortalama sepet 4.150 TL'den 6.680 TL'ye çıkar. Bu nedenle ciro, sipariş sayısından daha hızlı büyür.
- **Mevsimsellik:** Kasım-aralık dönemi zirvedir. Bayram öncesi 10 günde Moda ve Kozmetik satışları artar. Eylülde okula dönüşle Kitap & Hobi, yaz aylarında Spor & Outdoor yükselir.
- **Şehir dağılımı:** Siparişlerin %34'ü İstanbul'dan gelir, onu Ankara ve İzmir izler. Doğu illerinde teslimat daha uzun sürer, genel ortalama 2,1 gündür.
- **Ödeme:** %68 kredi kartı, %17 banka kartı, %10 kapıda ödeme, %5 havale/EFT. 3.000 TL üzeri kredi kartı alışverişlerinde taksit yaygındır.

## Gömülü olaylar (değerlendirme için doğru cevaplar)

| # | Olay | Dönem | Verideki etkisi |
|---|---|---|---|
| 1 | Black Friday | 24-30 Kasım 2025 | Günde 159 sipariş (kasımın geri kalanında 56, yaklaşık 2,8 kat). Ortalama indirim %27 (normalde %3). Bu haftada 1.113 sipariş var. |
| 2 | İzmir kargo krizi | Mart 2026 | İzmir'de ortalama teslim süresi 6,3 gün (diğer aylarda yaklaşık 2). İzmir'de iade oranı %24,6 (normalde %4-8), iade nedenlerinde "Geç teslimat" öne çıkıyor. |
| 3 | Elektronik düşüşü | Haziran-Temmuz 2026 | Elektronik satış adedi geçen yılın aynı dönemine göre %39,5, nisan-mayısa göre %43,6 düşük. Toplam ciro mayıstan hazirana 8,4 milyon TL'den 7,6 milyon TL'ye iniyor. Ağustosta toparlanıyor. |

Ajana "Haziranda satışlar neden düştü?" diye sorulduğunda beklenen cevap: düşüş Elektronik kategorisinden geliyor.
