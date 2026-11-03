"""
VerSes değerlendirmesi: soruları ElevenLabs ajanına yazılı (text-only) olarak sorar,
ajanın araç çağrılarını yerelde çalıştırır ve cevabı veritabanındaki doğru cevapla karşılaştırır.

Çalıştırma (proje kökünden):
    pip install -r requirements.txt
    python -m eval.run_eval              # tüm sorular
    python -m eval.run_eval --ids 4 15   # belirli sorular

Çıktı: eval/results.json ve eval/SONUCLAR.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websockets

from backend import tools
from backend.app import DB_PATH, _agent_id  # .env'yi de yükler
from eval.scoring import puanla

KLASOR = Path(__file__).resolve().parent
SESSIZLIK_S = 8      # son cevaptan sonra bu kadar yeni olay gelmezse cevap tamam sayılır
SORU_ZAMAN_ASIMI_S = 120


def ws_url(agent_id: str) -> str:
    if os.getenv("VOICEBI_WS_URL"):  # test için sahte sunucu
        return os.environ["VOICEBI_WS_URL"]
    anahtar = os.getenv("ELEVENLABS_API_KEY")
    if anahtar:
        url = "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url?" + urllib.parse.urlencode({"agent_id": agent_id})
        with urllib.request.urlopen(urllib.request.Request(url, headers={"xi-api-key": anahtar}), timeout=15) as r:
            return json.loads(r.read())["signed_url"]
    return f"wss://api.elevenlabs.io/v1/convai/conversation?agent_id={agent_id}"


def araci_calistir(ad: str, p: dict) -> str:
    if ad == "get_schema":
        return json.dumps(tools.get_schema(DB_PATH), ensure_ascii=False)
    if ad == "run_query":
        r = tools.run_query(DB_PATH, p.get("sql", ""))
        r.pop("chart", None)
        if r.get("ok") and len(r["rows"]) > 100:  # arayüzle aynı: ajana ilk 100 satır
            r["rows"] = r["rows"][:100]
        return json.dumps(r, ensure_ascii=False)
    if ad == "analyze_trend":
        return json.dumps(tools.analyze_trend(DB_PATH, **p), ensure_ascii=False)
    if ad == "show_chart":
        return "Grafik ekranda gösteriliyor."
    return f"Bilinmeyen araç: {ad}"


async def sor(agent_id: str, soru: str) -> dict:
    cevaplar, araclar = [], []
    async with websockets.connect(ws_url(agent_id), max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "conversation_initiation_client_data",
            "conversation_config_override": {"conversation": {"text_only": True}},
        }))
        # Ajanın karşılama mesajını bekle, sonra soruyu gönder
        ilk_mesaj_alindi = False
        while not ilk_mesaj_alindi:
            olay = json.loads(await asyncio.wait_for(ws.recv(), 20))
            if olay["type"] == "ping":
                await ws.send(json.dumps({"type": "pong", "event_id": olay["ping_event"]["event_id"]}))
            ilk_mesaj_alindi = olay["type"] == "agent_response"

        t0 = time.monotonic()
        await ws.send(json.dumps({"type": "user_message", "text": soru}))
        son_cevap_t = None
        while time.monotonic() - t0 < SORU_ZAMAN_ASIMI_S:
            try:
                olay = json.loads(await asyncio.wait_for(ws.recv(), SESSIZLIK_S))
            except asyncio.TimeoutError:
                if cevaplar:
                    break
                continue
            tur = olay["type"]
            if tur == "ping":
                await ws.send(json.dumps({"type": "pong", "event_id": olay["ping_event"]["event_id"]}))
            elif tur == "client_tool_call":
                c = olay["client_tool_call"]
                sonuc = araci_calistir(c["tool_name"], c.get("parameters") or {})
                araclar.append({"arac": c["tool_name"], "parametreler": c.get("parameters"),
                                "hata": '"ok": false' in sonuc})
                await ws.send(json.dumps({"type": "client_tool_result", "tool_call_id": c["tool_call_id"],
                                          "result": sonuc, "is_error": False}))
            elif tur == "agent_response":
                cevaplar.append(re.sub(r"\[[a-z ]{2,20}\]\s*", "", olay["agent_response_event"]["agent_response"], flags=re.I))
                son_cevap_t = time.monotonic()
    return {
        "cevap": " ".join(cevaplar),
        "araclar": araclar,
        "sure_s": round((son_cevap_t or time.monotonic()) - t0, 1),
    }


def dogru_cevap(soru: dict):
    if "beklenen" in soru:
        return soru["beklenen"]
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as con:
        return con.execute(soru["sql"]).fetchone()[0]


def bicimle(v) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return str(v)


ARA_KAYIT = KLASOR / "results_partial.json"


async def main(ids: list[int] | None, devam: bool = False):
    agent_id = _agent_id()
    sorular = json.loads((KLASOR / "questions.json").read_text(encoding="utf-8"))
    if ids:
        sorular = [s for s in sorular if s["id"] in ids]

    # Her sorudan sonra ara kayıt: yarıda kesilirse --devam ile kalan sorulardan sürdürülür
    sonuclar = json.loads(ARA_KAYIT.read_text(encoding="utf-8")) if devam and ARA_KAYIT.exists() else []
    bitenler = {r["id"] for r in sonuclar}
    if bitenler:
        print(f"{len(bitenler)} soru daha önce tamamlanmış, kalanlarla devam ediliyor.\n")
    for s in sorular:
        if s["id"] in bitenler:
            continue
        beklenen = dogru_cevap(s)
        try:
            r = await sor(agent_id, s["soru"])
        except Exception as e:
            r = {"cevap": "", "araclar": [], "sure_s": None, "hata": f"{type(e).__name__}: {e}"}
        r.update(id=s["id"], soru=s["soru"], tur=s["tur"], beklenen=beklenen,
                 dogru=bool(r["cevap"]) and puanla(s, beklenen, r["cevap"]))
        sonuclar.append(r)
        ARA_KAYIT.write_text(json.dumps(sonuclar, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{'✓' if r['dogru'] else '✗'}] {s['id']:>2}. {s['soru']}\n     beklenen: {bicimle(beklenen)}\n     cevap:    {r['cevap'] or r.get('hata')}\n")
        await asyncio.sleep(1)

    sonuclar.sort(key=lambda r: r["id"])
    dogru = sum(r["dogru"] for r in sonuclar)
    sureler = sorted(r["sure_s"] for r in sonuclar if r["sure_s"] is not None)
    medyan = sureler[len(sureler) // 2] if sureler else None
    arac_hatasi = sum(any(a["hata"] for a in r["araclar"]) for r in sonuclar)
    ozet = {"soru_sayisi": len(sonuclar), "dogru": dogru, "dogruluk_yuzde": round(100 * dogru / max(len(sonuclar), 1), 1),
            "medyan_cevap_suresi_s": medyan, "sql_hatasi_alip_duzelten": arac_hatasi}
    (KLASOR / "results.json").write_text(json.dumps({"ozet": ozet, "sonuclar": sonuclar}, ensure_ascii=False, indent=2), encoding="utf-8")

    satirlar = [
        "# Değerlendirme sonuçları", "",
        f"**Doğruluk: {dogru}/{len(sonuclar)} (%{ozet['dogruluk_yuzde']})** · medyan cevap süresi: {medyan} sn (yazılı mod)", "",
        "| # | Soru | Beklenen | Ajanın cevabı | Sonuç |", "|---|---|---|---|---|",
    ]
    for r in sonuclar:
        cevap = (r["cevap"] or r.get("hata", "")).replace("|", "/").replace("\n", " ")
        satirlar.append(f"| {r['id']} | {r['soru']} | {bicimle(r['beklenen'])} | {cevap} | {'✓' if r['dogru'] else '✗'} |")
    (KLASOR / "SONUCLAR.md").write_text("\n".join(satirlar) + "\n", encoding="utf-8")
    print(f"Doğruluk: {dogru}/{len(sonuclar)} (%{ozet['dogruluk_yuzde']}) · medyan süre {medyan} sn")
    print("Ayrıntılar: eval/SONUCLAR.md, eval/results.json")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")  # Windows konsolunda Türkçe karakterler ve ✓
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", type=int, nargs="*", help="Yalnızca bu soru numaralarını çalıştır")
    ap.add_argument("--devam", action="store_true", help="Yarıda kalan çalıştırmayı kaldığı yerden sürdür")
    a = ap.parse_args()
    asyncio.run(main(a.ids, a.devam))
