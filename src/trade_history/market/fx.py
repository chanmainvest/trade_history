"""Fetch USD/CAD FX rates via yfinance and store in DuckDB."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_FX_PAIRS = [
    ("USD", "CAD", "USDCAD=X"),
    ("CAD", "USD", "CADUSD=X"),
]


def refresh_fx_rates(
    duckdb_path: Path,
    start_date: date | None = None,
    end_date: date | None = None,
) -> None:
    """Download daily FX rates and upsert into DuckDB."""
    import yfinance as yf

    from trade_history.db.duckdb import get_connection, upsert_fx_rates

    conn = get_connection(duckdb_path)
    end = end_date or date.today()
    start = start_date or (end - timedelta(days=365 * 12))

    rows = []
    for from_ccy, to_ccy, yf_ticker in _FX_PAIRS:
        try:
            data = yf.download(
                yf_ticker,
                start=start.isoformat(),
                end=end.isoformat(),
                auto_adjust=True,
                progress=False,
            )
            data = data.dropna(subset=["Close"])
            for idx, row in data.iterrows():
                rows.append(
                    {
                        "date": str(idx.date()),
                        "from_currency": from_ccy,
                        "to_currency": to_ccy,
                        "rate": float(row["Close"]),
                    }
                )
        except Exception as exc:
            log.warning("Failed to get FX rate %s: %s", yf_ticker, exc)

    if rows:
        upsert_fx_rates(conn, rows)
        log.info("Stored %d FX rate rows", len(rows))
    conn.close()
