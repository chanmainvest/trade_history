"""Inspect mangled symbols in the instruments table."""
from ledger.db import sqlite as s

with s.session() as c:
    rows = c.execute(
        "SELECT symbol, asset_type, currency FROM instruments "
        "WHERE asset_type IN ('equity','etf') "
        "AND (symbol LIKE '%\\_%' ESCAPE '\\' OR length(symbol) > 8) "
        "ORDER BY symbol LIMIT 80"
    ).fetchall()
    for r in rows:
        print(repr(r["symbol"]), "|", r["asset_type"], "|", r["currency"])

    print("\n--- count by length bucket ---")
    rows2 = c.execute(
        "SELECT CASE WHEN length(symbol) <= 6 THEN 'short' "
        "            WHEN length(symbol) <= 12 THEN 'mid' "
        "            ELSE 'long' END AS bucket, COUNT(*) AS n "
        "FROM instruments WHERE asset_type IN ('equity','etf') "
        "GROUP BY bucket"
    ).fetchall()
    for r in rows2:
        print(r["bucket"], r["n"])
