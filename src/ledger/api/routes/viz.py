"""GET /viz — sector rotation, treemap, correlation matrix data feeds."""
from __future__ import annotations

import duckdb
from fastapi import APIRouter, Query

from ...config import DUCKDB_PATH
from ...db import sqlite as sqlite_db

router = APIRouter(prefix="/viz", tags=["viz"])


def _duck() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DUCKDB_PATH), read_only=True)


@router.get("/holdings_by_sector")
def holdings_by_sector(month_end: str = Query(...)) -> dict:
    """Treemap input: holdings + market value at month_end. Sector lookup is
    a TODO — for now we return symbol-level data and let the frontend group."""
    with sqlite_db.session() as conn:
        rows = [dict(r) for r in conn.execute(
            """
            WITH latest AS (
              SELECT instrument_id, MAX(as_of_date) AS d
                FROM position_snapshots
               WHERE as_of_date <= ?
            GROUP BY instrument_id
            )
            SELECT inst.symbol, inst.asset_type, inst.currency,
                   SUM(ps.market_value) AS market_value
              FROM position_snapshots ps
              JOIN latest l ON l.instrument_id = ps.instrument_id
                           AND l.d = ps.as_of_date
              JOIN instruments inst ON inst.instrument_id = ps.instrument_id
             WHERE inst.asset_type IN ('equity','etf')
          GROUP BY inst.symbol, inst.asset_type, inst.currency
            """, (month_end,),
        ).fetchall()]
    return {"as_of_date": month_end, "rows": rows}


@router.get("/correlation")
def correlation(start: str = Query(...), end: str = Query(...)) -> dict:
    """Pairwise correlation of daily returns over [start, end] for held symbols."""
    with sqlite_db.session() as conn:
        symbols = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM instruments "
            "WHERE asset_type IN ('equity','etf') ORDER BY symbol"
        ).fetchall()]
    if not symbols:
        return {"symbols": [], "matrix": []}
    con = _duck()
    try:
        placeholders = ",".join(["?"] * len(symbols))
        df = con.execute(
            f"SELECT symbol, trade_date, adj_close FROM daily_prices "
            f"WHERE symbol IN ({placeholders}) AND trade_date BETWEEN ? AND ?",
            [*symbols, start, end],
        ).df()
    finally:
        con.close()
    if df.empty:
        return {"symbols": symbols, "matrix": []}
    import pandas as pd
    p = df.pivot(index="trade_date", columns="symbol", values="adj_close").pct_change()
    corr = p.corr().fillna(0.0)
    return {"symbols": list(corr.columns),
            "matrix": corr.values.tolist()}


@router.get("/rrg")
def rrg(benchmark: str = Query("SPY"), window_days: int = 60,
        start: str | None = None, end: str | None = None) -> dict:
    """Compute Relative Rotation Graph coordinates per held symbol over time.

    Returns a list of frames; the frontend animates them on a date scrubber.
    Coordinates are JdK RS-Ratio (x) and JdK RS-Momentum (y) computed from
    `window_days` rolling stats vs benchmark.
    """
    with sqlite_db.session() as conn:
        symbols = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM instruments "
            "WHERE asset_type IN ('equity','etf') ORDER BY symbol"
        ).fetchall()]
    targets = sorted(set(symbols + [benchmark]))
    if not targets:
        return {"frames": []}
    import pandas as pd
    con = _duck()
    try:
        ph = ",".join(["?"] * len(targets))
        sql = (f"SELECT symbol, trade_date, adj_close FROM daily_prices "
               f"WHERE symbol IN ({ph})")
        params: list = list(targets)
        if start:
            sql += " AND trade_date >= ?"; params.append(start)
        if end:
            sql += " AND trade_date <= ?"; params.append(end)
        df = con.execute(sql, params).df()
    finally:
        con.close()
    if df.empty:
        return {"frames": []}
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    p = df.pivot(index="trade_date", columns="symbol", values="adj_close").sort_index()
    if benchmark not in p.columns:
        return {"frames": []}
    rs = p.div(p[benchmark], axis=0)
    rs_norm = 100 * rs / rs.rolling(window_days).mean()
    rs_mom = rs_norm.diff(window_days)
    frames = []
    for date_, row in rs_norm.iterrows():
        mom = rs_mom.loc[date_]
        items = []
        for sym in symbols:
            if sym == benchmark or pd.isna(row.get(sym)) or pd.isna(mom.get(sym)):
                continue
            items.append({"symbol": sym, "x": float(row[sym]), "y": float(mom[sym])})
        if items:
            frames.append({"date": date_.date().isoformat(), "points": items})
    return {"benchmark": benchmark, "window_days": window_days, "frames": frames}
