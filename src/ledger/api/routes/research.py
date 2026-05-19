"""GET /research — per-ticker price + my trade markers + financials."""
from __future__ import annotations

import duckdb
from fastapi import APIRouter, Query

from ...config import DUCKDB_PATH
from ...db import sqlite as sqlite_db

router = APIRouter(prefix="/research", tags=["research"])


def _duck() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DUCKDB_PATH), read_only=True)


@router.get("/prices")
def prices(symbol: str = Query(...), start: str | None = None,
           end: str | None = None, freq: str = Query("D", pattern="^[DWM]$")) -> dict:
    sym = symbol.upper()
    where = ["symbol = ?"]
    params: list = [sym]
    if start:
        where.append("trade_date >= ?"); params.append(start)
    if end:
        where.append("trade_date <= ?"); params.append(end)
    sql = ("SELECT trade_date, open, high, low, close, adj_close, volume "
           "FROM daily_prices WHERE " + " AND ".join(where) + " ORDER BY trade_date")
    con = _duck()
    try:
        df = con.execute(sql, params).df()
    finally:
        con.close()
    if df.empty:
        return {"symbol": sym, "freq": freq, "rows": []}

    if freq != "D":
        df["trade_date"] = pandas_to_datetime(df["trade_date"])
        rule = "W" if freq == "W" else "MS"
        df = (df.set_index("trade_date")
                .resample(rule)
                .agg({"open": "first", "high": "max", "low": "min",
                      "close": "last", "adj_close": "last", "volume": "sum"})
                .dropna(how="all")
                .reset_index())
    df["trade_date"] = df["trade_date"].astype(str)
    return {"symbol": sym, "freq": freq, "rows": df.to_dict(orient="records")}


def pandas_to_datetime(s):  # tiny indirection so import is lazy
    import pandas as pd
    return pd.to_datetime(s)


@router.get("/trades")
def trades(symbol: str = Query(...)) -> dict:
    """Return MY transactions for a symbol — to overlay as markers."""
    with sqlite_db.session() as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT t.trade_date, t.txn_type, t.quantity, t.price,
                      t.net_amount, t.currency, t.description,
                      a.account_number, ins.code AS institution_code,
                      inst.option_type, inst.option_strike, inst.option_expiry
                 FROM transactions t
                 JOIN instruments inst ON inst.instrument_id = t.instrument_id
                 JOIN accounts a ON a.account_id = t.account_id
                 JOIN institutions ins ON ins.institution_id = a.institution_id
                WHERE inst.symbol = ?
             ORDER BY t.trade_date""",
            (symbol.upper(),),
        ).fetchall()]
    return {"symbol": symbol.upper(), "rows": rows}


@router.get("/financials")
def financials(symbol: str = Query(...), period: str = Query("quarterly",
               pattern="^(quarterly|annual)$")) -> dict:
    table = "financials_quarterly" if period == "quarterly" else "financials_annual"
    con = _duck()
    try:
        df = con.execute(
            f"SELECT * FROM {table} WHERE symbol = ? ORDER BY period_end",
            [symbol.upper()],
        ).df()
    finally:
        con.close()
    if df.empty:
        return {"symbol": symbol.upper(), "period": period, "rows": []}
    df["period_end"] = df["period_end"].astype(str)
    return {"symbol": symbol.upper(), "period": period,
            "rows": df.to_dict(orient="records")}
