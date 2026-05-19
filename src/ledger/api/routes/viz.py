"""GET /viz — sector rotation, treemap, correlation matrix data feeds."""
from __future__ import annotations

import duckdb
from fastapi import APIRouter, Query

from ...config import DUCKDB_PATH
from ...db import sqlite as sqlite_db

router = APIRouter(prefix="/viz", tags=["viz"])


def _duck() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DUCKDB_PATH), read_only=True)


def _csv_list(v: str | None) -> list[str]:
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


def _csv_ints(v: str | None) -> list[int]:
    return [int(x) for x in _csv_list(v) if x.lstrip("-").isdigit()]


def _resolve_as_of(month_end: str | None) -> str | None:
    """If ``month_end`` is None, falls back to the latest snapshot date in
    the DB. If a specific date is given but nothing exists on/before it,
    returns None so the caller can render an empty state.
    """
    with sqlite_db.session() as conn:
        if month_end:
            row = conn.execute(
                "SELECT MAX(as_of_date) FROM position_snapshots WHERE as_of_date <= ?",
                (month_end,),
            ).fetchone()
        else:
            row = conn.execute("SELECT MAX(as_of_date) FROM position_snapshots").fetchone()
    return row[0] if row and row[0] else None


@router.get("/holdings_by_sector")
def holdings_by_sector(
    month_end: str | None = Query(None, description="ISO date; defaults to latest"),
    account_id: str | None = Query(None),
) -> dict:
    accts = _csv_ints(account_id)
    as_of = _resolve_as_of(month_end)
    if not as_of:
        return {"as_of_date": None, "rows": []}
    sql = [
        "WITH latest AS (",
        "  SELECT account_id, instrument_id, MAX(as_of_date) AS d",
        "    FROM position_snapshots",
        "   WHERE as_of_date <= ?",
    ]
    params: list = [as_of]
    if accts:
        sql.append(f"     AND account_id IN ({','.join('?' * len(accts))})")
        params.extend(accts)
    sql.extend([
        "GROUP BY account_id, instrument_id",
        ")",
        "SELECT inst.symbol, inst.asset_type, inst.currency,",
        "       SUM(ps.market_value) AS market_value",
        "  FROM position_snapshots ps",
        "  JOIN latest l ON l.account_id = ps.account_id",
        "               AND l.instrument_id = ps.instrument_id",
        "               AND l.d = ps.as_of_date",
        "  JOIN instruments inst ON inst.instrument_id = ps.instrument_id",
        " WHERE inst.asset_type IN ('equity','etf','mutual_fund','bond')",
        "   AND ps.market_value IS NOT NULL AND ps.market_value > 0",
        "GROUP BY inst.symbol, inst.asset_type, inst.currency",
        " HAVING market_value > 0",
    ])
    with sqlite_db.session() as conn:
        rows = [dict(r) for r in conn.execute("\n".join(sql), params).fetchall()]
    return {"as_of_date": as_of, "rows": rows}


@router.get("/correlation")
def correlation(
    start: str = Query(...),
    end: str = Query(...),
    account_id: str | None = Query(None),
) -> dict:
    """Pairwise correlation of daily returns over [start, end] for held symbols."""
    accts = _csv_ints(account_id)
    with sqlite_db.session() as conn:
        if accts:
            ph = ",".join("?" * len(accts))
            symbols = [r[0] for r in conn.execute(
                f"SELECT DISTINCT inst.symbol FROM instruments inst "
                f"JOIN position_snapshots ps ON ps.instrument_id = inst.instrument_id "
                f"WHERE inst.asset_type IN ('equity','etf') "
                f"  AND ps.account_id IN ({ph}) "
                f"ORDER BY inst.symbol",
                accts,
            ).fetchall()]
        else:
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
    p = df.pivot(index="trade_date", columns="symbol", values="adj_close").pct_change()
    corr = p.corr().fillna(0.0)
    return {"symbols": list(corr.columns), "matrix": corr.values.tolist()}


@router.get("/rrg")
def rrg(
    benchmark: str = Query("SPY"),
    window_days: int = 60,
    start: str | None = None,
    end: str | None = None,
    account_id: str | None = None,
) -> dict:
    accts = _csv_ints(account_id)
    with sqlite_db.session() as conn:
        if accts:
            ph = ",".join("?" * len(accts))
            symbols = [r[0] for r in conn.execute(
                f"SELECT DISTINCT inst.symbol FROM instruments inst "
                f"JOIN position_snapshots ps ON ps.instrument_id = inst.instrument_id "
                f"WHERE inst.asset_type IN ('equity','etf') AND ps.account_id IN ({ph}) "
                f"ORDER BY inst.symbol",
                accts,
            ).fetchall()]
        else:
            symbols = [r[0] for r in conn.execute(
                "SELECT DISTINCT symbol FROM instruments "
                "WHERE asset_type IN ('equity','etf') ORDER BY symbol"
            ).fetchall()]
    con = _duck()
    try:
        priced = {r[0] for r in con.execute(
            "SELECT DISTINCT symbol FROM daily_prices"
        ).fetchall()}
        symbols = [s for s in symbols if s in priced and s != benchmark]
        if benchmark not in priced or not symbols:
            return {"benchmark": benchmark, "window_days": window_days,
                    "frames": [],
                    "note": (f"benchmark {benchmark} not in daily_prices — "
                             f"run `uv run ledger market refresh-benchmarks`")
                            if benchmark not in priced else None}
        targets = symbols + [benchmark]
        import pandas as pd
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
    p = p.loc[p[benchmark].notna()]
    p = p.ffill(limit=5)
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
