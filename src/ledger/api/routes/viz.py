"""GET /viz — sector rotation, treemap, correlation matrix data feeds."""
from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import duckdb
from fastapi import APIRouter, Query
from tenacity import retry, stop_after_attempt, wait_fixed

from ...config import DUCKDB_PATH
from ...holdings import holdings_at, latest_holdings_date

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
            f"SELECT p.symbol, p.short_name, p.sector, p.industry, p.quote_type, "
            f"p.market_cap, c.category "
            f"FROM symbol_profiles p "
            f"LEFT JOIN fund_categories c ON c.symbol = p.symbol "
            f"WHERE p.symbol IN ({ph})",
            symbols,
        ).fetchall()
    except Exception:
        return {}
    finally:
        con.close()
    return {r[0]: {"short_name": r[1], "sector": r[2], "industry": r[3],
                   "quote_type": r[4], "market_cap": r[5], "category": r[6]}
            for r in rows}


def _display_sector(profile: dict, asset_type: str | None = None) -> str | None:
    """Yahoo publishes no sector/industry for funds. Fall back to the fund
    category, then a generic ETF label, so ETFs stop landing in Unknown."""
    sector = profile.get("sector")
    if sector:
        return sector
    if profile.get("quote_type") == "ETF" or asset_type == "etf":
        return profile.get("category") or "ETF"
    return None


def _csv_list(v: str | None) -> list[str]:
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


def _csv_ints(v: str | None) -> list[int]:
    return [int(x) for x in _csv_list(v) if x.lstrip("-").isdigit()]


def _resolve_as_of(month_end: str | None) -> str | None:
    """Resolve against complete holdings checkpoints only."""
    return latest_holdings_date(month_end)


def _held_symbols_at(
    as_of: str,
    account_ids: list[int],
    path: Path | str | None = None,
) -> list[str]:
    rows = holdings_at(as_of, account_ids, path=path)
    symbols = {
        r.get("market_symbol") or r["symbol"]
        for r in rows
        if r["symbol"] and r["asset_type"] in {"equity", "etf"} and abs(r["quantity"] or 0.0) > 1e-9
    }
    return sorted(symbols)


def _period_start(as_of: str, period: str) -> str:
    end = date.fromisoformat(as_of)
    match period:
        case "1d":
            days = 1
        case "1w":
            days = 7
        case "1m":
            days = 30
        case "3m":
            days = 90
        case "6m":
            days = 180
        case "1y":
            days = 365
        case _:
            return date(end.year, 1, 1).isoformat()
    return (end - timedelta(days=days)).isoformat()


def _symbol_performance(symbols: list[str], as_of: str, period: str) -> dict[str, float | None]:
    if not symbols:
        return {}
    start = _period_start(as_of, period)
    out: dict[str, float | None] = {}
    con = _duck()
    try:
        for symbol in symbols:
            end_row = con.execute(
                """
                SELECT adj_close FROM daily_prices
                 WHERE symbol = ? AND trade_date <= ?
                   AND adj_close IS NOT NULL
                 ORDER BY trade_date DESC
                 LIMIT 1
                """,
                [symbol, as_of],
            ).fetchone()
            start_row = con.execute(
                """
                SELECT adj_close FROM daily_prices
                 WHERE symbol = ? AND trade_date <= ?
                   AND adj_close IS NOT NULL
                 ORDER BY trade_date DESC
                 LIMIT 1
                """,
                [symbol, start],
            ).fetchone()
            if not end_row or not start_row or not start_row[0]:
                out[symbol] = None
            else:
                out[symbol] = (float(end_row[0]) / float(start_row[0]) - 1.0) * 100.0
    except Exception:
        return {symbol: None for symbol in symbols}
    finally:
        con.close()
    return out


def _price_data_through() -> str | None:
    """Latest trade date present in daily_prices, for freshness badges."""
    con = _duck()
    try:
        row = con.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
    except Exception:
        return None
    finally:
        con.close()
    return str(row[0]) if row and row[0] else None


