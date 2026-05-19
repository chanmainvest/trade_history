"""SQLite connection and schema management."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Return a SQLite connection with foreign keys enabled.

    Attempts WAL journal mode for better concurrency; falls back to the
    default (DELETE) mode when the filesystem doesn't support it (e.g.
    Docker bind-mounts on Windows via 9P/grpc-fuse).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass  # filesystem doesn't support WAL; use default journal mode
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path) -> sqlite3.Connection:
    """Create/migrate schema and return connection."""
    conn = get_connection(db_path)
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply incremental migrations for columns added after initial schema."""
    # statement_registry: balance columns
    cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(statement_registry)").fetchall()
    }
    for col, typedef in [
        ("opening_balance_cad", "REAL"),
        ("opening_balance_usd", "REAL"),
        ("closing_balance_cad", "REAL"),
        ("closing_balance_usd", "REAL"),
        ("balance_validated", "TEXT DEFAULT 'missing'"),
        ("docling_json", "TEXT"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE statement_registry ADD COLUMN {col} {typedef}")

    # transactions: statement_id
    tx_cols = {
        r["name"]
        for r in conn.execute("PRAGMA table_info(transactions)").fetchall()
    }
    if "statement_id" not in tx_cols:
        conn.execute(
            "ALTER TABLE transactions ADD COLUMN statement_id INTEGER REFERENCES statement_registry(id)"
        )
    if "docling_ref" not in tx_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN docling_ref TEXT")
    if "docling_page" not in tx_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN docling_page INTEGER")


def upsert_account(
    conn: sqlite3.Connection,
    *,
    institution: str,
    account_id: str,
    account_type: str,
    primary_currency: str = "CAD",
    as_of_date: str | None = None,
) -> int:
    """Insert or update an account record; return its id."""
    cur = conn.execute(
        """
        INSERT INTO accounts (institution, account_id, account_type, primary_currency,
                              first_seen_date, last_seen_date)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            last_seen_date = MAX(excluded.last_seen_date, accounts.last_seen_date),
            first_seen_date = MIN(excluded.first_seen_date, accounts.first_seen_date)
        RETURNING id
        """,
        (
            institution,
            account_id,
            account_type,
            primary_currency,
            as_of_date,
            as_of_date,
        ),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    return conn.execute(
        "SELECT id FROM accounts WHERE account_id = ?", (account_id,)
    ).fetchone()[0]


def upsert_description_symbol_map(
    conn: sqlite3.Connection,
    *,
    institution: str,
    description: str,
    symbol: str,
    source: str = "holdings",
) -> None:
    """Insert or update a description→symbol mapping."""
    conn.execute(
        """
        INSERT INTO description_symbol_map (institution, description, symbol, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(institution, description) DO UPDATE SET
            symbol = excluded.symbol,
            source = excluded.source
        """,
        (institution, description, symbol, source),
    )


def lookup_symbol_by_description(
    conn: sqlite3.Connection,
    institution: str,
    description: str,
) -> str | None:
    """Look up a symbol by institution and description."""
    row = conn.execute(
        "SELECT symbol FROM description_symbol_map WHERE institution = ? AND description = ?",
        (institution, description),
    ).fetchone()
    return row[0] if row else None


def upsert_instrument(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    exchange: str | None = None,
    name: str | None = None,
    asset_type: str,
    option_root: str | None = None,
    strike: float | None = None,
    expiry: str | None = None,
    put_call: str | None = None,
    multiplier: int = 100,
) -> int:
    """Insert or update an instrument; return its id."""
    # For equities (no option fields), ON CONFLICT doesn't work because
    # NULL != NULL in SQL. Check for existing equity by symbol first.
    if option_root is None and strike is None and expiry is None and put_call is None:
        existing = conn.execute(
            """SELECT id FROM instruments
               WHERE symbol = ? AND option_root IS NULL AND strike IS NULL
                 AND expiry IS NULL AND put_call IS NULL""",
            (symbol,),
        ).fetchone()
        if existing:
            return existing[0]

    cur = conn.execute(
        """
        INSERT INTO instruments (symbol, exchange, name, asset_type,
                                 option_root, strike, expiry, put_call, multiplier)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, option_root, strike, expiry, put_call) DO UPDATE SET
            name = COALESCE(excluded.name, instruments.name),
            exchange = COALESCE(excluded.exchange, instruments.exchange)
        RETURNING id
        """,
        (symbol, exchange, name, asset_type, option_root, strike, expiry, put_call, multiplier),
    )
    row = cur.fetchone()
    if row:
        return row[0]
    # Fallback lookup
    return conn.execute(
        """SELECT id FROM instruments
           WHERE symbol=? AND option_root IS ? AND strike IS ? AND expiry IS ? AND put_call IS ?""",
        (symbol, option_root, strike, expiry, put_call),
    ).fetchone()[0]
