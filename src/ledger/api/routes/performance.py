"""GET /performance — total asset value over time."""
from __future__ import annotations

from fastapi import APIRouter, Query

from ...db import sqlite as sqlite_db

router = APIRouter(prefix="/performance", tags=["performance"])


@router.get("/total")
def total(institution: str | None = None, account_id: int | None = None,
          symbol: str | None = None, asset_type: str | None = None) -> dict:
    """Sum market_value at each statement date, with optional filters."""
    sql = [
        "SELECT ps.as_of_date, SUM(ps.market_value) AS market_value, ps.currency",
        "  FROM position_snapshots ps",
        "  JOIN accounts a ON a.account_id = ps.account_id",
        "  JOIN institutions i ON i.institution_id = a.institution_id",
        "  JOIN instruments inst ON inst.instrument_id = ps.instrument_id",
        " WHERE 1=1",
    ]
    params: list = []
    if institution:
        sql.append(" AND i.code = ?"); params.append(institution)
    if account_id is not None:
        sql.append(" AND a.account_id = ?"); params.append(account_id)
    if symbol:
        sql.append(" AND inst.symbol = ?"); params.append(symbol.upper())
    if asset_type:
        sql.append(" AND inst.asset_type = ?"); params.append(asset_type)
    sql.append(" GROUP BY ps.as_of_date, ps.currency ORDER BY ps.as_of_date")
    with sqlite_db.session() as conn:
        rows = [dict(r) for r in conn.execute("\n".join(sql), params).fetchall()]
    return {"rows": rows}


@router.get("/cash")
def cash(account_id: int | None = Query(None)) -> dict:
    sql = ["SELECT as_of_date, account_id, currency, closing_balance "
           "  FROM cash_balances WHERE 1=1"]
    params: list = []
    if account_id is not None:
        sql.append(" AND account_id = ?"); params.append(account_id)
    sql.append(" ORDER BY as_of_date")
    with sqlite_db.session() as conn:
        rows = [dict(r) for r in conn.execute("\n".join(sql), params).fetchall()]
    return {"rows": rows}
