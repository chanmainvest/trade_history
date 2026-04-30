"""Market data scraper.

Fetches price + corporate actions + quarterly/annual financials via yfinance
into DuckDB. Rate-limited and retried with tenacity.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import duckdb
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import DUCKDB_PATH
from ..db import duckdb_store, sqlite as sqlite_db
from ..logging_setup import get_logger, jsonl_path

log = get_logger("market_scrape")


def _held_symbols() -> list[tuple[str, str]]:
    """Return [(symbol, currency)] for every instrument we've ever held."""
    with sqlite_db.session() as conn:
        rows = conn.execute(
            "SELECT DISTINCT symbol, currency FROM instruments "
            "WHERE asset_type IN ('equity','etf') ORDER BY symbol"
        ).fetchall()
    return [(r["symbol"], r["currency"]) for r in rows]


def _yf_symbol(symbol: str, currency: str) -> str:
    """Map our internal symbol to a yfinance-compatible ticker."""
    s = symbol.upper().replace(".", "-")
    if currency == "CAD" and not s.endswith(".TO") and ".TO" not in symbol:
        # Canadian listings on yfinance are .TO. Caller may have set it already.
        return f"{s}.TO" if "." not in symbol else symbol.upper()
    return symbol.upper()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _fetch_history(yfsym: str, start: str) -> pd.DataFrame:
    import yfinance as yf
    t = yf.Ticker(yfsym)
    df = t.history(start=start, interval="1d", auto_adjust=False)
    return df


def refresh_market_data(*, symbols: list[str] | None = None,
                        lookback_years: int = 15,
                        sleep_s: float = 1.5) -> None:
    duckdb_store.init_db()
    con = duckdb.connect(str(DUCKDB_PATH))
    jsonl = jsonl_path("market_scrape").open("a", encoding="utf-8")
    try:
        targets: list[tuple[str, str]]
        if symbols:
            targets = [(s, "USD") for s in symbols]
        else:
            targets = _held_symbols()
        start = (datetime.utcnow() - timedelta(days=365 * lookback_years)).date().isoformat()

        for sym, ccy in targets:
            yfsym = _yf_symbol(sym, ccy)
            log.info("Fetching %s (%s)", yfsym, ccy)
            try:
                df = _fetch_history(yfsym, start)
            except Exception as e:
                log.warning("fetch failed for %s: %s", yfsym, e)
                jsonl.write(json.dumps({"symbol": sym, "yf": yfsym,
                                        "status": "fail",
                                        "err": str(e)}) + "\n")
                continue
            if df is None or df.empty:
                jsonl.write(json.dumps({"symbol": sym, "yf": yfsym,
                                        "status": "empty"}) + "\n")
                time.sleep(sleep_s)
                continue
            df = df.reset_index().rename(columns={
                "Date": "trade_date", "Open": "open", "High": "high",
                "Low": "low", "Close": "close", "Adj Close": "adj_close",
                "Volume": "volume",
            })
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
            df["symbol"] = sym
            df["currency"] = ccy
            df["exchange"] = None
            df = df[["symbol", "exchange", "currency", "trade_date", "open",
                     "high", "low", "close", "adj_close", "volume"]]
            con.execute("DELETE FROM daily_prices WHERE symbol = ?", [sym])
            con.register("df", df)
            con.execute("INSERT INTO daily_prices SELECT * FROM df")
            con.unregister("df")
            jsonl.write(json.dumps({"symbol": sym, "yf": yfsym,
                                    "status": "ok",
                                    "rows": int(len(df))}) + "\n")
            time.sleep(sleep_s)
    finally:
        jsonl.close()
        con.close()
