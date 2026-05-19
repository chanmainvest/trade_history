"""GET /monthly — point-in-time consolidated holdings + diff.

Implementation notes (see AGENTS.md item 7):

For any given ``as_of`` date we use the latest complete statement per account
on or before that day as the quantity checkpoint, then replay signed
transaction movements after that account statement up to ``as_of``. Before the
first statement for an account, ``initial_positions`` plus transactions are
used.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query

from ...db import sqlite as sqlite_db
from ...quantity import quantity_delta

router = APIRouter(prefix="/monthly", tags=["monthly"])


def _csv_ints(v: str | None) -> list[int]:
    if not v:
        return []
    return [int(x) for x in v.split(",") if x.strip().lstrip("-").isdigit()]


def _holdings_at(as_of: str, account_ids: list[int], path: Path | str | None = None) -> list[dict]:
    base_sql = """
        WITH account_anchor AS (
            SELECT account_id, MAX(as_of_date) AS d
              FROM position_snapshots
             WHERE as_of_date <= ?
             GROUP BY account_id
        ),
        anchor_qty AS (
            SELECT ps.account_id, ps.instrument_id, ps.quantity, ps.as_of_date,
                   ps.avg_cost, ps.book_value, ps.market_price,
                   ps.market_value, ps.unrealized_pnl
              FROM position_snapshots ps
              JOIN account_anchor aa ON aa.account_id = ps.account_id
                                    AND aa.d = ps.as_of_date
        ),
        post_txn AS (
            SELECT t.account_id, t.instrument_id,
                   SUM(quantity_delta(t.txn_type, t.quantity)) AS quantity
              FROM transactions t
              LEFT JOIN account_anchor aa ON aa.account_id = t.account_id
             WHERE t.instrument_id IS NOT NULL
               AND t.quantity IS NOT NULL
               AND t.trade_date <= ?
               AND (aa.d IS NULL OR t.trade_date > aa.d)
             GROUP BY t.account_id, t.instrument_id
        ),
        pre_snapshot AS (
            SELECT ip.account_id, ip.instrument_id, SUM(ip.quantity) AS quantity
              FROM initial_positions ip
              LEFT JOIN account_anchor aa ON aa.account_id = ip.account_id
             WHERE ip.as_of_date <= ?
               AND aa.d IS NULL
             GROUP BY ip.account_id, ip.instrument_id
        ),
        keys AS (
            SELECT account_id, instrument_id FROM anchor_qty
            UNION
            SELECT account_id, instrument_id FROM post_txn
            UNION
            SELECT account_id, instrument_id FROM pre_snapshot
        ),
        qty AS (
            SELECT k.account_id, k.instrument_id,
                   COALESCE(aq.quantity, 0) + COALESCE(pt.quantity, 0)
                   + COALESCE(pre.quantity, 0) AS quantity
              FROM keys k
              LEFT JOIN anchor_qty aq ON aq.account_id = k.account_id
                                     AND aq.instrument_id = k.instrument_id
              LEFT JOIN post_txn pt ON pt.account_id = k.account_id
                                   AND pt.instrument_id = k.instrument_id
              LEFT JOIN pre_snapshot pre ON pre.account_id = k.account_id
                                         AND pre.instrument_id = k.instrument_id
             WHERE ABS(COALESCE(aq.quantity, 0) + COALESCE(pt.quantity, 0)
                       + COALESCE(pre.quantity, 0)) > 1e-9
        )
        SELECT COALESCE(aq.as_of_date, ?) AS as_of_date,
               q.account_id, a.account_number, a.nickname,
               ins.code AS institution_code, ins.display_name AS institution_name,
               COALESCE(inst.option_root, inst.symbol) AS symbol,
               inst.asset_type, inst.currency,
               inst.option_expiry, inst.option_strike, inst.option_type,
               q.quantity,
               aq.avg_cost,
               CASE
                   WHEN aq.avg_cost IS NOT NULL THEN aq.avg_cost * q.quantity
                   ELSE aq.book_value
               END AS book_value,
               aq.market_price,
               CASE
                   WHEN aq.market_price IS NOT NULL THEN aq.market_price * q.quantity
                   ELSE aq.market_value
               END AS market_value,
               CASE
                   WHEN aq.market_price IS NOT NULL AND aq.avg_cost IS NOT NULL
                   THEN (aq.market_price - aq.avg_cost) * q.quantity
                   ELSE aq.unrealized_pnl
               END AS unrealized_pnl
          FROM qty q
          JOIN accounts a ON a.account_id = q.account_id
          JOIN institutions ins ON ins.institution_id = a.institution_id
          JOIN instruments inst ON inst.instrument_id = q.instrument_id
          LEFT JOIN anchor_qty aq ON aq.account_id = q.account_id
                                  AND aq.instrument_id = q.instrument_id
         WHERE 1=1
        {acct_clause}
         ORDER BY institution_name, a.account_number, inst.symbol
    """
    params: list = [as_of, as_of, as_of, as_of]
    if account_ids:
        acct_clause = f"AND q.account_id IN ({','.join('?' * len(account_ids))})"
        params.extend(account_ids)
    else:
        acct_clause = ""
    sql = base_sql.format(acct_clause=acct_clause)
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    with sqlite_db.session(db_path) as conn:
        conn.create_function("quantity_delta", 2, quantity_delta)
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
