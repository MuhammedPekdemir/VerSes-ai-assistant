"""
VerSes FastAPI sunucusu.

ElevenLabs ajanının araçları tarayıcıda "client tool" olarak tanımlanır; tarayıcı bu
uç noktaları çağırır ve sonucu ajana iletir. Böylece lokal geliştirmede tünel gerekmez.

Çalıştırma:
    uvicorn backend.app:app --reload
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import tools

KOK = Path(__file__).resolve().parent.parent


def _env_yukle(yol: Path) -> None:
    """Basit .env okuyucu; zaten tanımlı ortam değişkenlerini ezmez."""
    if yol.exists():
        # utf-8-sig: Windows Not Defteri dosyanın başına BOM ekleyebilir, ilk anahtar bozulmasın
        for satir in yol.read_text(encoding="utf-8-sig").splitlines():
            satir = satir.strip()
            if satir.startswith("export "):
                satir = satir[len("export "):].strip()
            if satir and not satir.startswith("#") and "=" in satir:
                k, v = satir.split("=", 1)
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]  # KEY="deger" yazımında tırnakları at
                os.environ.setdefault(k.strip(), v)


_env_yukle(KOK / ".env")
DB_PATH = Path(os.getenv("VOICEBI_DB", KOK / "data" / "voicebi.db"))
MAX_ROWS = int(os.getenv("VOICEBI_MAX_ROWS", "500"))
TIMEOUT_S = float(os.getenv("VOICEBI_QUERY_TIMEOUT_S", "5"))

app = FastAPI(title="VerSes", description="Türkçe sesli veri analisti için veritabanı araçları.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("VOICEBI_CORS", "http://localhost:8000,http://127.0.0.1:8000,http://localhost:8765,http://127.0.0.1:8765").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class QueryIn(BaseModel):
    sql: str = Field(..., description="Tek bir SELECT veya WITH ... SELECT sorgusu (SQLite).")


class TrendIn(BaseModel):
    metric: str = Field(..., description="ciro, siparis_sayisi, satis_adedi, iade_orani, ort_teslim_gun, ort_sepet")
    donem_a_bas: str = Field(..., description="Önceki dönemin başlangıcı, YYYY-MM-DD")
    donem_a_bit: str = Field(..., description="Önceki dönemin bitişi, YYYY-MM-DD")
    donem_b_bas: str = Field(..., description="Sonraki dönemin başlangıcı, YYYY-MM-DD")
    donem_b_bit: str = Field(..., description="Sonraki dönemin bitişi, YYYY-MM-DD")
    dimension: str | None = Field(None, description="kategori, sehir, odeme_turu, kargo_firmasi, marka; boşsa hepsi")


@app.get("/health")
def health():
    return {"ok": True, "db": DB_PATH.exists()}


def _agent_id() -> str | None:
    if os.getenv("ELEVENLABS_AGENT_ID"):
        return os.environ["ELEVENLABS_AGENT_ID"]
    cfg = KOK / "agent" / "config.json"
    return json.loads(cfg.read_text(encoding="utf-8")).get("agent_id") if cfg.exists() else None


@app.get("/api/session")
def session():
    """
    Ön yüzün ajana bağlanması için gereken bilgi. API anahtarı varsa tarayıcıya anahtar yerine
    kısa ömürlü imzalı URL verilir; yoksa herkese açık ajan için yalnızca agent_id döner.
    """
    agent_id = _agent_id()
    anahtar = os.getenv("ELEVENLABS_API_KEY")
    if not agent_id:
        return {"ok": False, "error": "ELEVENLABS_AGENT_ID tanımlı değil."}
    if not anahtar:
        return {"ok": True, "agent_id": agent_id}
    url = "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?" + urllib.parse.urlencode({"agent_id": agent_id})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"xi-api-key": anahtar}), timeout=10) as r:
            return {"ok": True, "agent_id": agent_id, "signed_url": json.loads(r.read())["signed_url"]}
    except Exception as e:  # imzalı URL alınamazsa herkese açık bağlantıya düş
        return {"ok": True, "agent_id": agent_id, "warning": f"İmzalı URL alınamadı: {e}"}


@app.get("/tools/get_schema")
def get_schema():
    return tools.get_schema(DB_PATH)


@app.post("/tools/run_query")
def run_query(body: QueryIn):
    return tools.run_query(DB_PATH, body.sql, max_rows=MAX_ROWS, timeout_s=TIMEOUT_S)


@app.post("/tools/analyze_trend")
def analyze_trend(body: TrendIn):
    return tools.analyze_trend(DB_PATH, **body.model_dump())


# Ön yüz aynı sunucudan servis edilir (aynı köken: tarayıcı araç çağrılarında CORS gerekmez)
_FRONTEND = KOK / "frontend"
if _FRONTEND.exists():
    app.mount("/", StaticFiles(directory=_FRONTEND, html=True), name="frontend")
