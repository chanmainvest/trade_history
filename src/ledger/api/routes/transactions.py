"""GET /transactions — filterable transaction list."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ...db import sqlite as sqlite_db

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.get("")
def list_transactions(
    start: str | None = Query(None, description="ISO date inclusive"),
    end: str | None = Query(None, description="ISO date inclusive"),
    institution: str | None = None,
    account_id: int | None = None,
    symbol: str | None = None,
    txn_type: str | None = None,
    limit: int = 5000,
) -> dict:
    sql = [
        "SELECT t.transaction_id, t.trade_date, t.settle_date, t.txn_type,",
        "       t.quantity, t.price, t.gross_amount, t.commission, t.other_fees,",
        "       t.net_amount, t.currency, t.description,",
        "       a.account_number, a.account_type, a.nickname,",
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
    if institution:
        sql.append(" AND i.code = ?")
        params.append(institution)
    if account_id is not None:
        sql.append(" AND a.account_id = ?")
        params.append(account_id)
    if symbol:
        sql.append(" AND inst.symbol = ?")
        params.append(symbol.upper())
    if txn_type:
        sql.append(" AND t.txn_type = ?")
        params.append(txn_type)
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
            "WHERE asset_type IN ('equity','etf','option') ORDER BY symbol"
        ).fetchall()]
    return {"rows": rows}
