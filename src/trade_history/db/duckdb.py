"""DuckDB store for market price and FX rate data."""

from __future__ import annotations

from pathlib import Path

import duckdb


def get_connection(duckdb_path: Path) -> duckdb.DuckDBPyConnection:
    """Open (or create) the DuckDB database and ensure tables exist."""
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(duckdb_path))
    _init_schema(conn)
    return conn


def _init_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            ticker  VARCHAR NOT NULL,
            date    DATE    NOT NULL,
            open    DOUBLE,
            high    DOUBLE,
            low     DOUBLE,
            close   DOUBLE NOT NULL,
            volume  BIGINT,
            currency VARCHAR NOT NULL DEFAULT 'CAD',
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fx_rates (
            date         DATE    NOT NULL,
            from_currency VARCHAR NOT NULL,
            to_currency   VARCHAR NOT NULL,
            rate          DOUBLE  NOT NULL,
            PRIMARY KEY (date, from_currency, to_currency)
        )
    """)


def upsert_ohlcv(
    conn: duckdb.DuckDBPyConnection,
    rows: list[dict],
) -> None:
    """Bulk upsert OHLCV rows. Each dict: ticker, date, open, high, low, close, volume, currency."""
    if not rows:
        return
    import pandas as pd

    df = pd.DataFrame(rows)  # noqa: F841 — referenced inside DuckDB SQL string
    conn.execute("""
        INSERT OR REPLACE INTO ohlcv
        SELECT ticker, date::DATE, open, high, low, close, volume, currency
        FROM df
    """)


def upsert_fx_rates(
    conn: duckdb.DuckDBPyConnection,
    rows: list[dict],
) -> None:
    """Bulk upsert FX rate rows. Each dict: date, from_currency, to_currency, rate."""
    if not rows:
        return
    import pandas as pd

    df = pd.DataFrame(rows)  # noqa: F841 — referenced inside DuckDB SQL string
    conn.execute("""
        INSERT OR REPLACE INTO fx_rates
        SELECT date::DATE, from_currency, to_currency, rate
        FROM df
    """)


def get_fx_rate(
    conn: duckdb.DuckDBPyConnection,
    from_currency: str,
    to_currency: str,
    date: str,
) -> float | None:
    """Return the FX rate for a given date (or nearest prior date)."""
    if from_currency == to_currency:
        return 1.0
    row = conn.execute(
        """
        SELECT rate FROM fx_rates
        WHERE from_currency = ? AND to_currency = ? AND date <= ?::DATE
        ORDER BY date DESC LIMIT 1
        """,
        [from_currency, to_currency, date],
    ).fetchone()
    if row:
        return row[0]
    # Try inverse
    row = conn.execute(
        """
        SELECT 1.0 / rate FROM fx_rates
        WHERE from_currency = ? AND to_currency = ? AND date <= ?::DATE
        ORDER BY date DESC LIMIT 1
        """,
        [to_currency, from_currency, date],
    ).fetchone()
    return row[0] if row else None
