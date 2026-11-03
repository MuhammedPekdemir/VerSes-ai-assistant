# VerSes

Türkçe konuşarak bir e-ticaret veritabanına soru sorduğunuz sesli veri analisti. ElevenLabs ajanı soruyu anlıyor, veritabanı şemasına bakıp SQL yazıyor, sorguyu güvenli bir katmandan çalıştırıyor ve cevabı sesli anlatırken ekranda grafik çiziyor.

> Durum: veri seti, backend, ElevenLabs ajanı ve web arayüzü hazır. Değerlendirme ve demo videosu sırada.

## Mimari

```
Tarayıcı (mikrofon + grafik)  <->  ElevenLabs Agent (ses + LLM)
        |
        |  client tools (tarayıcı lokal API'yi çağırır, tünel gerekmez)
        v
FastAPI: get_schema / run_query / analyze_trend  ->  SQLite (salt-okunur)
```

## Kurulum

```bash
pip install -r requirements.txt
python data/generate_data.py          # data/voicebi.db üretir (seed 42, ~5 sn)
cp .env.example .env                  # ajan kimliği hazır; API anahtarı isteğe bağlı
uvicorn backend.app:app --reload      # arayüz: http://localhost:8000, API: /docs
pytest -q
```

## Arayüz

Tek sayfa (`frontend/`): solda mikrofon, konuşma dökümü ve yazılı soru kutusu; sağda Plotly grafiği (Grafik / Tablo); altta ajanın çalıştırdığı SQL. Ajanın araçları tarayıcıda client tool olarak tanımlı: `get_schema`, `run_query`, `analyze_trend` FastAPI'yi çağırır, `show_chart` son sonucu çizer.

## Araçlar

| Uç nokta | Ne yapar |
|---|---|
| `GET /tools/get_schema` | Tablolar, kolonlar, ilişkiler, kolon notları ve kategorik kolonların olası değerleri |
| `POST /tools/run_query` | Tek bir SELECT sorgusunu çalıştırır, sonucu ve grafik önerisini döner |
| `POST /tools/analyze_trend` | İki dönemi karşılaştırır, değişimi en çok açıklayan kırılımı (kategori, şehir, ödeme, kargo, marka) bulur |

### SQL güvenliği

1. **Statik kontrol (sqlglot):** tek ifade, yalnızca `SELECT` / `WITH`, yazma, `PRAGMA`, `ATTACH` ve tehlikeli fonksiyonlar reddedilir.
2. **Salt-okunur bağlantı:** SQLite `mode=ro` + `query_only` + okuma dışındaki her işlemi reddeden authorizer.
3. **Kaynak sınırı:** en fazla 500 satır, 5 saniye zaman aşımı.

Hatalı bir sorguda araç `{"ok": false, "error": "..."}` döner; ajan hatayı okuyup sorgusunu düzeltir.

## Veri seti

Ocak 2025 - Ekim 2026 arası sentetik Türk e-ticaret verisi: 30 bin sipariş, 5 bin müşteri, 300 ürün, 8 kategori, 20 il. İçine değerlendirme için üç olay gömülü (Black Friday, İzmir kargo krizi, Elektronik düşüşü). Ayrıntılar: [data/VERI_SETI.md](data/VERI_SETI.md).
