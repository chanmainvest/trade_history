"""Fetch OHLCV price data via yfinance and store in DuckDB."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)


def refresh_prices(
    duckdb_path: Path,
    tickers: list[str],
    start_date: date | None = None,
    end_date: date | None = None,
) -> None:
    """Download OHLCV for each ticker and upsert into DuckDB."""
    import yfinance as yf

    from trade_history.db.duckdb import get_connection, upsert_ohlcv

    if not tickers:
        return

    conn = get_connection(duckdb_path)
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=365 * 10))

    log.info("Fetching prices for %d tickers (%s → %s)", len(tickers), start, end)

    # Batch download
    data = yf.download(
        tickers,
        start=start.isoformat(),
        end=end.isoformat(),
        group_by="ticker",
        auto_adjust=True,
        progress=False,
    )

    rows = []
    for ticker in tickers:
        try:
            if len(tickers) == 1:
                df = data
            else:
                df = data[ticker]
            df = df.dropna(subset=["Close"])
            for idx, row in df.iterrows():
                # Detect currency: Canadian tickers end in .TO or .V
                currency = "CAD" if ticker.endswith((".TO", ".V")) else "USD"
                rows.append(
                    {
                        "ticker": ticker,
                        "date": str(idx.date()),
                        "open": float(row.get("Open", row["Close"])),
                        "high": float(row.get("High", row["Close"])),
                        "low": float(row.get("Low", row["Close"])),
                        "close": float(row["Close"]),
                        "volume": int(row.get("Volume", 0)),
                        "currency": currency,
                    }
                )
        except Exception as exc:
            log.warning("Failed to get data for %s: %s", ticker, exc)

    if rows:
        upsert_ohlcv(conn, rows)
        log.info("Stored %d OHLCV rows", len(rows))
    conn.close()


def get_tickers_from_db(sqlite_path: Path) -> list[str]:
    """Return distinct equity symbols from instruments table."""
    import sqlite3

    conn = sqlite3.connect(str(sqlite_path))
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM instruments WHERE asset_type IN ('equity', 'etf')"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]
