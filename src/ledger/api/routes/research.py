"""GET /research — per-ticker price + my trade markers + financials."""
from __future__ import annotations

import duckdb
from fastapi import APIRouter, Query

from ...config import DUCKDB_PATH
from ...db import sqlite as sqlite_db
from ...ticker_changes import TickerSegment, ticker_segments

router = APIRouter(prefix="/research", tags=["research"])


def _duck() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DUCKDB_PATH), read_only=True)


def _segments(symbol: str) -> list[TickerSegment]:
    with sqlite_db.session() as conn:
        rows = ticker_segments(conn, symbol.upper())
    return rows


def _metadata(requested: str, segments: list[TickerSegment]) -> dict:
    symbols = list(dict.fromkeys(segment.symbol for segment in segments)) or [requested]
    return {
        "requested_symbol": requested,
        "symbol": symbols[-1],
        "symbols": symbols,
        "ticker_changes": [
            {
                "from_symbol": segments[index].symbol,
                "to_symbol": segments[index + 1].symbol,
                "effective_date": segments[index].valid_to,
            }
            for index in range(len(segments) - 1)
        ],
    }


@router.get("/prices")
def prices(symbol: str = Query(...), start: str | None = None,
           end: str | None = None, freq: str = Query("D", pattern="^[DWM]$")) -> dict:
    sym = symbol.upper()
    segments = _segments(sym)
    if not segments:
        segments = [TickerSegment(0, "", sym, None, None)]
    alternatives: list[str] = []
    params: list = []
    for segment in segments:
        conditions = ["symbol = ?"]
        values: list = [segment.symbol]
        if segment.valid_from:
            conditions.append("trade_date >= ?")
            values.append(segment.valid_from)
        if segment.valid_to:
            conditions.append("trade_date < ?")
            values.append(segment.valid_to)
        alternatives.append("(" + " AND ".join(conditions) + ")")
        params.extend(values)
    where = ["(" + " OR ".join(alternatives) + ")"]
    if start:
        where.append("trade_date >= ?")
        params.append(start)
    if end:
        where.append("trade_date <= ?")
        params.append(end)
    sql = ("SELECT trade_date, symbol AS source_symbol, open, high, low, close, adj_close, volume "
           "FROM daily_prices WHERE " + " AND ".join(where) + " ORDER BY trade_date")
    con = _duck()
    try:
        df = con.execute(sql, params).df()
    finally:
        con.close()
    if df.empty:
        return {**_metadata(sym, segments), "freq": freq, "rows": []}

    if freq != "D":
        df["trade_date"] = pandas_to_datetime(df["trade_date"])
        rule = "W" if freq == "W" else "MS"
        df = (df.set_index("trade_date")
                .resample(rule)
                .agg({"source_symbol": "last", "open": "first", "high": "max", "low": "min",
                      "close": "last", "adj_close": "last", "volume": "sum"})
                .dropna(how="all")
                .reset_index())
    df["trade_date"] = df["trade_date"].astype(str)
    return {**_metadata(sym, segments), "freq": freq, "rows": df.to_dict(orient="records")}


def pandas_to_datetime(s):  # tiny indirection so import is lazy
    import pandas as pd
    return pd.to_datetime(s)


@router.get("/trades")
def trades(symbol: str = Query(...)) -> dict:
    """Return MY transactions for a symbol — to overlay as markers."""
    with sqlite_db.session() as conn:
        segments = ticker_segments(conn, symbol.upper())
        ids = [segment.instrument_id for segment in segments]
        if not ids:
            return {**_metadata(symbol.upper(), []), "rows": []}
        placeholders = ",".join("?" * len(ids))
        rows = [dict(r) for r in conn.execute(
            f"""SELECT t.trade_date, t.txn_type, t.quantity, t.price,
                      t.net_amount, t.currency, t.description,
                      a.account_number, ins.code AS institution_code,
                      COALESCE(inst.option_root, inst.symbol) AS symbol,
                      inst.option_type, inst.option_strike, inst.option_expiry
                 FROM transactions t
                 JOIN instruments inst ON inst.instrument_id = t.instrument_id
                 JOIN accounts a ON a.account_id = t.account_id
                 JOIN institutions ins ON ins.institution_id = a.institution_id
                WHERE inst.instrument_id IN ({placeholders})
                   OR inst.option_root IN ({','.join('?' * len(segments))})
             ORDER BY t.trade_date""",
            (*ids, *(segment.symbol for segment in segments)),
        ).fetchall()]
    return {**_metadata(symbol.upper(), segments), "rows": rows}


@router.get("/financials")
def financials(symbol: str = Query(...), period: str = Query("quarterly",
               pattern="^(quarterly|annual)$")) -> dict:
    table = "financials_quarterly" if period == "quarterly" else "financials_annual"
    segments = _segments(symbol.upper())
    symbols = list(dict.fromkeys(segment.symbol for segment in segments)) or [symbol.upper()]
    placeholders = ",".join("?" * len(symbols))
    con = _duck()
    try:
        df = con.execute(
            f"SELECT * FROM {table} WHERE symbol IN ({placeholders}) ORDER BY period_end, symbol",
            symbols,
        ).df()
    finally:
        con.close()
    if df.empty:
        return {**_metadata(symbol.upper(), segments), "period": period, "rows": []}
    rank = {value: index for index, value in enumerate(symbols)}
    df["_ticker_rank"] = df["symbol"].map(rank)
    df = df.sort_values(["period_end", "_ticker_rank"]).drop_duplicates(
        subset=["period_end"], keep="last"
    ).drop(columns=["_ticker_rank"])
    df["period_end"] = df["period_end"].astype(str)
    return {**_metadata(symbol.upper(), segments), "period": period,
            "rows": df.to_dict(orient="records")}
