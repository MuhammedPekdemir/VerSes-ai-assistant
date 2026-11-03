"""
Sorgu sonucuna göre grafik türü önerir. Çıktı, ön yüzün doğrudan Plotly'ye verebileceği
basit bir tanımdır: {"type": "line" | "bar" | "kpi" | "table", "x": kolon, "y": [kolonlar]}.
"""

from __future__ import annotations

import re

_TARIH = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def _sayisal(deger) -> bool:
    return isinstance(deger, (int, float)) and not isinstance(deger, bool)


def grafik_oner(columns: list[str], rows: list[list]) -> dict:
    if not rows or not columns:
        return {"type": "table"}
    if len(rows) == 1 and len(columns) <= 3 and any(_sayisal(v) for v in rows[0]):
        return {"type": "kpi", "y": [c for c, v in zip(columns, rows[0]) if _sayisal(v)]}

    ornek = rows[: min(len(rows), 50)]
    sayisal = [i for i in range(len(columns)) if all(_sayisal(r[i]) or r[i] is None for r in ornek)
               and any(_sayisal(r[i]) for r in ornek)]
    metin = [i for i in range(len(columns)) if i not in sayisal]
    if not sayisal or not metin:
        return {"type": "table"}

    x = metin[0]
    y = [columns[i] for i in sayisal[:3]]
    zaman = all(isinstance(r[x], str) and _TARIH.match(r[x]) for r in ornek)
    if zaman and len(rows) >= 3:
        # Zaman ekseninde ikinci bir metin kolonu varsa (ör. kategori) her biri ayrı çizgi olur
        seri = columns[metin[1]] if len(metin) > 1 else None
        return {"type": "line", "x": columns[x], "y": y, "series": seri}
    if len(metin) == 1 and len(rows) <= 30:
        return {"type": "bar", "x": columns[x], "y": y}
    return {"type": "table"}