def _usd_cad_rate(con: duckdb.DuckDBPyConnection, as_of: date) -> tuple[float, str] | None:
    """Latest USD/CAD rate on or before as_of, tolerating either stored pair direction.

    FX conversion is presentation-only; statement amounts stay native.
    """
    row = con.execute(
        "SELECT base, quote, rate, rate_date FROM fx_rates "
        "WHERE rate_date <= ? AND ((base = 'USD' AND quote = 'CAD') "
        "OR (base = 'CAD' AND quote = 'USD')) "
        "ORDER BY rate_date DESC LIMIT 1",
        [as_of],
    ).fetchone()
    if not row:
        return None
    base, rate, rate_date = str(row[0]), float(row[2]), str(row[3])
    usd_cad = rate if base == "USD" else 1.0 / rate
    return usd_cad, rate_date


@router.get("/holdings_by_sector")
def holdings_by_sector(
    month_end: Annotated[
        date | None, Query(description="ISO date; defaults to latest")
    ] = None,
    account_id: str | None = Query(None),
    period: str = Query("1m", pattern="^(1d|1w|1m|3m|6m|1y|ytd)$"),
    currency: Annotated[str | None, Query(pattern="^(CAD|USD)$",
                                          description="Display currency; holdings are converted "
                                                      "presentation-only via fx_rates")] = None,
) -> dict:
    currency = currency or "CAD"
    accts = _csv_ints(account_id)
    as_of = _resolve_as_of(month_end.isoformat() if month_end is not None else None)
    if not as_of:
        return {"as_of_date": None, "rows": []}
    rows = []
    for row in holdings_at(as_of, accts):
        if row["asset_type"] not in {"equity", "etf", "mutual_fund", "bond"}:
            continue
        market_value = row["market_value"] or 0.0
        if market_value <= 0:
            continue
        rows.append({
            "account_id": row["account_id"],
            "account_number": row["account_number"],
            "institution_code": row["institution_code"],
            "institution_name": row["institution_name"],
            "symbol": row["symbol"],
            "market_symbol": row.get("market_symbol") or row["symbol"],
            "asset_type": row["asset_type"],
            "currency": row["currency"],
            "market_value": market_value,
        })
    profiles = _symbol_profiles([r["market_symbol"] for r in rows])
    performance = _symbol_performance([r["market_symbol"] for r in rows], as_of, period)
    for r in rows:
        profile = profiles.get(r["market_symbol"], {})
        r["sector"] = _display_sector(profile, r["asset_type"])
        r["industry"] = profile.get("industry")
        r["performance_pct"] = performance.get(r["market_symbol"])
    fx: dict | None = None
    unconverted: set[str] = set()
    if any(r["currency"] != currency for r in rows):
        con = _duck()
        try:
            rate = _usd_cad_rate(con, as_of)
        finally:
            con.close()
        if rate:
            usd_cad, rate_date = rate
            fx = {"usd_cad": usd_cad, "rate_date": rate_date}
    for r in rows:
        if r["currency"] == currency:
            continue
        if not fx:
            unconverted.add(r["currency"])
            continue
        r["market_value"] = (r["market_value"] * fx["usd_cad"]
                             if r["currency"] == "USD" else r["market_value"] / fx["usd_cad"])
        r["currency"] = currency
    rows.sort(key=lambda r: (r["institution_code"], r["account_number"], r["symbol"], r["currency"]))
    return {"as_of_date": as_of, "period": period, "currency": currency,
            "fx": fx,
            "unconverted_currencies": sorted(unconverted),
            "price_data_through": _price_data_through(), "rows": rows}


_ASSET_SPARK_WINDOWS = [("1w", 7), ("1mo", 30), ("3mo", 90), ("1y", 365), ("3y", 366 * 3)]
_SPARK_POINTS = 24


def _downsample(values: list[float], n: int) -> list[float]:
    if len(values) <= n:
        return values
    step = (len(values) - 1) / (n - 1)
    idx = sorted({min(len(values) - 1, round(i * step)) for i in range(n)})
    return [values[i] for i in idx]


