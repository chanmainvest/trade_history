"""GET /trades — all fills with optional filtering and closed-position P/L."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from trade_history.api.deps import get_sqlite

router = APIRouter()


@router.get("")
def list_trades(
    conn: Annotated[sqlite3.Connection, Depends(get_sqlite)],
    currency: Literal["CAD", "USD"] = "CAD",
    account_id: str | None = None,
    symbol: str | None = None,
    asset_type: str | None = None,
    activity: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort_by: str = "trade_date",
    sort_dir: Literal["asc", "desc"] = "desc",
    limit: int = Query(default=500, le=5000),
    offset: int = 0,
):
    """Return trade list with optional P/L for closed positions."""
    allowed_sort = {
        "trade_date", "symbol", "activity", "quantity", "price", "amount", "account_id"
    }
    if sort_by not in allowed_sort:
        sort_by = "trade_date"

    filters = []
    params: list = []

    if account_id:
        filters.append("a.account_id = ?")
        params.append(account_id)
    if symbol:
        filters.append("i.symbol LIKE ?")
        params.append(f"%{symbol}%")
    if asset_type:
        filters.append("i.asset_type = ?")
        params.append(asset_type)
    if activity:
        filters.append("t.activity = ?")
        params.append(activity)
    if date_from:
        filters.append("t.trade_date >= ?")
        params.append(date_from)
    if date_to:
        filters.append("t.trade_date <= ?")
        params.append(date_to)

    where = ("WHERE " + " AND ".join(filters)) if filters else ""

    rows = conn.execute(
        f"""
        SELECT
            t.id,
            a.institution,
            a.account_id,
            a.account_type,
            t.trade_date,
            t.settle_date,
            t.activity,
            i.symbol,
            i.asset_type,
            i.put_call,
            i.strike,
            i.expiry,
            i.option_root,
            t.quantity,
            t.price,
            t.amount,
            t.currency,
            t.commission,
            t.source_file,
            t.statement_id
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        LEFT JOIN instruments i ON i.id = t.instrument_id
        {where}
        ORDER BY t.{sort_by} {sort_dir.upper()}
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()

    return [dict(row) for row in rows]


def _build_filters(
    account_id: str | None,
    symbol: str | None,
    asset_type: str | None,
    activity: str | None,
    date_from: str | None,
    date_to: str | None,
) -> tuple[str, list]:
    filters = []
    params: list = []
    if account_id:
        filters.append("a.account_id = ?")
        params.append(account_id)
    if symbol:
        filters.append("i.symbol LIKE ?")
        params.append(f"%{symbol}%")
    if asset_type:
        filters.append("i.asset_type = ?")
        params.append(asset_type)
    if activity:
        filters.append("t.activity = ?")
        params.append(activity)
    if date_from:
        filters.append("t.trade_date >= ?")
        params.append(date_from)
    if date_to:
        filters.append("t.trade_date <= ?")
        params.append(date_to)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    return where, params


@router.get("/count")
def count_trades(
    conn: Annotated[sqlite3.Connection, Depends(get_sqlite)],
    currency: Literal["CAD", "USD"] = "CAD",
    account_id: str | None = None,
    symbol: str | None = None,
    asset_type: str | None = None,
    activity: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """Return count of trades matching filters."""
    where, params = _build_filters(account_id, symbol, asset_type, activity, date_from, date_to)
    row = conn.execute(
        f"""
        SELECT COUNT(*) as count
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        LEFT JOIN instruments i ON i.id = t.instrument_id
        {where}
        """,
        params,
    ).fetchone()
    return {"count": row["count"]}
