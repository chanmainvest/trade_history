"""GET /transactions — filterable transaction list.

Filters accept either a single value or a comma-separated list. All filters
AND together. ``min_abs_amount`` keeps rows whose ``ABS(net_amount)`` is at
least that value.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from ...db import sqlite as sqlite_db
from ...statement_selection import canonical_statement_clause

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _csv_list(v: str | None) -> list[str]:
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


@router.get("")
def list_transactions(
    start: Annotated[date | None, Query(description="ISO date inclusive")] = None,
    end: Annotated[date | None, Query(description="ISO date inclusive")] = None,
    institution: str | None = Query(None, description="comma-separated codes"),
    account_id: str | None = Query(None, description="comma-separated ids"),
    symbol: str | None = Query(None, description="comma-separated tickers"),
    txn_type: str | None = Query(None, description="comma-separated types"),
    min_abs_amount: float | None = Query(
        None, description="keep |net_amount| >= this value"
    ),
    limit: int = 5000,
) -> dict:
    limit = min(max(limit, 1), 50_000)
    canonical_sql = canonical_statement_clause("t.statement_id")
    rows_sql = f"""
        WITH ledger_rows AS (
            SELECT 'transaction' AS row_kind,
                   'transaction:' || t.transaction_id AS row_id,
                   t.transaction_id, NULL AS initial_id, t.statement_id,
                   t.trade_date, t.settle_date, t.txn_type,
                   t.quantity, t.price, t.gross_amount, t.commission,
                   t.other_fees, t.net_amount, t.currency, t.description,
                   a.account_id, a.account_number, a.account_type, a.nickname,
                   i.code AS institution_code, i.display_name AS institution_name,
                   COALESCE(inst.option_root, inst.symbol) AS symbol,
                   successor.symbol AS related_symbol,
                   inst.asset_type, inst.option_expiry, inst.option_strike,
                   inst.option_type
              FROM transactions t
              JOIN accounts a ON a.account_id = t.account_id
              JOIN institutions i ON i.institution_id = a.institution_id
              LEFT JOIN instruments inst ON inst.instrument_id = t.instrument_id
              LEFT JOIN instrument_ticker_change_sources source
                     ON source.transaction_id = t.transaction_id
              LEFT JOIN instrument_ticker_changes tc
                     ON tc.ticker_change_id = source.ticker_change_id
              LEFT JOIN instruments successor
                     ON successor.instrument_id = tc.to_instrument_id
             WHERE {canonical_sql}
            UNION ALL
            SELECT 'initial_position' AS row_kind,
                   'initial:' || ip.initial_id AS row_id,
                   NULL AS transaction_id, ip.initial_id, NULL AS statement_id,
                   ip.as_of_date AS trade_date, NULL AS settle_date,
                   'initial_position' AS txn_type,
                   ip.quantity, NULL AS price, NULL AS gross_amount,
                   NULL AS commission, NULL AS other_fees, NULL AS net_amount,
                   ip.currency,
                   CASE
                       WHEN ip.notes LIKE 'inferred:%'
                       THEN 'Inferred opening position before first complete statement'
                       ELSE 'Reviewed opening position before first complete statement'
                   END AS description,
                   a.account_id, a.account_number, a.account_type, a.nickname,
                   i.code AS institution_code, i.display_name AS institution_name,
                   COALESCE(inst.option_root, inst.symbol) AS symbol,
                   NULL AS related_symbol,
                   inst.asset_type, inst.option_expiry, inst.option_strike,
                   inst.option_type
              FROM initial_positions ip
              JOIN accounts a ON a.account_id = ip.account_id
              JOIN institutions i ON i.institution_id = a.institution_id
              JOIN instruments inst ON inst.instrument_id = ip.instrument_id
        )
        SELECT * FROM ledger_rows WHERE 1=1
    """
    filters: list[str] = []
    params: list = []
    if start:
        filters.append(" AND trade_date >= ?")
        params.append(start.isoformat())
    if end:
        filters.append(" AND trade_date <= ?")
        params.append(end.isoformat())
    insts = _csv_list(institution)
    if insts:
        filters.append(f" AND institution_code IN ({','.join('?' * len(insts))})")
        params.extend(insts)
    accts = [int(x) for x in _csv_list(account_id) if x.isdigit()]
    if accts:
        filters.append(f" AND account_id IN ({','.join('?' * len(accts))})")
        params.extend(accts)
    syms = [s.upper() for s in _csv_list(symbol)]
    if syms:
        filters.append(
            f" AND (symbol IN ({','.join('?' * len(syms))})"
            f" OR related_symbol IN ({','.join('?' * len(syms))}))"
        )
        params.extend(syms)
        params.extend(syms)
    types = _csv_list(txn_type)
    if types:
        filters.append(f" AND txn_type IN ({','.join('?' * len(types))})")
        params.extend(types)
    if min_abs_amount is not None and min_abs_amount > 0:
        filters.append(" AND ABS(COALESCE(net_amount, 0)) >= ?")
        params.append(min_abs_amount)

    with sqlite_db.session(sqlite_db.SQLITE_PATH) as conn:
        all_rows = [
            dict(row)
            for row in conn.execute(
                rows_sql + "".join(filters) + " ORDER BY trade_date DESC, row_id DESC",
                params,
            ).fetchall()
        ]
    total_count = len(all_rows)
    rows = all_rows[:limit]
    return {"rows": rows, "count": len(rows), "total_count": total_count, "has_more": total_count > len(rows)}


@router.get("/accounts")
def accounts() -> dict:
    with sqlite_db.session(sqlite_db.SQLITE_PATH) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT a.account_id, a.account_number, a.account_type, a.nickname, "
            "       a.base_currency, i.code AS institution_code, i.display_name AS institution_name "
            "  FROM accounts a JOIN institutions i ON i.institution_id = a.institution_id "
            " ORDER BY i.display_name, a.account_number"
        ).fetchall()]
    return {"rows": rows}


@router.get("/symbols")
def symbols() -> dict:
    with sqlite_db.session(sqlite_db.SQLITE_PATH) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT DISTINCT COALESCE(i.option_root, i.symbol) AS symbol, "
            "       i.asset_type, i.currency FROM instruments i "
            "WHERE i.asset_type IN ('equity','etf','option','mutual_fund','bond') "
            "  AND (EXISTS (SELECT 1 FROM transactions t WHERE t.instrument_id = i.instrument_id) "
            "       OR EXISTS (SELECT 1 FROM position_snapshots ps WHERE ps.instrument_id = i.instrument_id) "
            "       OR EXISTS (SELECT 1 FROM initial_positions ip WHERE ip.instrument_id = i.instrument_id) "
            "       OR EXISTS (SELECT 1 FROM instrument_ticker_changes tc "
            "                   WHERE tc.from_instrument_id = i.instrument_id "
            "                      OR tc.to_instrument_id = i.instrument_id)) "
            "ORDER BY symbol"
        ).fetchall()]
    return {"rows": rows}


@router.get("/txn-types")
def txn_types() -> dict:
    """Distinct transaction types actually present in the DB."""
    with sqlite_db.session(sqlite_db.SQLITE_PATH) as conn:
        rows = [r[0] for r in conn.execute(
            "SELECT txn_type FROM ("
            "SELECT DISTINCT txn_type FROM transactions "
            "UNION ALL SELECT 'initial_position' WHERE EXISTS (SELECT 1 FROM initial_positions)"
            ") ORDER BY txn_type"
        ).fetchall()]
    return {"rows": rows}


@router.get("/latest-date")
def latest_date() -> dict:
    with sqlite_db.session(sqlite_db.SQLITE_PATH) as conn:
        row = conn.execute(
            "SELECT MAX(as_of_date) FROM position_snapshots"
        ).fetchone()
    return {"latest": row[0] if row else None}
