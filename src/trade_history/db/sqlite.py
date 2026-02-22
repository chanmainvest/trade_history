from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from trade_history.config import settings


SQLITE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS statement_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  institution TEXT NOT NULL,
  account_id TEXT,
  file_path TEXT NOT NULL UNIQUE,
  period_start TEXT,
  period_end TEXT,
  format_version TEXT NOT NULL,
  parse_status TEXT NOT NULL,
  parse_message TEXT,
  checksum TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS accounts (
  account_id TEXT PRIMARY KEY,
  institution TEXT NOT NULL,
  account_name TEXT,
  account_type TEXT,
  base_currency TEXT,
  masked_number TEXT
);

CREATE TABLE IF NOT EXISTS instruments (
  instrument_id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol_raw TEXT NOT NULL,
  symbol_norm TEXT NOT NULL,
  asset_type TEXT NOT NULL,
  option_root TEXT,
  strike REAL,
  expiry TEXT,
  put_call TEXT,
  multiplier INTEGER DEFAULT 1,
  exchange TEXT,
  sector TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_instruments_symbol_contract
ON instruments(symbol_norm, asset_type, IFNULL(expiry, ''), IFNULL(strike, -1), IFNULL(put_call, ''));

CREATE TABLE IF NOT EXISTS symbol_overrides (
  symbol_norm TEXT PRIMARY KEY,
  market_symbol TEXT NOT NULL,
  sector_override TEXT,
  notes TEXT,
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS instrument_metadata (
  symbol_norm TEXT NOT NULL,
  provider TEXT NOT NULL,
  market_symbol TEXT,
  display_name TEXT,
  quote_type TEXT,
  sector TEXT,
  industry TEXT,
  exchange TEXT,
  source_json TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (symbol_norm, provider)
);

CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  settle_date TEXT,
  event_type TEXT NOT NULL,
  instrument_id INTEGER,
  side TEXT,
  quantity REAL,
  price REAL,
  gross_amount REAL,
  commission REAL DEFAULT 0,
  fees REAL DEFAULT 0,
  currency TEXT,
  source_file_id INTEGER NOT NULL,
  source_line_ref TEXT,
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (account_id) REFERENCES accounts(account_id),
  FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
  FOREIGN KEY (source_file_id) REFERENCES statement_files(id)
);

CREATE INDEX IF NOT EXISTS idx_events_trade_date ON events(trade_date);
CREATE INDEX IF NOT EXISTS idx_events_account ON events(account_id);
CREATE INDEX IF NOT EXISTS idx_events_instrument ON events(instrument_id);

CREATE TABLE IF NOT EXISTS statement_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file_id INTEGER NOT NULL,
  account_id TEXT NOT NULL,
  snapshot_date TEXT,
  metric_code TEXT NOT NULL,
  currency TEXT,
  value_native REAL NOT NULL,
  source_line_ref TEXT,
  raw_line TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (source_file_id) REFERENCES statement_files(id),
  FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE INDEX IF NOT EXISTS idx_statement_snapshots_file ON statement_snapshots(source_file_id);
CREATE INDEX IF NOT EXISTS idx_statement_snapshots_account_date
ON statement_snapshots(account_id, snapshot_date, metric_code);

CREATE TABLE IF NOT EXISTS transfers (
  transfer_id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_event_id INTEGER NOT NULL,
  to_event_id INTEGER NOT NULL,
  transfer_group_key TEXT NOT NULL UNIQUE,
  continuity_mode TEXT NOT NULL DEFAULT 'carry_cost',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (from_event_id) REFERENCES events(event_id),
  FOREIGN KEY (to_event_id) REFERENCES events(event_id)
);

CREATE TABLE IF NOT EXISTS lot_closures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  close_event_id INTEGER NOT NULL,
  instrument_id INTEGER NOT NULL,
  account_id TEXT NOT NULL,
  quantity_closed REAL NOT NULL,
  proceeds_native REAL NOT NULL,
  cost_native REAL NOT NULL,
  realized_pl_native REAL NOT NULL,
  currency TEXT NOT NULL,
  method TEXT NOT NULL DEFAULT 'average_cost',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (close_event_id) REFERENCES events(event_id),
  FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
  FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE TABLE IF NOT EXISTS position_state (
  account_id TEXT NOT NULL,
  instrument_id INTEGER NOT NULL,
  currency TEXT NOT NULL,
  quantity REAL NOT NULL,
  cost_total_native REAL NOT NULL,
  avg_cost_native REAL,
  as_of_event_id INTEGER NOT NULL,
  as_of_trade_date TEXT NOT NULL,
  PRIMARY KEY (account_id, instrument_id, currency),
  FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id),
  FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE TABLE IF NOT EXISTS fx_rates (
  date TEXT NOT NULL,
  pair TEXT NOT NULL,
  rate REAL NOT NULL,
  source TEXT NOT NULL,
  PRIMARY KEY (date, pair)
);

CREATE TABLE IF NOT EXISTS daily_snapshots (
  date TEXT NOT NULL,
  account_id TEXT NOT NULL,
  instrument_id INTEGER NOT NULL,
  quantity REAL NOT NULL,
  mv_native REAL,
  mv_cad REAL,
  mv_usd REAL,
  cost_native REAL,
  unrealized_pl_native REAL,
  realized_pl_ytd_native REAL,
  PRIMARY KEY (date, account_id, instrument_id),
  FOREIGN KEY (account_id) REFERENCES accounts(account_id),
  FOREIGN KEY (instrument_id) REFERENCES instruments(instrument_id)
);

CREATE TABLE IF NOT EXISTS quarantine_transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  institution TEXT NOT NULL,
  file_path TEXT NOT NULL,
  page_number INTEGER,
  raw_line TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_name TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at TEXT,
  details_json TEXT
);
"""


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def get_connection(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or settings.sqlite_path
    ensure_parent(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # WAL can fail on some mounted/network filesystems (notably certain Docker Desktop mounts).
        conn.execute("PRAGMA journal_mode = WAL;")
    except sqlite3.DatabaseError:
        conn.execute("PRAGMA journal_mode = DELETE;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(path: Path | None = None) -> None:
    with get_connection(path) as conn:
        conn.executescript(SQLITE_SCHEMA)


@contextmanager
def db_session(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = get_connection(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
