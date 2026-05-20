"""GET /viz — sector rotation, treemap, correlation matrix data feeds."""
from __future__ import annotations

import duckdb
from fastapi import APIRouter, Query
from tenacity import retry, stop_after_attempt, wait_fixed

from ...config import DUCKDB_PATH
from ...db import sqlite as sqlite_db
from .monthly import _holdings_at

router = APIRouter(prefix="/viz", tags=["viz"])


@retry(stop=stop_after_attempt(3), wait=wait_fixed(0.25))
def _duck() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(DUCKDB_PATH), read_only=True)


def _symbol_profiles(symbols: list[str]) -> dict[str, dict[str, str | None]]:
    if not symbols:
        return {}
    con = _duck()
    try:
        ph = ",".join(["?"] * len(symbols))
        rows = con.execute(
            f"SELECT symbol, sector, industry FROM symbol_profiles WHERE symbol IN ({ph})",
            symbols,
        ).fetchall()
    except Exception:
        return {}
    finally:
        con.close()
    return {r[0]: {"sector": r[1], "industry": r[2]} for r in rows}


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


def _held_symbols_at(as_of: str, account_ids: list[int]) -> list[str]:
    rows = _holdings_at(as_of, account_ids)
    symbols = {
        r["symbol"]
        for r in rows
        if r["symbol"] and r["asset_type"] in {"equity", "etf"} and abs(r["quantity"] or 0.0) > 1e-9
    }
    return sorted(symbols)


@router.get("/holdings_by_sector")
def holdings_by_sector(
    month_end: str | None = Query(None, description="ISO date; defaults to latest"),
    account_id: str | None = Query(None),
) -> dict:
    accts = _csv_ints(account_id)
    as_of = _resolve_as_of(month_end)
    if not as_of:
        return {"as_of_date": None, "rows": []}
    grouped: dict[tuple[str, str, str], float] = {}
    for row in _holdings_at(as_of, accts):
        if row["asset_type"] not in {"equity", "etf", "mutual_fund", "bond"}:
            continue
        market_value = row["market_value"] or 0.0
        if market_value <= 0:
            continue
        key = (row["symbol"], row["asset_type"], row["currency"])
        grouped[key] = grouped.get(key, 0.0) + market_value
    rows = [
        {"symbol": symbol, "asset_type": asset_type, "currency": currency, "market_value": market_value}
        for (symbol, asset_type, currency), market_value in sorted(grouped.items())
        if market_value > 0
    ]
    profiles = _symbol_profiles([r["symbol"] for r in rows])
    for r in rows:
        profile = profiles.get(r["symbol"], {})
        r["sector"] = profile.get("sector")
        r["industry"] = profile.get("industry")
    return {"as_of_date": as_of, "rows": rows}


@router.get("/correlation")
def correlation(
    start: str = Query(...),
    end: str = Query(...),
    account_id: str | None = Query(None),
) -> dict:
    """Pairwise correlation of daily returns over [start, end] for held symbols."""
    accts = _csv_ints(account_id)
    symbols = _held_symbols_at(end, accts)
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
    corr_symbols = list(corr.columns)
    return {"symbols": corr_symbols, "matrix": corr.values.tolist(),
            "profiles": _symbol_profiles(corr_symbols)}


@router.get("/rrg")
def rrg(
    benchmark: str = Query("SPY"),
    window_days: int = 60,
    start: str | None = None,
    end: str | None = None,
    account_id: str | None = None,
) -> dict:
    accts = _csv_ints(account_id)
    as_of = _resolve_as_of(end)
    symbols = _held_symbols_at(as_of, accts) if as_of else []
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
            sql += " AND trade_date >= ?"
            params.append(start)
        if end:
            sql += " AND trade_date <= ?"
            params.append(end)
        df = con.execute(sql, params).df()
    finally:
        con.close()
    if df.empty:
        return {"frames": []}
    profiles = _symbol_profiles(symbols)
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
            items.append({"symbol": sym, "x": float(row[sym]), "y": float(mom[sym]),
                          "sector": profiles.get(sym, {}).get("sector")})
        if items:
            frames.append({"date": date_.date().isoformat(), "points": items})
    return {"benchmark": benchmark, "window_days": window_days, "frames": frames}
