"""FastAPI dependency injectors for DB connections."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Generator
from pathlib import Path

import duckdb

from trade_history.db.duckdb import get_connection as duck_connect
from trade_history.db.sqlite import get_connection as sqlite_connect


def _db_dir() -> Path:
    return Path(os.environ.get("DB_PATH", "data"))


def get_sqlite() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite_connect(_db_dir() / "trade_history.db")
    try:
        yield conn
    finally:
        conn.close()


def get_duckdb() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    conn = duck_connect(_db_dir() / "market_data.duckdb")
    try:
        yield conn
    finally:
        conn.close()
