"""GET /monthly — point-in-time consolidated holdings + diff.

Implementation notes (see AGENTS.md item 7):

For any given ``as_of`` date we currently report the latest
``position_snapshots`` row per (account, instrument) whose ``as_of_date``
is ``<= as_of``. This is *carry-forward from the last statement*, NOT a
true day-by-day reconstruction from transactions.

A full transactions-based reconstruction is queued as a follow-up; this
endpoint is the existing checkpoint-based view, extended with portfolio
filtering and account_ids support.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from ...db import sqlite as sqlite_db

router = APIRouter(prefix="/monthly", tags=["monthly"])


def _csv_ints(v: str | None) -> list[int]:
    if not v:
        return []
    return [int(x) for x in v.split(",") if x.strip().lstrip("-").isdigit()]


def _holdings_at(as_of: str, account_ids: list[int]) -> list[dict]:
    base_sql = """
        WITH latest AS (
          SELECT account_id, instrument_id, MAX(as_of_date) AS d
            FROM position_snapshots
           WHERE as_of_date <= ?
        {acct_clause}
        GROUP BY account_id, instrument_id
        )
        SELECT ps.as_of_date, ps.account_id, a.account_number, a.nickname,
               ins.code AS institution_code, ins.display_name AS institution_name,
               inst.symbol, inst.asset_type, inst.currency,
               inst.option_expiry, inst.option_strike, inst.option_type,
               ps.quantity, ps.avg_cost, ps.book_value,
               ps.market_price, ps.market_value, ps.unrealized_pnl
          FROM position_snapshots ps
          JOIN latest l ON l.account_id = ps.account_id
                       AND l.instrument_id = ps.instrument_id
                       AND l.d = ps.as_of_date
          JOIN accounts a ON a.account_id = ps.account_id
          JOIN institutions ins ON ins.institution_id = a.institution_id
          JOIN instruments inst ON inst.instrument_id = ps.instrument_id
         WHERE ps.quantity != 0
         ORDER BY institution_name, a.account_number, inst.symbol
    """
    params: list = [as_of]
    if account_ids:
        acct_clause = f"AND account_id IN ({','.join('?' * len(account_ids))})"
        params.extend(account_ids)
    else:
        acct_clause = ""
    sql = base_sql.format(acct_clause=acct_clause)
    with sqlite_db.session() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


@router.get("/snapshot")
def snapshot(
    month_end: str | None = Query(None, description="ISO date; defaults to latest"),
    account_id: str | None = Query(None),
) -> dict:
    accts = _csv_ints(account_id)
    if not month_end:
        with sqlite_db.session() as conn:
            row = conn.execute("SELECT MAX(as_of_date) FROM position_snapshots").fetchone()
        month_end = row[0] if row and row[0] else ""
    rows = _holdings_at(month_end, accts) if month_end else []
    return {"as_of_date": month_end, "rows": rows}


@router.get("/diff")
def diff(
    a: str = Query(...),
    b: str = Query(...),
    account_id: str | None = Query(None),
) -> dict:
    accts = _csv_ints(account_id)
    rows_a = {(r["account_id"], r["symbol"], r["option_expiry"], r["option_strike"], r["option_type"]): r
              for r in _holdings_at(a, accts)}
    rows_b = {(r["account_id"], r["symbol"], r["option_expiry"], r["option_strike"], r["option_type"]): r
              for r in _holdings_at(b, accts)}
    keys = set(rows_a) | set(rows_b)
    diffs = []
    for k in sorted(keys, key=lambda x: (x[1] or "", x[0])):
        ra, rb = rows_a.get(k), rows_b.get(k)
        qa = ra["quantity"] if ra else 0.0
        qb = rb["quantity"] if rb else 0.0
        if abs((qb or 0) - (qa or 0)) < 1e-9:
            continue
        ref = rb or ra
        diffs.append({
            "account_id": ref["account_id"],
            "account_number": ref["account_number"],
            "institution_code": ref["institution_code"],
            "symbol": ref["symbol"],
            "asset_type": ref["asset_type"], "currency": ref["currency"],
            "option_expiry": ref["option_expiry"], "option_strike": ref["option_strike"],
            "option_type": ref["option_type"],
            "qty_a": qa, "qty_b": qb, "qty_delta": (qb or 0) - (qa or 0),
            "mv_a": (ra or {}).get("market_value"),
            "mv_b": (rb or {}).get("market_value"),
        })
    return {"a": a, "b": b, "rows": diffs}
