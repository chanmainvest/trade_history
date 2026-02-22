from __future__ import annotations

from contextlib import contextmanager
import duckdb
from pathlib import Path
from typing import Iterator

from trade_history.config import settings
from trade_history.db.sqlite import ensure_parent


DUCK_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_stooq_prices (
  symbol_norm TEXT NOT NULL,
  trade_date DATE NOT NULL,
  open DOUBLE,
  high DOUBLE,
  low DOUBLE,
  close DOUBLE,
  volume DOUBLE,
  currency TEXT,
  raw_payload TEXT,
  ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_yahoo_prices (
  symbol_norm TEXT NOT NULL,
  trade_date DATE NOT NULL,
  open DOUBLE,
  high DOUBLE,
  low DOUBLE,
  close DOUBLE,
  volume DOUBLE,
  currency TEXT,
  raw_payload TEXT,
  ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_crossref (
  symbol_norm TEXT NOT NULL,
  trade_date DATE NOT NULL,
  stooq_close DOUBLE,
  yahoo_close DOUBLE,
  quality_flag TEXT NOT NULL,
  canonical_source TEXT NOT NULL,
  canonical_close DOUBLE NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS canonical_prices (
  symbol_norm TEXT NOT NULL,
  trade_date DATE NOT NULL,
  open DOUBLE,
  high DOUBLE,
  low DOUBLE,
  close DOUBLE,
  volume DOUBLE,
  currency TEXT,
  source TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS raw_boc_fx (
  observed_date DATE NOT NULL,
  series_id TEXT NOT NULL,
  value DOUBLE NOT NULL,
  raw_payload TEXT,
  ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS canonical_fx (
  observed_date DATE NOT NULL,
  base_currency TEXT NOT NULL,
  quote_currency TEXT NOT NULL,
  rate DOUBLE NOT NULL,
  source TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def connect(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    db_path = path or settings.duckdb_path
    ensure_parent(db_path)
    conn = duckdb.connect(str(db_path))
    return conn


def init_db(path: Path | None = None) -> None:
    conn = connect(path)
    try:
        conn.execute(DUCK_SCHEMA)
    finally:
        conn.close()


@contextmanager
def session(path: Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()
