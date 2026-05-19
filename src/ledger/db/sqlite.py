"""SQLite connection helpers + schema bootstrap."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from ..config import SQLITE_PATH

_SCHEMA = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


def connect(path: Path | str = SQLITE_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path | str = SQLITE_PATH) -> None:
    conn = connect(path)
    try:
        conn.executescript(_SCHEMA)
        _migrate_existing_schema(conn)
        conn.commit()
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_existing_schema(conn: sqlite3.Connection) -> None:
    if "notes" not in _table_columns(conn, "initial_cash"):
        conn.execute("ALTER TABLE initial_cash ADD COLUMN notes TEXT")


@contextmanager
def session(path: Path | str = SQLITE_PATH):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_institution(conn: sqlite3.Connection, code: str, display_name: str) -> int:
    cur = conn.execute(
        "INSERT INTO institutions(code, display_name) VALUES(?,?) "
        "ON CONFLICT(code) DO UPDATE SET display_name = excluded.display_name "
        "RETURNING institution_id",
        (code, display_name),
    )
    return cur.fetchone()[0]


def upsert_account(
    conn: sqlite3.Connection,
    *,
    institution_id: int,
    account_number: str,
    account_type: str | None = None,
    nickname: str | None = None,
    base_currency: str = "CAD",
) -> int:
    cur = conn.execute(
        "INSERT INTO accounts(institution_id, account_number, account_type, nickname, base_currency) "
        "VALUES(?,?,?,?,?) "
        "ON CONFLICT(institution_id, account_number) DO UPDATE SET "
        "  account_type = COALESCE(excluded.account_type, accounts.account_type), "
        "  nickname     = COALESCE(excluded.nickname, accounts.nickname), "
        "  base_currency= COALESCE(excluded.base_currency, accounts.base_currency) "
        "RETURNING account_id",
        (institution_id, account_number, account_type, nickname, base_currency),
    )
    return cur.fetchone()[0]


def upsert_instrument(
    conn: sqlite3.Connection,
    *,
    asset_type: str,
    symbol: str,
    currency: str,
    exchange: str | None = None,
    name: str | None = None,
    option_root: str | None = None,
    option_expiry: str | None = None,
    option_strike: float | None = None,
    option_type: str | None = None,
    option_multiplier: int = 100,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO instruments
            (asset_type, symbol, currency, exchange, name,
             option_root, option_expiry, option_strike, option_type, option_multiplier)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(asset_type, symbol, currency, option_expiry, option_strike, option_type)
        DO UPDATE SET
            exchange = COALESCE(excluded.exchange, instruments.exchange),
            name     = COALESCE(excluded.name, instruments.name)
        RETURNING instrument_id
        """,
        (
            asset_type, symbol, currency, exchange, name,
            option_root, option_expiry, option_strike, option_type, option_multiplier,
        ),
    )
    return cur.fetchone()[0]
