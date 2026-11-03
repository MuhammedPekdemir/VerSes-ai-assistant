from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import tools
from backend.app import app
from backend.charts import grafik_oner
from backend.sql_guard import SQLGuardError, calistir, dogrula

DB = Path(__file__).resolve().parent.parent / "data" / "voicebi.db"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="Önce python data/generate_data.py çalıştırın")


# --- SQL güvenliği ------------------------------------------------------------
@pytest.mark.parametrize("sql", [
    "DELETE FROM siparisler",
    "DROP TABLE urunler",
    "UPDATE urunler SET liste_fiyati_tl = 0",
    "INSERT INTO kategoriler VALUES (99, 'x')",
    "SELECT 1; DROP TABLE urunler",
    "PRAGMA table_info(urunler)",
    "ATTACH DATABASE '/tmp/x.db' AS x",
    "CREATE TABLE x (a INT)",
    "SELECT load_extension('x')",
    "",
])
def test_yazma_ve_tehlikeli_sorgular_reddedilir(sql):
    with pytest.raises(SQLGuardError):
        dogrula(sql)


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM siparisler",
    "WITH a AS (SELECT sehir FROM siparisler) SELECT sehir, COUNT(*) FROM a GROUP BY 1",
    "SELECT 1 UNION ALL SELECT 2",
    "select * from urunler;",
])
def test_okuma_sorgulari_gecer(sql):
    assert dogrula(sql)


def test_satir_limiti_uygulanir():
    r = calistir(DB, "SELECT * FROM siparis_kalemleri", max_rows=50)
    assert len(r.rows) == 50 and r.truncated


def test_zaman_asimi():
    yavas = "SELECT COUNT(*) FROM siparis_kalemleri a, siparis_kalemleri b"
    with pytest.raises(SQLGuardError, match="tamamlanamadı"):
        calistir(DB, yavas, timeout_s=0.2)


def test_hatali_sql_anlasilir_mesaj_doner():
    r = tools.run_query(DB, "SELECT olmayan_kolon FROM siparisler")
    assert r["ok"] is False and "olmayan_kolon" in r["error"]


# --- Araçlar ------------------------------------------------------------------
def test_get_schema():
    s = tools.get_schema(DB)
    adlar = {t["tablo"] for t in s["tablolar"]}
    assert adlar == {"kategoriler", "urunler", "musteriler", "siparisler", "siparis_kalemleri", "iadeler"}
    assert "Elektronik" in s["olasi_degerler"]["kategoriler.kategori_adi"]


def test_run_query_grafik_onerisi():
    r = tools.run_query(DB, "SELECT substr(siparis_tarihi,1,7) ay, COUNT(*) n FROM siparisler GROUP BY 1")
    assert r["ok"] and r["chart"]["type"] == "line"


# Gömülü olaylar: analyze_trend her birini doğru kırılımda bulmalı
def test_elektronik_dususunu_bulur():
    r = tools.analyze_trend(DB, "ciro", "2026-04-01", "2026-05-31", "2026-06-01", "2026-07-31")
    k = r["en_belirgin_kirilim"]
    assert (k["boyut"], k["grup"]) == ("kategori", "Elektronik") and k["degisim_yuzde"] < -30


def test_izmir_teslim_gecikmesini_bulur():
    r = tools.analyze_trend(DB, "ort_teslim_gun", "2026-02-01", "2026-02-28", "2026-03-01", "2026-03-31")
    k = r["en_belirgin_kirilim"]
    assert (k["boyut"], k["grup"]) == ("sehir", "İzmir") and k["donem_b"] > 5


def test_izmir_iade_artisini_bulur():
    r = tools.analyze_trend(DB, "iade_orani", "2026-02-01", "2026-02-28", "2026-03-01", "2026-03-31", dimension="sehir")
    assert r["kirilimlar"]["sehir"][0]["grup"] == "İzmir"


