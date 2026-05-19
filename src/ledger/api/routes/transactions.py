"""GET /transactions — filterable transaction list.

Filters accept either a single value or a comma-separated list. All filters
AND together. ``min_abs_amount`` keeps rows whose ``ABS(net_amount)`` is at
least that value.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from ...db import sqlite as sqlite_db

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _csv_list(v: str | None) -> list[str]:
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


@router.get("")
def list_transactions(
    start: str | None = Query(None, description="ISO date inclusive"),
    end: str | None = Query(None, description="ISO date inclusive"),
    institution: str | None = Query(None, description="comma-separated codes"),
    account_id: str | None = Query(None, description="comma-separated ids"),
    symbol: str | None = Query(None, description="comma-separated tickers"),
    txn_type: str | None = Query(None, description="comma-separated types"),
    min_abs_amount: float | None = Query(
        None, description="keep |net_amount| >= this value"
    ),
    limit: int = 5000,
) -> dict:
    sql = [
        "SELECT t.transaction_id, t.trade_date, t.settle_date, t.txn_type,",
        "       t.quantity, t.price, t.gross_amount, t.commission, t.other_fees,",
        "       t.net_amount, t.currency, t.description,",
        "       a.account_id, a.account_number, a.account_type, a.nickname,",
        "       i.code AS institution_code, i.display_name AS institution_name,",
        "       inst.symbol, inst.asset_type, inst.option_expiry, inst.option_strike, inst.option_type",
        "  FROM transactions t",
        "  JOIN accounts a ON a.account_id = t.account_id",
        "  JOIN institutions i ON i.institution_id = a.institution_id",
        "  LEFT JOIN instruments inst ON inst.instrument_id = t.instrument_id",
        " WHERE 1=1",
    ]
    params: list = []
    if start:
        sql.append(" AND t.trade_date >= ?")
        params.append(start)
    if end:
        sql.append(" AND t.trade_date <= ?")
        params.append(end)
    insts = _csv_list(institution)
    if insts:
        sql.append(f" AND i.code IN ({','.join('?' * len(insts))})")
        params.extend(insts)
    accts = [int(x) for x in _csv_list(account_id) if x.isdigit()]
    if accts:
        sql.append(f" AND a.account_id IN ({','.join('?' * len(accts))})")
        params.extend(accts)
    syms = [s.upper() for s in _csv_list(symbol)]
    if syms:
        sql.append(f" AND inst.symbol IN ({','.join('?' * len(syms))})")
        params.extend(syms)
    types = _csv_list(txn_type)
    if types:
        sql.append(f" AND t.txn_type IN ({','.join('?' * len(types))})")
        params.extend(types)
    if min_abs_amount is not None and min_abs_amount > 0:
        sql.append(" AND ABS(COALESCE(t.net_amount, 0)) >= ?")
        params.append(min_abs_amount)
    sql.append(" ORDER BY t.trade_date DESC, t.transaction_id DESC LIMIT ?")
    params.append(limit)

    with sqlite_db.session() as conn:
        rows = [dict(r) for r in conn.execute("\n".join(sql), params).fetchall()]
    return {"rows": rows, "count": len(rows)}


@router.get("/accounts")
def accounts() -> dict:
    with sqlite_db.session() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT a.account_id, a.account_number, a.account_type, a.nickname, "
            "       a.base_currency, i.code AS institution_code, i.display_name AS institution_name "
            "  FROM accounts a JOIN institutions i ON i.institution_id = a.institution_id "
            " ORDER BY i.display_name, a.account_number"
        ).fetchall()]
    return {"rows": rows}


@router.get("/symbols")
def symbols() -> dict:
    with sqlite_db.session() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT DISTINCT symbol, asset_type, currency FROM instruments "
            "WHERE asset_type IN ('equity','etf','option','mutual_fund','bond') "
            "ORDER BY symbol"
        ).fetchall()]
    return {"rows": rows}


@router.get("/txn-types")
def txn_types() -> dict:
    """Distinct transaction types actually present in the DB."""
    with sqlite_db.session() as conn:
        rows = [r[0] for r in conn.execute(
            "SELECT DISTINCT txn_type FROM transactions ORDER BY txn_type"
        ).fetchall()]
    return {"rows": rows}


@router.get("/latest-date")
def latest_date() -> dict:
    with sqlite_db.session() as conn:
        row = conn.execute(
            "SELECT MAX(as_of_date) FROM position_snapshots"
        ).fetchone()
    return {"latest": row[0] if row else None}
