"""Market data scraper.

Fetches price + corporate actions + quarterly/annual financials via yfinance
into DuckDB. Rate-limited and retried with tenacity.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from ..config import DATA_DIR, DUCKDB_PATH
from ..db import duckdb_store
from ..db import sqlite as sqlite_db
from ..domains import utc_now_text
from ..logging_setup import get_logger, jsonl_path

log = get_logger("market_scrape")


@dataclass(frozen=True)
class MarketTarget:
    ledger_symbol: str
    provider_symbol: str
    currency: str
    exchange: str | None
    instrument_id: int | None = None


def _held_symbols(path: Path | str | None = None) -> list[MarketTarget]:
    """Return referenced listings with an explicit Yahoo provider symbol."""
    with sqlite_db.session(path if path is not None else sqlite_db.SQLITE_PATH) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT i.instrument_id, i.symbol, i.currency, i.exchange,
                   market.provider_symbol
              FROM instruments i
              JOIN instrument_market_symbols market
                ON market.instrument_id = i.instrument_id
               AND market.provider = 'yahoo'
               AND market.status IN ('candidate','verified','failed')
             WHERE i.asset_type IN ('equity','etf')
               AND (
                    EXISTS (SELECT 1 FROM transactions t WHERE t.instrument_id = i.instrument_id)
                 OR EXISTS (SELECT 1 FROM position_snapshots p WHERE p.instrument_id = i.instrument_id)
                 OR EXISTS (SELECT 1 FROM initial_positions initial WHERE initial.instrument_id = i.instrument_id)
               )
             ORDER BY market.provider_symbol, i.instrument_id
            """
        ).fetchall()
    return [
        MarketTarget(
            ledger_symbol=str(row["symbol"]),
            provider_symbol=str(row["provider_symbol"]),
            currency=str(row["currency"]),
            exchange=row["exchange"],
            instrument_id=int(row["instrument_id"]),
        )
        for row in rows
    ]


def _yf_symbol(symbol: str, currency: str, exchange: str | None = None) -> str:
    """Map our internal symbol to a yfinance-compatible ticker."""
    symbol_upper = symbol.upper()
    if symbol_upper.endswith(".TO"):
        return symbol_upper
    yfinance_symbol = symbol_upper.replace(".", "-")
    exchange_upper = (exchange or "").upper()
    if exchange_upper in {"TSX", "TSXV", "TSX-V", "NEO", "CSE"} and "." not in symbol_upper:
        return f"{yfinance_symbol}.TO"
    return yfinance_symbol


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def _fetch_history(yfsym: str, start: str) -> pd.DataFrame:
    import yfinance as yf
    yf.set_tz_cache_location(str(DATA_DIR / "yfinance_cache"))
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
        targets: list[MarketTarget]
        if symbols:
            targets = [MarketTarget(s, s, "USD", None) for s in symbols]
        else:
            targets = _held_symbols()
        start = (datetime.utcnow() - timedelta(days=365 * lookback_years)).date().isoformat()

        for target in targets:
            price_symbol = target.provider_symbol
            log.info(
                "Fetching %s for %s (%s)",
                price_symbol,
                target.ledger_symbol,
                target.currency,
            )
            try:
                df = _fetch_history(price_symbol, start)
            except Exception as e:
                log.warning("fetch failed for %s: %s", price_symbol, e)
                _record_market_status(target, "failed", str(e))
                jsonl.write(json.dumps({"symbol": target.ledger_symbol, "yf": price_symbol,
                                        "status": "fail",
                                        "err": str(e)}) + "\n")
                continue
            if df is None or df.empty:
                _record_market_status(target, "failed", "Yahoo returned no price history")
                jsonl.write(json.dumps({"symbol": target.ledger_symbol, "yf": price_symbol,
                                        "status": "empty"}) + "\n")
                time.sleep(sleep_s)
                continue
            df = df.reset_index().rename(columns={
                "Date": "trade_date", "Open": "open", "High": "high",
                "Low": "low", "Close": "close", "Adj Close": "adj_close",
                "Volume": "volume",
            })
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
            df["symbol"] = price_symbol
            df["currency"] = target.currency
            df["exchange"] = target.exchange
            df = df[["symbol", "exchange", "currency", "trade_date", "open",
                     "high", "low", "close", "adj_close", "volume"]]
            con.execute("DELETE FROM daily_prices WHERE symbol = ?", [price_symbol])
            con.register("df", df)
            con.execute("INSERT INTO daily_prices SELECT * FROM df")
            con.unregister("df")
            _record_market_status(target, "verified", None)
            jsonl.write(json.dumps({"symbol": target.ledger_symbol, "yf": price_symbol,
                                    "status": "ok",
                                    "rows": int(len(df))}) + "\n")
            time.sleep(sleep_s)
    finally:
        jsonl.close()
        con.close()


def _record_market_status(target: MarketTarget, status: str, error: str | None) -> None:
    if target.instrument_id is None:
        return
    now = utc_now_text()
    with sqlite_db.session() as conn:
        conn.execute(
            """
            UPDATE instrument_market_symbols
               SET status = ?, last_checked_at = ?,
                   verified_at = CASE WHEN ? = 'verified' THEN ? ELSE verified_at END,
                   last_error = ?
             WHERE instrument_id = ? AND provider = 'yahoo'
            """,
            (
                status,
                now,
                status,
                now,
                error[:500] if error else None,
                target.instrument_id,
            ),
        )