def _historical_vol(closes: list[float]) -> float | None:
    """Annualized 30-day realized volatility (percent) from daily adj closes."""
    window = closes[-31:]
    if len(window) < 6:
        return None
    rets = [b / a - 1.0 for a, b in zip(window, window[1:], strict=False) if a]
    if len(rets) < 5:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return (var ** 0.5) * (252 ** 0.5) * 100.0


@router.get("/assets")
def assets(
    month_end: Annotated[
        date | None, Query(description="ISO date; defaults to latest")
    ] = None,
    account_id: str | None = Query(None),
) -> dict:
    """One row per held asset at the selected date: price, market cap,
    realized (historical) volatility, option-implied IV, and per-window
    sparkline series (1w/1mo/3mo/1y/3y) with the window's % change."""
    accts = _csv_ints(account_id)
    as_of = _resolve_as_of(month_end.isoformat() if month_end is not None else None)
    if not as_of:
        return {"as_of_date": None, "rows": []}
    # Holdings resolve to the latest complete checkpoint on or before the
    # selected date, but prices/IV/vol windows clamp to the selected date
    # itself so the default view shows the freshest market data.
    end_text = month_end.isoformat() if month_end is not None else date.today().isoformat()

    agg: dict[str, dict] = {}
    broker_prices: dict[str, tuple[float, str | None]] = {}
    for row in holdings_at(as_of, accts):
        if row["asset_type"] not in {"equity", "etf", "mutual_fund", "bond"}:
            continue
        sym = row.get("market_symbol") or row["symbol"]
        entry = agg.setdefault(sym, {
            "symbol": row["symbol"],
            "market_symbol": sym,
            "asset_type": row["asset_type"],
            "currency": row["currency"],
            "quantity": 0.0,
            "market_value": 0.0,
            "accounts": [],
        })
        entry["quantity"] += row["quantity"] or 0.0
        entry["market_value"] += row["market_value"] or 0.0
        reported_price = row.get("market_price")
        if (sym not in broker_prices and isinstance(reported_price, (int, float))
                and math.isfinite(float(reported_price))):
            broker_prices[sym] = (float(reported_price), row.get("price_date") or row.get("checkpoint_date"))
        label = f'{row["institution_code"]}•{row["account_number"]}'
        if label not in entry["accounts"]:
            entry["accounts"].append(label)
    if not agg:
        return {"as_of_date": as_of, "rows": [], "price_data_through": _price_data_through()}

    symbols = sorted(agg)
    profiles = _symbol_profiles(symbols)

    con = _duck()
    try:
        end = date.fromisoformat(end_text)
        window_start = (end - timedelta(days=_ASSET_SPARK_WINDOWS[-1][1] + 14)).isoformat()
        ph = ",".join(["?"] * len(symbols))
        price_rows = con.execute(
            f"SELECT symbol, trade_date, close, adj_close FROM daily_prices "
            f"WHERE symbol IN ({ph}) AND trade_date <= ? AND trade_date >= ? "
            f"ORDER BY symbol, trade_date",
            [*symbols, end_text, window_start],
        ).fetchall()
        iv_rows = con.execute(
            f"SELECT symbol, iv_30d, trade_date FROM option_implied_vol "
            f"WHERE symbol IN ({ph}) AND trade_date <= ?",
            [*symbols, end_text],
        ).fetchall()
    finally:
        con.close()

    series: dict[str, list[tuple]] = {}
    for symbol, trade_date, close, adj_close in price_rows:
        series.setdefault(symbol, []).append((trade_date, close, adj_close))
    iv_at: dict[str, tuple] = {}
    for symbol, iv, trade_date in iv_rows:
        if symbol not in iv_at or str(iv_at[symbol][1]) < str(trade_date):
            iv_at[symbol] = (iv, trade_date)

    rows = []
    for sym in symbols:
        entry = agg[sym]
        points = series.get(sym, [])
        # Yahoo rows can end in an all-NULL placeholder (unfilled session);
        # the quoted price is the latest row that actually carries a value.
        priced = next((p for p in reversed(points) if p[1] is not None or p[2] is not None), None)
        price = priced[1] if priced else None
        if price is None and priced is not None:
            price = priced[2]
        price_date = str(priced[0]) if priced else None
        adj = [a if a is not None else c for _, c, a in points if a is not None or c is not None]
        sparks = {}
        for label, days in _ASSET_SPARK_WINDOWS:
            # Values inside the window; the % change spans the visible
            # sparkline (first→last available adjusted close in the window).
            win_end = end - timedelta(days=days)
            vals = [(a if a is not None else c)
                    for d, c, a in points if d > win_end and (a is not None or c is not None)]
            pct = None
            if len(vals) >= 2 and vals[0]:
                pct = (vals[-1] / vals[0] - 1.0) * 100.0
            sparks[label] = {
                "points": [round(v, 4) for v in _downsample(vals, _SPARK_POINTS)],
                "pct": pct,
            }
        profile = profiles.get(sym, {})
        # No Yahoo listing (bank fund codes, delisted names): fall back to the
        # broker's checkpoint unit value (printed market_value ÷ quantity),
        # flagged so the UI can distinguish it from a quoted price.
        price_source = "yahoo" if price is not None else None
        if price is None and sym in broker_prices:
            price, price_date = broker_prices[sym]
            price_source = "broker"
        if price is None and entry["quantity"]:
            price = entry["market_value"] / entry["quantity"]
            price_source = "broker" if price else None
        rows.append({
            **entry,
            "name": profile.get("short_name"),
            "price": price,
            "price_source": price_source,
            "price_date": price_date,
            "market_cap": profile.get("market_cap"),
            "hv_30d": _historical_vol(adj),
            "iv": iv_at.get(sym, (None, None))[0],
            "iv_date": str(iv_at[sym][1]) if sym in iv_at else None,
            "sparks": sparks,
        })
    rows.sort(key=lambda r: r["market_value"], reverse=True)
    return {"as_of_date": as_of, "rows": rows, "price_data_through": _price_data_through()}