def test_black_friday_gunluk_artis():
    r = tools.analyze_trend(DB, "siparis_sayisi", "2025-11-01", "2025-11-23", "2025-11-24", "2025-11-30")
    assert r["donem_b"]["gunluk_ortalama"] > 2.5 * r["donem_a"]["gunluk_ortalama"]


def test_analyze_trend_gecersiz_girdi():
    assert tools.analyze_trend(DB, "kar", "2026-01-01", "2026-01-31", "2026-02-01", "2026-02-28")["ok"] is False
    assert tools.analyze_trend(DB, "ciro", "dun", "2026-01-31", "2026-02-01", "2026-02-28")["ok"] is False


# --- Grafik önerisi -------------------------------------------------------------
def test_grafik_turleri():
    assert grafik_oner(["toplam"], [[5]])["type"] == "kpi"
    assert grafik_oner(["kategori", "ciro"], [["A", 1.0], ["B", 2.0]])["type"] == "bar"
    assert grafik_oner(["ad", "soyad"], [["a", "b"]])["type"] == "table"


# --- API ------------------------------------------------------------------------
def test_api_uc_noktalari():
    c = TestClient(app)
    assert c.get("/health").json()["db"] is True
    assert c.get("/tools/get_schema").json()["ok"]
    r = c.post("/tools/run_query", json={"sql": "SELECT COUNT(*) AS n FROM musteriler"}).json()
    assert r["rows"] == [[5000]]
    r = c.post("/tools/run_query", json={"sql": "DELETE FROM musteriler"}).json()
    assert r["ok"] is False
    r = c.post("/tools/analyze_trend", json={"metric": "ciro", "donem_a_bas": "2026-04-01", "donem_a_bit": "2026-05-31",
                                             "donem_b_bas": "2026-06-01", "donem_b_bit": "2026-07-31", "dimension": "kategori"}).json()
    assert r["ok"]


def test_session_agent_id_doner(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    r = TestClient(app).get("/api/session").json()
    assert r["ok"] and r["agent_id"].startswith("agent_") and "signed_url" not in r


def test_sondaki_yorum_sarmalayiciyi_bozmaz():
    r = tools.run_query(DB, "SELECT COUNT(*) AS n FROM musteriler -- toplam müşteri")
    assert r["ok"] and r["rows"] == [[5000]]


def test_analyze_trend_ters_tarih_ve_veri_disi_donem():
    assert tools.analyze_trend(DB, "ciro", "2026-05-31", "2026-04-01", "2026-06-01", "2026-07-31")["ok"] is False
    r = tools.analyze_trend(DB, "ciro", "2030-01-01", "2030-01-31", "2030-02-01", "2030-02-28")
    assert r["ok"] and "veri yok" in r["uyari"]


def test_analyze_trend_tek_donem_veri_disi_cokmez():
    # B dönemi veri aralığının dışında: daha önce None / float bölmesinde TypeError veriyordu
    r = tools.analyze_trend(DB, "ciro", "2026-09-01", "2026-09-30", "2026-11-01", "2026-11-30")
    assert r["ok"] and "Dönem B" in r["uyari"] and r["degisim_yuzde"] is None
    r = tools.analyze_trend(DB, "iade_orani", "2024-11-01", "2024-11-30", "2026-03-01", "2026-03-31")
    assert r["ok"] and "Dönem A" in r["uyari"]


def test_env_okuyucu_bom_ve_tirnak(tmp_path, monkeypatch):
    from backend.app import _env_yukle
    monkeypatch.delenv("VERSES_TEST_A", raising=False)
    monkeypatch.delenv("VERSES_TEST_B", raising=False)
    f = tmp_path / ".env"
    f.write_bytes('\ufeffVERSES_TEST_A="tirnakli"\nexport VERSES_TEST_B=duz\n'.encode("utf-8"))
    _env_yukle(f)
    import os
    assert os.environ["VERSES_TEST_A"] == "tirnakli" and os.environ["VERSES_TEST_B"] == "duz"
