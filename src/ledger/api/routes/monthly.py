"""GET /monthly — month-end consolidated holdings + diff."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ...db import sqlite as sqlite_db

router = APIRouter(prefix="/monthly", tags=["monthly"])


def _holdings_at(month_end: str) -> list[dict]:
    with sqlite_db.session() as conn:
        rows = [dict(r) for r in conn.execute(
            """
            WITH latest AS (
              SELECT account_id, instrument_id, MAX(as_of_date) AS d
                FROM position_snapshots
               WHERE as_of_date <= ?
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
             ORDER BY institution_name, a.account_number, inst.symbol
            """,
            (month_end,),
        ).fetchall()]
    return rows


@router.get("/snapshot")
def snapshot(month_end: str = Query(...)) -> dict:
    rows = _holdings_at(month_end)
    return {"as_of_date": month_end, "rows": rows}


@router.get("/diff")
def diff(a: str = Query(...), b: str = Query(...)) -> dict:
    """Diff month-end b minus month-end a (b is the "later" snapshot)."""
    rows_a = {(r["account_id"], r["symbol"], r["option_expiry"], r["option_strike"], r["option_type"]): r
              for r in _holdings_at(a)}
    rows_b = {(r["account_id"], r["symbol"], r["option_expiry"], r["option_strike"], r["option_type"]): r
              for r in _holdings_at(b)}
    keys = set(rows_a) | set(rows_b)
    diffs = []
    for k in sorted(keys):
        ra, rb = rows_a.get(k), rows_b.get(k)
        qa = ra["quantity"] if ra else 0.0
        qb = rb["quantity"] if rb else 0.0
        if abs((qb or 0) - (qa or 0)) < 1e-9:
            continue
        ref = rb or ra
        diffs.append({
            "account_id": ref["account_id"], "symbol": ref["symbol"],
            "asset_type": ref["asset_type"], "currency": ref["currency"],
            "option_expiry": ref["option_expiry"], "option_strike": ref["option_strike"],
            "option_type": ref["option_type"],
            "qty_a": qa, "qty_b": qb, "qty_delta": (qb or 0) - (qa or 0),
        })
    return {"a": a, "b": b, "rows": diffs}
