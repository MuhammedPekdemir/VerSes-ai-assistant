"""
SQL güvenlik katmanı.

Ajanın yazdığı SQL üç katmanda korunur:
  1. Statik kontrol (sqlglot): tek ifade, yalnızca SELECT / WITH, yasaklı düğüm yok.
  2. Bağlantı: SQLite salt-okunur modda açılır, ayrıca bir authorizer
     okuma dışındaki her işlemi reddeder.
  3. Kaynak sınırı: sonuç satır limitiyle sarılır, sorgu zaman aşımına uğrarsa kesilir.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

YASAK_DUGUMLER = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create,
    exp.Alter, exp.Command, exp.Pragma, exp.Attach, exp.Detach, exp.Transaction,
    exp.Commit, exp.Rollback, exp.Set, exp.Use,
)
YASAK_FONKSIYONLAR = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


class SQLGuardError(ValueError):
    """Sorgu güvenlik kontrolünden geçemedi. Mesaj ajana geri gönderilir."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    truncated: bool
    elapsed_ms: int
    sql: str


def dogrula(sql: str) -> str:
    """Sorguyu doğrular ve normalize edilmiş halini döner. Geçersizse SQLGuardError fırlatır."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise SQLGuardError("Boş sorgu gönderildi.")
    try:
        ifadeler = [e for e in sqlglot.parse(sql, read="sqlite") if e is not None]
    except ParseError as e:
        raise SQLGuardError(f"SQL ayrıştırılamadı: {str(e).splitlines()[0]}") from e
    if len(ifadeler) != 1:
        raise SQLGuardError("Tek seferde yalnızca bir SQL ifadesi çalıştırılabilir.")
    agac = ifadeler[0]
    if not isinstance(agac, exp.Query):
        raise SQLGuardError("Yalnızca SELECT (veya WITH ... SELECT) sorgularına izin veriliyor.")
    for dugum in agac.walk():
        if isinstance(dugum, YASAK_DUGUMLER):
            raise SQLGuardError(f"Sorguda izin verilmeyen bir işlem var: {type(dugum).__name__}.")
        if isinstance(dugum, exp.Func):
            ad = (dugum.name or "").lower() if isinstance(dugum, exp.Anonymous) else dugum.sql_name().lower()
            if ad in YASAK_FONKSIYONLAR:
                raise SQLGuardError(f"'{ad}' fonksiyonu kullanılamaz.")
    return sql


def _authorizer(action, arg1, arg2, dbname, source):
    izinli = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
    # SQLite, CTE ve alt sorgular için de SQLITE_SELECT/READ üretir; geri kalan her şey reddedilir.
    if action in izinli:
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in YASAK_FONKSIYONLAR:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def baglan(db_path: Path) -> sqlite3.Connection:
    """Veritabanını salt-okunur açar."""
    if not Path(db_path).exists():
        raise FileNotFoundError(f"Veritabanı bulunamadı: {db_path}. Önce `python data/generate_data.py` çalıştırın.")
    # as_uri(): Windows yolundaki ters bölü, boşluk, "#" ve Türkçe karakterler URI içinde doğru kodlanır
    con = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
    con.execute("PRAGMA query_only = ON")
    return con


def calistir(db_path: Path, sql: str, max_rows: int = 500, timeout_s: float = 5.0) -> QueryResult:
    temiz = dogrula(sql)
    # Satır sonları önemli: sorgu "-- yorum" ile biterse sarmalayıcının kapanışını yutmasın
    sarili = f"SELECT * FROM (\n{temiz}\n) AS _sonuc LIMIT {int(max_rows) + 1}"
    con = baglan(db_path)
    con.set_authorizer(_authorizer)
    bitis = time.monotonic() + timeout_s
    con.set_progress_handler(lambda: 1 if time.monotonic() > bitis else 0, 10_000)
    t0 = time.monotonic()
    try:
        cur = con.execute(sarili)
        satirlar = cur.fetchall()
        kolonlar = [d[0] for d in cur.description]
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            raise SQLGuardError(f"Sorgu {timeout_s:g} saniyede tamamlanamadı. Daha dar bir tarih aralığı veya toplulaştırma deneyin.") from e
        if "not authorized" in str(e):
            raise SQLGuardError("Sorgu yalnızca okuma izni olan işlemler içerebilir.") from e
        raise SQLGuardError(f"SQL hatası: {e}") from e
    except sqlite3.DatabaseError as e:
        raise SQLGuardError(f"SQL hatası: {e}") from e
    finally:
        con.close()
    kesildi = len(satirlar) > max_rows
    return QueryResult(
        columns=kolonlar,
        rows=[list(r) for r in satirlar[:max_rows]],
        truncated=kesildi,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
        sql=temiz,
    )