@router.get("/correlation")
def correlation(
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
    account_id: str | None = Query(None),
) -> dict:
    """Pairwise correlation of daily returns over [start, end] for held symbols."""
    accts = _csv_ints(account_id)
    start_text, end_text = start.isoformat(), end.isoformat()
    symbols = _held_symbols_at(end_text, accts)
    if not symbols:
        return {"symbols": [], "matrix": []}
    con = _duck()
    try:
        placeholders = ",".join(["?"] * len(symbols))
        df = con.execute(
            f"SELECT symbol, trade_date, adj_close FROM daily_prices "
            f"WHERE symbol IN ({placeholders}) AND trade_date BETWEEN ? AND ?",
            [*symbols, start_text, end_text],
        ).df()
    finally:
        con.close()
    if df.empty:
        return {"symbols": symbols, "matrix": []}
    p = df.pivot(index="trade_date", columns="symbol", values="adj_close").pct_change()
    corr = p.corr().fillna(0.0)
    corr_symbols = list(corr.columns)
    raw_profiles = _symbol_profiles(corr_symbols)
    profiles = {sym: {**profile, "sector": _display_sector(profile)}
                for sym, profile in raw_profiles.items()}
    return {"symbols": corr_symbols, "matrix": corr.values.tolist(),
            "profiles": profiles}


@router.get("/rrg")
def rrg(
    benchmark: str = Query("SPY"),
    window_days: int = 60,
    start: date | None = None,
    end: date | None = None,
    account_id: str | None = None,
) -> dict:
    accts = _csv_ints(account_id)
    start_text = start.isoformat() if start is not None else None
    end_text = end.isoformat() if end is not None else None
    as_of = _resolve_as_of(end_text)
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
        if start_text:
            sql += " AND trade_date >= ?"
            params.append(start_text)
        if end_text:
            sql += " AND trade_date <= ?"
            params.append(end_text)
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
                          "sector": _display_sector(profiles.get(sym, {}))})
        if items:
            frames.append({"date": date_.date().isoformat(), "points": items})
    return {"benchmark": benchmark, "window_days": window_days, "frames": frames}
