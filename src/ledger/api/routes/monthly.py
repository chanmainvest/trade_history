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

import duckdb
from fastapi import APIRouter, Query

from ...config import DUCKDB_PATH
from ...db import sqlite as sqlite_db
from ...quantity import quantity_delta

router = APIRouter(prefix="/monthly", tags=["monthly"])

NON_CASH_TXN_TYPES = {
    "stock_split",
    "stock_split_credit",
    "stock_split_debit",
    "name_change",
    "spinoff",
    "merger",
}


def _csv_ints(v: str | None) -> list[int]:
    if not v:
        return []
    return [int(x) for x in v.split(",") if x.strip().lstrip("-").isdigit()]


def _holdings_at(as_of: str, account_ids: list[int], path: Path | str | None = None) -> list[dict]:
    base_sql = """
        WITH scope_anchor AS (
            SELECT ps.account_id, ps.currency, MAX(ps.as_of_date) AS d
              FROM position_snapshots ps
              JOIN snapshot_sets ss ON ss.snapshot_set_id = ps.snapshot_set_id
             WHERE ps.as_of_date <= ?
               AND ss.section_type = 'positions'
               AND ss.can_clear_omitted = 1
             GROUP BY ps.account_id, ps.currency
        ),
        anchor_qty AS (
               SELECT account_id, instrument_id, instrument_key, currency,
                      quantity, as_of_date,
                     avg_cost, book_value, market_price,
                     market_value, unrealized_pnl
                 FROM (
                  SELECT ps.account_id, ps.instrument_id, i.instrument_key,
                        ps.currency, ps.quantity, ps.as_of_date,
                        ps.avg_cost, ps.book_value, ps.market_price,
                        ps.market_value, ps.unrealized_pnl,
                        ROW_NUMBER() OVER (
                         PARTITION BY ps.account_id, i.instrument_key, ps.currency
                         ORDER BY ps.statement_id DESC, ps.snapshot_id DESC
                        ) AS rn
                    FROM position_snapshots ps
                    JOIN snapshot_sets ss ON ss.snapshot_set_id = ps.snapshot_set_id
                    JOIN scope_anchor aa ON aa.account_id = ps.account_id
                                      AND aa.currency = ps.currency
                                      AND aa.d = ps.as_of_date
                    JOIN instruments i ON i.instrument_id = ps.instrument_id
                   WHERE ss.section_type = 'positions'
                     AND ss.can_clear_omitted = 1
                 )
                WHERE rn = 1
        ),
        post_txn AS (
            SELECT t.account_id, i.instrument_id, i.instrument_key, i.currency,
                   SUM(COALESCE(t.position_delta, quantity_delta(t.txn_type, t.quantity))) AS quantity
               FROM transactions t
               JOIN instruments i ON i.instrument_id = t.instrument_id
               LEFT JOIN scope_anchor aa ON aa.account_id = t.account_id
                                        AND aa.currency = i.currency
              WHERE t.instrument_id IS NOT NULL
                AND t.trade_date <= ?
                AND (aa.d IS NULL OR t.trade_date > aa.d)
              GROUP BY t.account_id, i.instrument_id, i.instrument_key, i.currency
        ),
        pre_snapshot AS (
            SELECT ip.account_id, i.instrument_id, i.instrument_key, ip.currency,
                   SUM(ip.quantity) AS quantity
               FROM initial_positions ip
               JOIN instruments i ON i.instrument_id = ip.instrument_id
               LEFT JOIN scope_anchor aa ON aa.account_id = ip.account_id
                                        AND aa.currency = ip.currency
              WHERE ip.as_of_date <= ?
                AND aa.d IS NULL
              GROUP BY ip.account_id, i.instrument_id, i.instrument_key, ip.currency
        ),
        keys AS (
            SELECT account_id, instrument_id, instrument_key, currency FROM anchor_qty
            UNION
            SELECT account_id, instrument_id, instrument_key, currency FROM post_txn
            UNION
            SELECT account_id, instrument_id, instrument_key, currency FROM pre_snapshot
        ),
        qty AS (
            SELECT k.account_id, k.instrument_id, k.instrument_key, k.currency,
                   COALESCE(aq.quantity, 0) + COALESCE(pt.quantity, 0)
                    + COALESCE(pre.quantity, 0) AS quantity
               FROM keys k
               LEFT JOIN anchor_qty aq ON aq.account_id = k.account_id
                                      AND aq.instrument_key = k.instrument_key
                                      AND aq.currency = k.currency
               LEFT JOIN post_txn pt ON pt.account_id = k.account_id
                                    AND pt.instrument_key = k.instrument_key
                                    AND pt.currency = k.currency
               LEFT JOIN pre_snapshot pre ON pre.account_id = k.account_id
                                          AND pre.instrument_key = k.instrument_key
                                          AND pre.currency = k.currency
             WHERE ABS(COALESCE(aq.quantity, 0) + COALESCE(pt.quantity, 0)
                       + COALESCE(pre.quantity, 0)) > 1e-9
        )
        SELECT COALESCE(aq.as_of_date, ?) AS as_of_date,
               q.account_id, a.account_number, a.nickname,
                ins.code AS institution_code, ins.display_name AS institution_name,
                COALESCE(inst.option_root, inst.symbol) AS symbol,
                inst.asset_type, inst.currency, q.instrument_key,
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
                                   AND aq.instrument_key = q.instrument_key
                                   AND aq.currency = q.currency
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
        security_rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        cash_rows = _cash_at(conn, as_of, account_ids)
        return security_rows + cash_rows


def _cash_at(conn, as_of: str, account_ids: list[int]) -> list[dict]:
    acct_clause = ""
    params: list = [as_of, as_of, as_of]
    if account_ids:
        acct_clause = f"AND k.account_id IN ({','.join('?' * len(account_ids))})"
        params.extend(account_ids)
    sql = f"""
        WITH account_currency AS (
            SELECT account_id, currency FROM cash_balances
            UNION
            SELECT account_id, currency FROM initial_cash
            UNION
            SELECT account_id, currency FROM transactions WHERE currency IS NOT NULL
        ),
        anchor AS (
            SELECT cb.account_id, cb.currency, MAX(cb.as_of_date) AS as_of_date
              FROM cash_balances cb
              JOIN snapshot_sets ss ON ss.snapshot_set_id = cb.snapshot_set_id
             WHERE cb.as_of_date <= ?
               AND ss.section_type = 'cash'
               AND ss.can_clear_omitted = 1
             GROUP BY cb.account_id, cb.currency
        ),
        anchor_balance AS (
            SELECT cb.account_id, cb.currency, cb.as_of_date, cb.closing_balance
              FROM cash_balances cb
              JOIN anchor a ON a.account_id = cb.account_id
                           AND a.currency = cb.currency
                           AND a.as_of_date = cb.as_of_date
        ),
        post_txn AS (
            SELECT t.account_id, t.currency,
                   SUM(COALESCE(t.cash_delta, t.net_amount)) AS balance_delta
              FROM transactions t
              LEFT JOIN anchor a ON a.account_id = t.account_id
                                AND a.currency = t.currency
             WHERE t.currency IS NOT NULL
                AND COALESCE(t.cash_delta, t.net_amount) IS NOT NULL
                AND COALESCE(t.cash_effective_date, t.trade_date) <= ?
                AND (a.as_of_date IS NULL OR
                     COALESCE(t.cash_effective_date, t.trade_date) > a.as_of_date)
               AND t.txn_type NOT IN ({','.join('?' * len(NON_CASH_TXN_TYPES))})
             GROUP BY t.account_id, t.currency
        ),
        initial AS (
            SELECT ic.account_id, ic.currency, SUM(ic.balance) AS balance
              FROM initial_cash ic
              LEFT JOIN anchor a ON a.account_id = ic.account_id
                                AND a.currency = ic.currency
             WHERE ic.as_of_date <= ?
               AND a.as_of_date IS NULL
             GROUP BY ic.account_id, ic.currency
        ),
        cash_qty AS (
            SELECT k.account_id, k.currency,
                   COALESCE(ab.closing_balance, 0) + COALESCE(pt.balance_delta, 0)
                   + COALESCE(i.balance, 0) AS balance,
                   ab.as_of_date AS anchor_date
              FROM account_currency k
              LEFT JOIN anchor_balance ab ON ab.account_id = k.account_id
                                         AND ab.currency = k.currency
              LEFT JOIN post_txn pt ON pt.account_id = k.account_id
                                   AND pt.currency = k.currency
              LEFT JOIN initial i ON i.account_id = k.account_id
                                 AND i.currency = k.currency
             WHERE 1=1 {acct_clause}
        )
        SELECT COALESCE(cq.anchor_date, ?) AS as_of_date,
               cq.account_id, a.account_number, a.nickname,
               ins.code AS institution_code, ins.display_name AS institution_name,
                cq.currency || ' Cash' AS symbol,
                'cash' AS asset_type,
                cq.currency AS currency,
                'cash|' || cq.currency AS instrument_key,
               NULL AS option_expiry, NULL AS option_strike, NULL AS option_type,
               cq.balance AS quantity,
               NULL AS avg_cost,
               cq.balance AS book_value,
               1.0 AS market_price,
               cq.balance AS market_value,
               NULL AS unrealized_pnl
          FROM cash_qty cq
          JOIN accounts a ON a.account_id = cq.account_id
          JOIN institutions ins ON ins.institution_id = a.institution_id
         WHERE ABS(cq.balance) > 1e-9
         ORDER BY institution_name, a.account_number, cq.currency
    """
    query_params = [as_of, as_of, *sorted(NON_CASH_TXN_TYPES), as_of, *params[3:], as_of]
    return [dict(r) for r in conn.execute(sql, query_params).fetchall()]


def _fx_rate(base: str, quote: str, as_of: str) -> tuple[float | None, str | None]:
    if base == quote:
        return 1.0, as_of
    try:
        con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
        try:
            row = con.execute(
                """
                SELECT rate, rate_date
                  FROM fx_rates
                 WHERE base = ? AND quote = ? AND rate_date <= ?
                 ORDER BY rate_date DESC
                 LIMIT 1
                """,
                [base, quote, as_of],
            ).fetchone()
            if row:
                return float(row[0]), str(row[1])
            inverse = con.execute(
                """
                SELECT rate, rate_date
                  FROM fx_rates
                 WHERE base = ? AND quote = ? AND rate_date <= ?
                 ORDER BY rate_date DESC
                 LIMIT 1
                """,
                [quote, base, as_of],
            ).fetchone()
            if inverse and inverse[0]:
                return 1.0 / float(inverse[0]), str(inverse[1])
        finally:
            con.close()
    except Exception:
        return None, None
    return None, None


def _snapshot_totals(rows: list[dict], as_of: str) -> dict:
    native: dict[str, float] = {}
    for row in rows:
        currency = row.get("currency") or ""
        if not currency:
            continue
        native[currency] = native.get(currency, 0.0) + float(row.get("market_value") or 0.0)
    usd_to_cad, cad_fx_date = _fx_rate("USD", "CAD", as_of)
    cad_to_usd, usd_fx_date = _fx_rate("CAD", "USD", as_of)
    combined: dict[str, float | str | None] = {}
    if usd_to_cad is not None:
        combined["CAD"] = native.get("CAD", 0.0) + native.get("USD", 0.0) * usd_to_cad
        combined["usd_cad"] = usd_to_cad
        combined["cad_fx_date"] = cad_fx_date
    if cad_to_usd is not None:
        combined["USD"] = native.get("USD", 0.0) + native.get("CAD", 0.0) * cad_to_usd
        combined["cad_usd"] = cad_to_usd
        combined["usd_fx_date"] = usd_fx_date
    return {"native": native, "combined": combined}


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
    return {"as_of_date": month_end, "rows": rows, "totals": _snapshot_totals(rows, month_end) if month_end else {}}


@router.get("/diff")
def diff(
    a: str = Query(...),
    b: str = Query(...),
    account_id: str | None = Query(None),
) -> dict:
    accts = _csv_ints(account_id)
    rows_a = {(r["account_id"], r["instrument_key"], r["currency"]): r
              for r in _holdings_at(a, accts)}
    rows_b = {(r["account_id"], r["instrument_key"], r["currency"]): r
              for r in _holdings_at(b, accts)}
    keys = set(rows_a) | set(rows_b)
    diffs = []
    for k in sorted(keys, key=lambda x: (x[1] or "", x[0], x[2] or "")):
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
            "instrument_key": ref["instrument_key"],
            "symbol": ref["symbol"],
            "asset_type": ref["asset_type"], "currency": ref["currency"],
            "option_expiry": ref["option_expiry"], "option_strike": ref["option_strike"],
            "option_type": ref["option_type"],
            "qty_a": qa, "qty_b": qb, "qty_delta": (qb or 0) - (qa or 0),
            "mv_a": (ra or {}).get("market_value"),
            "mv_b": (rb or {}).get("market_value"),
        })
    return {"a": a, "b": b, "rows": diffs}
