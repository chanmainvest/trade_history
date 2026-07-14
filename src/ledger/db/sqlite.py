"""SQLite connection helpers + schema bootstrap."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ..config import SQLITE_PATH
from ..identity import (
    canonical_evidence_key,
    canonical_instrument_key,
    canonical_statement_key,
)
from ..quantity import quantity_delta

_SCHEMA = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
SCHEMA_VERSION = 6


def connect(path: Path | str = SQLITE_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path | str = SQLITE_PATH) -> None:
    conn = connect(path)
    try:
        if _needs_v6_migration(conn):
            _migrate_v5_to_v6(conn)
        conn.executescript(_SCHEMA)
        _migrate_existing_schema(conn)
        conn.commit()
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            first = dict(violations[0])
            raise sqlite3.IntegrityError(
                f"schema migration left {len(violations)} foreign-key violation(s); first={first}"
            )
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None


def _needs_v6_migration(conn: sqlite3.Connection) -> bool:
    return _table_exists(conn, "instruments") and (
        "instrument_key" not in _table_columns(conn, "instruments")
        or "statement_key" not in _table_columns(conn, "statements")
    )


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def _create_v6_support_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ingestion_runs (
            ingestion_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file_id INTEGER NOT NULL REFERENCES source_files(source_file_id)
                                   ON DELETE CASCADE,
            source_sha256 TEXT,
            parser_name TEXT,
            parser_version TEXT,
            contract_version TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            resolver_version TEXT,
            status TEXT NOT NULL CHECK (status IN
                ('pending','parsing','validated','active','failed','skipped','superseded')),
            error_summary TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            content_counts_json TEXT,
            content_hash TEXT,
            activated_at TEXT
        )
        """
    )
    _add_column(
        conn,
        "source_files",
        "active_ingestion_run_id INTEGER REFERENCES ingestion_runs(ingestion_run_id) "
        "ON DELETE SET NULL",
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_evidence (
            evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            evidence_key TEXT NOT NULL UNIQUE,
            source_file_id INTEGER NOT NULL REFERENCES source_files(source_file_id)
                                   ON DELETE CASCADE,
            ingestion_run_id INTEGER REFERENCES ingestion_runs(ingestion_run_id)
                                     ON DELETE CASCADE,
            row_kind TEXT NOT NULL,
            occurrence INTEGER NOT NULL,
            page_number INTEGER,
            line_number INTEGER,
            raw_text TEXT,
            bbox_json TEXT,
            words_json TEXT,
            parser_rule TEXT,
            parser_version TEXT,
            CHECK (page_number IS NULL OR page_number >= 1),
            CHECK (line_number IS NULL OR line_number >= 1),
            UNIQUE(source_file_id, row_kind, occurrence, ingestion_run_id)
        )
        """
    )


def _ensure_legacy_runs(conn: sqlite3.Connection) -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    sources = conn.execute(
        """
        SELECT sf.*,
               EXISTS(SELECT 1 FROM statements s WHERE s.source_file_id = sf.source_file_id)
                   AS has_statements
          FROM source_files sf
         ORDER BY sf.source_file_id
        """
    ).fetchall()
    for source in sources:
        existing = conn.execute(
            "SELECT ingestion_run_id, status FROM ingestion_runs "
            "WHERE source_file_id = ? ORDER BY ingestion_run_id DESC LIMIT 1",
            (source["source_file_id"],),
        ).fetchone()
        should_activate = bool(source["has_statements"]) or source["parse_status"] in {
            "ok",
            "partial",
        }
        status = (
            "active"
            if should_activate
            else "skipped"
            if source["parse_status"] == "skipped"
            else "failed"
        )
        if existing is None:
            run_id = conn.execute(
                """
                INSERT INTO ingestion_runs(
                    source_file_id, source_sha256, parser_name, parser_version,
                    contract_version, schema_version, status, error_summary,
                    started_at, finished_at, activated_at
                ) VALUES (?, ?, ?, ?, 'legacy-v5', 5, ?, ?, ?, ?, ?)
                RETURNING ingestion_run_id
                """,
                (
                    source["source_file_id"],
                    source["sha256"],
                    source["parser_name"],
                    source["parser_version"],
                    status,
                    "migrated source status" if status == "failed" else None,
                    source["parsed_at"] or now,
                    source["parsed_at"] or now,
                    (source["parsed_at"] or now) if status == "active" else None,
                ),
            ).fetchone()[0]
        else:
            run_id = existing["ingestion_run_id"]
            if should_activate and existing["status"] != "active":
                conn.execute(
                    "UPDATE ingestion_runs SET status = 'active', activated_at = ? "
                    "WHERE ingestion_run_id = ?",
                    (source["parsed_at"] or now, run_id),
                )
        if status == "active":
            conn.execute(
                "UPDATE source_files SET active_ingestion_run_id = ? WHERE source_file_id = ?",
                (run_id, source["source_file_id"]),
            )


def _weighted_average(
    first_value: float | None,
    first_weight: float,
    second_value: float | None,
    second_weight: float,
) -> float | None:
    if first_value is None or second_value is None:
        return None
    total_weight = abs(first_weight) + abs(second_weight)
    if total_weight == 0:
        return first_value if first_value == second_value else None
    return (
        float(first_value) * abs(first_weight)
        + float(second_value) * abs(second_weight)
    ) / total_weight


def _sum_if_complete(first: float | None, second: float | None) -> float | None:
    if first is None or second is None:
        return None
    return float(first) + float(second)


def _merge_snapshot_instrument(
    conn: sqlite3.Connection,
    *,
    loser_id: int,
    keeper_id: int,
) -> None:
    rows = conn.execute(
        "SELECT * FROM position_snapshots WHERE instrument_id = ? ORDER BY snapshot_id",
        (loser_id,),
    ).fetchall()
    for row in rows:
        conflict = conn.execute(
            "SELECT * FROM position_snapshots "
            "WHERE statement_id = ? AND instrument_id = ?",
            (row["statement_id"], keeper_id),
        ).fetchone()
        if conflict is None:
            conn.execute(
                "UPDATE position_snapshots SET instrument_id = ? WHERE snapshot_id = ?",
                (keeper_id, row["snapshot_id"]),
            )
            continue
        quantity = float(conflict["quantity"]) + float(row["quantity"])
        avg_cost = _weighted_average(
            conflict["avg_cost"],
            conflict["quantity"],
            row["avg_cost"],
            row["quantity"],
        )
        market_price = _weighted_average(
            conflict["market_price"],
            conflict["quantity"],
            row["market_price"],
            row["quantity"],
        )
        raw_parts = [
            text.strip()
            for text in (conflict["raw_line"], row["raw_line"])
            if text and text.strip()
        ]
        conn.execute(
            """
            UPDATE position_snapshots
               SET quantity = ?, avg_cost = ?, book_value = ?, market_price = ?,
                   market_value = ?, unrealized_pnl = ?, raw_line = ?
             WHERE snapshot_id = ?
            """,
            (
                quantity,
                avg_cost,
                _sum_if_complete(conflict["book_value"], row["book_value"]),
                market_price,
                _sum_if_complete(conflict["market_value"], row["market_value"]),
                _sum_if_complete(conflict["unrealized_pnl"], row["unrealized_pnl"]),
                "\n".join(dict.fromkeys(raw_parts)) or None,
                conflict["snapshot_id"],
            ),
        )
        if _table_exists(conn, "position_transaction_links"):
            conn.execute(
                "UPDATE OR IGNORE position_transaction_links SET snapshot_id = ? "
                "WHERE snapshot_id = ?",
                (conflict["snapshot_id"], row["snapshot_id"]),
            )
            conn.execute(
                "DELETE FROM position_transaction_links WHERE snapshot_id = ?",
                (row["snapshot_id"],),
            )
        conn.execute(
            "DELETE FROM position_snapshots WHERE snapshot_id = ?",
            (row["snapshot_id"],),
        )


def _merge_initial_instrument(
    conn: sqlite3.Connection,
    *,
    loser_id: int,
    keeper_id: int,
) -> None:
    rows = conn.execute(
        "SELECT * FROM initial_positions WHERE instrument_id = ? ORDER BY initial_id",
        (loser_id,),
    ).fetchall()
    for row in rows:
        conflict = conn.execute(
            "SELECT * FROM initial_positions "
            "WHERE account_id = ? AND as_of_date = ? AND instrument_id = ?",
            (row["account_id"], row["as_of_date"], keeper_id),
        ).fetchone()
        if conflict is None:
            conn.execute(
                "UPDATE initial_positions SET instrument_id = ? WHERE initial_id = ?",
                (keeper_id, row["initial_id"]),
            )
            continue
        quantity = float(conflict["quantity"]) + float(row["quantity"])
        notes = [
            text.strip()
            for text in (conflict["notes"], row["notes"])
            if text and text.strip()
        ]
        conn.execute(
            """
            UPDATE initial_positions
               SET quantity = ?, avg_cost = ?, notes = ?
             WHERE initial_id = ?
            """,
            (
                quantity,
                _weighted_average(
                    conflict["avg_cost"],
                    conflict["quantity"],
                    row["avg_cost"],
                    row["quantity"],
                ),
                " | ".join(dict.fromkeys(notes)) or None,
                conflict["initial_id"],
            ),
        )
        conn.execute(
            "DELETE FROM initial_positions WHERE initial_id = ?",
            (row["initial_id"],),
        )


def _migrate_instruments(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT * FROM instruments ORDER BY instrument_id").fetchall()
    keepers: dict[str, int] = {}
    keys: dict[int, str] = {}
    for row in rows:
        key = canonical_instrument_key(
            asset_type=row["asset_type"],
            symbol=row["symbol"],
            currency=row["currency"],
            option_root=row["option_root"],
            option_expiry=row["option_expiry"],
            option_strike=row["option_strike"],
            option_type=row["option_type"],
            option_multiplier=row["option_multiplier"] or 100,
        )
        keys[row["instrument_id"]] = key
        keeper_id = keepers.setdefault(key, row["instrument_id"])
        if keeper_id == row["instrument_id"]:
            continue
        conn.execute(
            "UPDATE transactions SET instrument_id = ? WHERE instrument_id = ?",
            (keeper_id, row["instrument_id"]),
        )
        if _table_exists(conn, "instrument_aliases"):
            conn.execute(
                "UPDATE instrument_aliases SET instrument_id = ? WHERE instrument_id = ?",
                (keeper_id, row["instrument_id"]),
            )
        _merge_snapshot_instrument(
            conn,
            loser_id=row["instrument_id"],
            keeper_id=keeper_id,
        )
        _merge_initial_instrument(
            conn,
            loser_id=row["instrument_id"],
            keeper_id=keeper_id,
        )

    conn.execute("DROP TABLE IF EXISTS instruments_v6")
    conn.execute(
        """
        CREATE TABLE instruments_v6 (
            instrument_id INTEGER PRIMARY KEY AUTOINCREMENT,
            instrument_key TEXT NOT NULL UNIQUE,
            asset_type TEXT NOT NULL CHECK (asset_type IN
                ('equity','etf','option','bond','mutual_fund','cash','other')),
            symbol TEXT NOT NULL,
            exchange TEXT,
            currency TEXT NOT NULL,
            name TEXT,
            cusip TEXT,
            isin TEXT,
            option_root TEXT,
            option_expiry TEXT,
            option_strike REAL,
            option_type TEXT CHECK (option_type IN ('CALL','PUT') OR option_type IS NULL),
            option_multiplier INTEGER DEFAULT 100,
            resolution_method TEXT,
            resolution_confidence REAL,
            CHECK (resolution_confidence IS NULL OR
                   (resolution_confidence >= 0 AND resolution_confidence <= 1))
        )
        """
    )
    for row in rows:
        if keepers[keys[row["instrument_id"]]] != row["instrument_id"]:
            continue
        conn.execute(
            """
            INSERT INTO instruments_v6(
                instrument_id, instrument_key, asset_type, symbol, exchange,
                currency, name, cusip, isin, option_root, option_expiry,
                option_strike, option_type, option_multiplier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["instrument_id"],
                keys[row["instrument_id"]],
                row["asset_type"],
                row["symbol"],
                row["exchange"],
                row["currency"],
                row["name"],
                row["cusip"],
                row["isin"],
                row["option_root"],
                row["option_expiry"],
                row["option_strike"],
                row["option_type"],
                row["option_multiplier"],
            ),
        )
    conn.execute("DROP TABLE instruments")
    conn.execute("ALTER TABLE instruments_v6 RENAME TO instruments")


def _source_identity(row: sqlite3.Row) -> str:
    return row["sha256"] or row["relpath"]


def _migrate_statements(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT s.*, sf.sha256, sf.relpath, sf.active_ingestion_run_id,
               i.code AS institution_code, a.account_number
          FROM statements s
          JOIN source_files sf ON sf.source_file_id = s.source_file_id
          JOIN accounts a ON a.account_id = s.account_id
          JOIN institutions i ON i.institution_id = a.institution_id
         ORDER BY s.statement_id
        """
    ).fetchall()
    conn.execute("DROP TABLE IF EXISTS statements_v6")
    conn.execute(
        """
        CREATE TABLE statements_v6 (
            statement_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file_id INTEGER NOT NULL REFERENCES source_files(source_file_id)
                                   ON DELETE CASCADE,
            ingestion_run_id INTEGER NOT NULL REFERENCES ingestion_runs(ingestion_run_id)
                                     ON DELETE CASCADE,
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            statement_key TEXT NOT NULL UNIQUE,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            statement_type TEXT NOT NULL DEFAULT 'monthly',
            UNIQUE(source_file_id, account_id, period_start, period_end, statement_type)
        )
        """
    )
    for row in rows:
        if row["active_ingestion_run_id"] is None:
            raise sqlite3.IntegrityError(
                f"statement {row['statement_id']} has no migratable active ingestion run"
            )
        key = canonical_statement_key(
            source_identity=_source_identity(row),
            institution_code=row["institution_code"],
            account_number=row["account_number"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            statement_type=row["statement_type"],
        )
        conn.execute(
            """
            INSERT INTO statements_v6(
                statement_id, source_file_id, ingestion_run_id, account_id,
                statement_key, period_start, period_end, statement_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["statement_id"],
                row["source_file_id"],
                row["active_ingestion_run_id"],
                row["account_id"],
                key,
                row["period_start"],
                row["period_end"],
                row["statement_type"],
            ),
        )
    conn.execute("DROP TABLE statements")
    conn.execute("ALTER TABLE statements_v6 RENAME TO statements")


def _create_snapshot_sets_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshot_sets (
            snapshot_set_id INTEGER PRIMARY KEY AUTOINCREMENT,
            statement_id INTEGER NOT NULL REFERENCES statements(statement_id) ON DELETE CASCADE,
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            as_of_date TEXT NOT NULL,
            currency TEXT NOT NULL,
            section_type TEXT NOT NULL CHECK (section_type IN ('positions','cash','summary')),
            scope_key TEXT NOT NULL DEFAULT 'default',
            completeness TEXT NOT NULL CHECK (completeness IN
                ('complete','partial','absent','unknown')),
            can_clear_omitted INTEGER GENERATED ALWAYS AS
                (CASE WHEN completeness = 'complete' THEN 1 ELSE 0 END) STORED,
            evidence_id INTEGER REFERENCES source_evidence(evidence_id),
            reported_total REAL,
            validation_status TEXT NOT NULL DEFAULT 'unvalidated' CHECK (validation_status IN
                ('unvalidated','valid','warning','invalid')),
            UNIQUE(statement_id, currency, section_type, scope_key)
        )
        """
    )


def _ensure_snapshot_set(
    conn: sqlite3.Connection,
    *,
    statement_id: int,
    account_id: int,
    as_of_date: str,
    currency: str,
    section_type: str,
    completeness: str = "unknown",
    scope_key: str = "default",
) -> int:
    return conn.execute(
        """
        INSERT INTO snapshot_sets(
            statement_id, account_id, as_of_date, currency, section_type,
            scope_key, completeness, validation_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'warning')
        ON CONFLICT(statement_id, currency, section_type, scope_key) DO UPDATE SET
            as_of_date = excluded.as_of_date
        RETURNING snapshot_set_id
        """,
        (
            statement_id,
            account_id,
            as_of_date,
            currency,
            section_type,
            scope_key,
            completeness,
        ),
    ).fetchone()[0]


def _ensure_evidence(
    conn: sqlite3.Connection,
    *,
    source_file_id: int,
    ingestion_run_id: int | None,
    row_kind: str,
    occurrence: int,
    raw_text: str | None,
    parser_rule: str,
) -> int:
    source = conn.execute(
        "SELECT relpath, sha256, parser_version FROM source_files WHERE source_file_id = ?",
        (source_file_id,),
    ).fetchone()
    if source is None:
        raise sqlite3.IntegrityError(f"missing source file {source_file_id} for evidence")
    key = canonical_evidence_key(
        source_identity=_source_identity(source),
        row_kind=row_kind,
        occurrence=occurrence,
        raw_text=raw_text,
        parser_rule=parser_rule,
    )
    existing = conn.execute(
        "SELECT evidence_id FROM source_evidence "
        "WHERE source_file_id = ? AND row_kind = ? AND occurrence = ? "
        "AND ingestion_run_id IS ?",
        (source_file_id, row_kind, occurrence, ingestion_run_id),
    ).fetchone()
    if existing:
        return existing["evidence_id"]
    return conn.execute(
        """
        INSERT INTO source_evidence(
            evidence_key, source_file_id, ingestion_run_id, row_kind,
            occurrence, raw_text, parser_rule, parser_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING evidence_id
        """,
        (
            key,
            source_file_id,
            ingestion_run_id,
            row_kind,
            occurrence,
            raw_text,
            parser_rule,
            source["parser_version"],
        ),
    ).fetchone()[0]


def _statement_source_run(
    conn: sqlite3.Connection,
    statement_id: int | None,
    source_file_id: int | None,
) -> tuple[int | None, int | None]:
    if statement_id is not None:
        row = conn.execute(
            "SELECT source_file_id, ingestion_run_id FROM statements WHERE statement_id = ?",
            (statement_id,),
        ).fetchone()
        if row:
            return row["source_file_id"], row["ingestion_run_id"]
    if source_file_id is not None:
        row = conn.execute(
            "SELECT active_ingestion_run_id FROM source_files WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()
        return source_file_id, row["active_ingestion_run_id"] if row else None
    return None, None


def _migrate_transactions(conn: sqlite3.Connection) -> None:
    for definition in (
        "ingestion_run_id INTEGER REFERENCES ingestion_runs(ingestion_run_id) ON DELETE SET NULL",
        "evidence_id INTEGER REFERENCES source_evidence(evidence_id) ON DELETE SET NULL",
        "position_delta REAL",
        "cash_delta REAL",
        "cash_effective_date TEXT",
        "resolution_method TEXT",
        "resolution_confidence REAL",
        "resolution_evidence_id INTEGER REFERENCES source_evidence(evidence_id) ON DELETE SET NULL",
    ):
        _add_column(conn, "transactions", definition)

    rows = conn.execute("SELECT * FROM transactions ORDER BY transaction_id").fetchall()
    for row in rows:
        source_file_id, run_id = _statement_source_run(
            conn,
            row["statement_id"],
            row["source_file_id"],
        )
        evidence_id = None
        if source_file_id is not None:
            evidence_id = _ensure_evidence(
                conn,
                source_file_id=source_file_id,
                ingestion_run_id=run_id,
                row_kind="transaction",
                occurrence=row["transaction_id"],
                raw_text=row["raw_line"],
                parser_rule="migration:v5",
            )
        position_effect = (
            quantity_delta(row["txn_type"], row["quantity"])
            if row["quantity"] is not None
            else None
        )
        conn.execute(
            """
            UPDATE transactions
               SET source_file_id = COALESCE(source_file_id, ?),
                   ingestion_run_id = ?, evidence_id = ?, position_delta = ?,
                   cash_delta = net_amount,
                   cash_effective_date = COALESCE(settle_date, trade_date)
             WHERE transaction_id = ?
            """,
            (
                source_file_id,
                run_id,
                evidence_id,
                position_effect,
                row["transaction_id"],
            ),
        )


def _migrate_positions(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT ps.*, s.source_file_id, s.ingestion_run_id
          FROM position_snapshots ps
          JOIN statements s ON s.statement_id = ps.statement_id
         ORDER BY ps.snapshot_id
        """
    ).fetchall()
    conn.execute("DROP TABLE IF EXISTS position_snapshots_v6")
    conn.execute(
        """
        CREATE TABLE position_snapshots_v6 (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            statement_id INTEGER NOT NULL REFERENCES statements(statement_id) ON DELETE CASCADE,
            snapshot_set_id INTEGER NOT NULL REFERENCES snapshot_sets(snapshot_set_id)
                                    ON DELETE CASCADE,
            evidence_id INTEGER NOT NULL REFERENCES source_evidence(evidence_id) ON DELETE CASCADE,
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            as_of_date TEXT NOT NULL,
            instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id),
            quantity REAL NOT NULL,
            avg_cost REAL,
            book_value REAL,
            market_price REAL,
            market_value REAL,
            unrealized_pnl REAL,
            currency TEXT NOT NULL,
            raw_line TEXT,
            UNIQUE(snapshot_set_id, instrument_id)
        )
        """
    )
    for row in rows:
        snapshot_set_id = _ensure_snapshot_set(
            conn,
            statement_id=row["statement_id"],
            account_id=row["account_id"],
            as_of_date=row["as_of_date"],
            currency=row["currency"],
            section_type="positions",
        )
        evidence_id = _ensure_evidence(
            conn,
            source_file_id=row["source_file_id"],
            ingestion_run_id=row["ingestion_run_id"],
            row_kind="position",
            occurrence=row["snapshot_id"],
            raw_text=row["raw_line"],
            parser_rule="migration:v5",
        )
        conn.execute(
            """
            INSERT INTO position_snapshots_v6(
                snapshot_id, statement_id, snapshot_set_id, evidence_id,
                account_id, as_of_date, instrument_id, quantity, avg_cost,
                book_value, market_price, market_value, unrealized_pnl,
                currency, raw_line
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["snapshot_id"],
                row["statement_id"],
                snapshot_set_id,
                evidence_id,
                row["account_id"],
                row["as_of_date"],
                row["instrument_id"],
                row["quantity"],
                row["avg_cost"],
                row["book_value"],
                row["market_price"],
                row["market_value"],
                row["unrealized_pnl"],
                row["currency"],
                row["raw_line"],
            ),
        )
    conn.execute("DROP TABLE position_snapshots")
    conn.execute("ALTER TABLE position_snapshots_v6 RENAME TO position_snapshots")


def _migrate_cash_balances(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT cb.*, s.source_file_id, s.ingestion_run_id
          FROM cash_balances cb
          JOIN statements s ON s.statement_id = cb.statement_id
         ORDER BY cb.cash_balance_id
        """
    ).fetchall()
    conn.execute("DROP TABLE IF EXISTS cash_balances_v6")
    conn.execute(
        """
        CREATE TABLE cash_balances_v6 (
            cash_balance_id INTEGER PRIMARY KEY AUTOINCREMENT,
            statement_id INTEGER NOT NULL REFERENCES statements(statement_id) ON DELETE CASCADE,
            snapshot_set_id INTEGER NOT NULL REFERENCES snapshot_sets(snapshot_set_id)
                                    ON DELETE CASCADE,
            evidence_id INTEGER NOT NULL REFERENCES source_evidence(evidence_id) ON DELETE CASCADE,
            account_id INTEGER NOT NULL REFERENCES accounts(account_id),
            as_of_date TEXT NOT NULL,
            currency TEXT NOT NULL,
            opening_balance REAL,
            closing_balance REAL NOT NULL,
            raw_line TEXT,
            UNIQUE(snapshot_set_id)
        )
        """
    )
    for row in rows:
        snapshot_set_id = _ensure_snapshot_set(
            conn,
            statement_id=row["statement_id"],
            account_id=row["account_id"],
            as_of_date=row["as_of_date"],
            currency=row["currency"],
            section_type="cash",
        )
        evidence_id = _ensure_evidence(
            conn,
            source_file_id=row["source_file_id"],
            ingestion_run_id=row["ingestion_run_id"],
            row_kind="cash",
            occurrence=row["cash_balance_id"],
            raw_text=None,
            parser_rule="migration:v5:cash-source-unavailable",
        )
        conn.execute(
            """
            INSERT INTO cash_balances_v6(
                cash_balance_id, statement_id, snapshot_set_id, evidence_id,
                account_id, as_of_date, currency, opening_balance,
                closing_balance, raw_line
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                row["cash_balance_id"],
                row["statement_id"],
                snapshot_set_id,
                evidence_id,
                row["account_id"],
                row["as_of_date"],
                row["currency"],
                row["opening_balance"],
                row["closing_balance"],
            ),
        )
    conn.execute("DROP TABLE cash_balances")
    conn.execute("ALTER TABLE cash_balances_v6 RENAME TO cash_balances")


def _migrate_quarantine(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT * FROM quarantine_transactions ORDER BY quarantine_id"
    ).fetchall()
    conn.execute("DROP TABLE IF EXISTS quarantine_transactions_v6")
    conn.execute(
        """
        CREATE TABLE quarantine_transactions_v6 (
            quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file_id INTEGER REFERENCES source_files(source_file_id) ON DELETE CASCADE,
            ingestion_run_id INTEGER REFERENCES ingestion_runs(ingestion_run_id) ON DELETE CASCADE,
            statement_id INTEGER REFERENCES statements(statement_id) ON DELETE CASCADE,
            account_id INTEGER REFERENCES accounts(account_id),
            evidence_id INTEGER REFERENCES source_evidence(evidence_id) ON DELETE CASCADE,
            occurrence INTEGER NOT NULL,
            raw_line TEXT NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(ingestion_run_id, evidence_id)
        )
        """
    )
    for row in rows:
        source_file_id, run_id = _statement_source_run(
            conn,
            None,
            row["source_file_id"],
        )
        evidence_id = None
        if source_file_id is not None:
            evidence_id = _ensure_evidence(
                conn,
                source_file_id=source_file_id,
                ingestion_run_id=run_id,
                row_kind="quarantine",
                occurrence=row["quarantine_id"],
                raw_text=row["raw_line"],
                parser_rule="migration:v5",
            )
        conn.execute(
            """
            INSERT INTO quarantine_transactions_v6(
                quarantine_id, source_file_id, ingestion_run_id, statement_id,
                account_id, evidence_id, occurrence, raw_line, reason, created_at
            ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["quarantine_id"],
                source_file_id,
                run_id,
                row["account_id"],
                evidence_id,
                row["quarantine_id"],
                row["raw_line"],
                row["reason"],
                row["created_at"],
            ),
        )
    conn.execute("DROP TABLE quarantine_transactions")
    conn.execute("ALTER TABLE quarantine_transactions_v6 RENAME TO quarantine_transactions")


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """Upgrade a complete pre-refactor ledger while preserving row IDs.

    Derived rows that referenced duplicate logical instruments are repointed to
    the oldest canonical ID. Same-statement duplicate holdings are combined as
    reported lots; no balancing rows or guessed values are introduced. Legacy
    sections are explicitly marked ``unknown`` completeness.
    """
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        _create_v6_support_tables(conn)
        _ensure_legacy_runs(conn)
        _migrate_instruments(conn)
        _migrate_statements(conn)
        _create_snapshot_sets_table(conn)
        _migrate_transactions(conn)
        _migrate_positions(conn)
        _migrate_cash_balances(conn)
        _migrate_quarantine(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


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


def active_ingestion_run_id(conn: sqlite3.Connection, source_file_id: int) -> int:
    row = conn.execute(
        "SELECT active_ingestion_run_id FROM source_files WHERE source_file_id = ?",
        (source_file_id,),
    ).fetchone()
    if row is None or row["active_ingestion_run_id"] is None:
        raise sqlite3.IntegrityError(
            f"source file {source_file_id} has no active ingestion run"
        )
    return int(row["active_ingestion_run_id"])


def record_ingestion_run(
    conn: sqlite3.Connection,
    *,
    source_file_id: int,
    source_sha256: str | None,
    parser_name: str | None,
    parser_version: str | None,
    contract_version: str,
    status: str,
    error_summary: str | None = None,
    resolver_version: str | None = None,
) -> int:
    """Record an extraction attempt and activate only successful output."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    run_status = "active" if status in {"ok", "partial"} else status
    if run_status not in {"active", "failed", "skipped", "pending"}:
        raise ValueError(f"unsupported ingestion run status: {status!r}")
    if run_status == "active":
        conn.execute(
            "UPDATE ingestion_runs SET status = 'superseded' "
            "WHERE source_file_id = ? AND status = 'active'",
            (source_file_id,),
        )
    run_id = conn.execute(
        """
        INSERT INTO ingestion_runs(
            source_file_id, source_sha256, parser_name, parser_version,
            contract_version, schema_version, resolver_version, status,
            error_summary, started_at, finished_at, activated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING ingestion_run_id
        """,
        (
            source_file_id,
            source_sha256,
            parser_name,
            parser_version,
            contract_version,
            SCHEMA_VERSION,
            resolver_version,
            run_status,
            error_summary,
            now,
            now,
            now if run_status == "active" else None,
        ),
    ).fetchone()[0]
    if run_status == "active":
        conn.execute(
            "UPDATE source_files SET active_ingestion_run_id = ? WHERE source_file_id = ?",
            (run_id, source_file_id),
        )
    return int(run_id)


def upsert_source_evidence(
    conn: sqlite3.Connection,
    *,
    source_file_id: int,
    ingestion_run_id: int,
    row_kind: str,
    occurrence: int,
    raw_text: str | None,
    parser_version: str | None,
    parser_rule: str | None = None,
    page_number: int | None = None,
    line_number: int | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    words: list[dict[str, object]] | None = None,
) -> int:
    source = conn.execute(
        "SELECT relpath, sha256 FROM source_files WHERE source_file_id = ?",
        (source_file_id,),
    ).fetchone()
    if source is None:
        raise sqlite3.IntegrityError(f"missing source file {source_file_id}")
    key = canonical_evidence_key(
        source_identity=_source_identity(source),
        row_kind=row_kind,
        occurrence=occurrence,
        raw_text=raw_text,
        page_number=page_number,
        line_number=line_number,
        parser_rule=parser_rule,
    )
    return int(
        conn.execute(
            """
            INSERT INTO source_evidence(
                evidence_key, source_file_id, ingestion_run_id, row_kind,
                occurrence, page_number, line_number, raw_text, bbox_json,
                words_json, parser_rule, parser_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_file_id, row_kind, occurrence, ingestion_run_id)
            DO UPDATE SET
                evidence_key = excluded.evidence_key,
                page_number = excluded.page_number,
                line_number = excluded.line_number,
                raw_text = excluded.raw_text,
                bbox_json = excluded.bbox_json,
                words_json = excluded.words_json,
                parser_rule = excluded.parser_rule,
                parser_version = excluded.parser_version
            RETURNING evidence_id
            """,
            (
                key,
                source_file_id,
                ingestion_run_id,
                row_kind,
                occurrence,
                page_number,
                line_number,
                raw_text,
                json.dumps(bbox) if bbox is not None else None,
                json.dumps(words, sort_keys=True) if words is not None else None,
                parser_rule,
                parser_version,
            ),
        ).fetchone()[0]
    )


def upsert_snapshot_set(
    conn: sqlite3.Connection,
    *,
    statement_id: int,
    account_id: int,
    as_of_date: str,
    currency: str,
    section_type: str,
    scope_key: str,
    completeness: str,
    evidence_id: int | None,
    reported_total: float | None,
    validation_status: str,
) -> int:
    return int(
        conn.execute(
            """
            INSERT INTO snapshot_sets(
                statement_id, account_id, as_of_date, currency, section_type,
                scope_key, completeness, evidence_id, reported_total,
                validation_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(statement_id, currency, section_type, scope_key) DO UPDATE SET
                as_of_date = excluded.as_of_date,
                completeness = excluded.completeness,
                evidence_id = excluded.evidence_id,
                reported_total = excluded.reported_total,
                validation_status = excluded.validation_status
            RETURNING snapshot_set_id
            """,
            (
                statement_id,
                account_id,
                as_of_date,
                currency,
                section_type,
                scope_key,
                completeness,
                evidence_id,
                reported_total,
                validation_status,
            ),
        ).fetchone()[0]
    )


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
    resolution_method: str | None = None,
    resolution_confidence: float | None = None,
) -> int:
    instrument_key = canonical_instrument_key(
        asset_type=asset_type,
        symbol=symbol,
        currency=currency,
        option_root=option_root,
        option_expiry=option_expiry,
        option_strike=option_strike,
        option_type=option_type,
        option_multiplier=option_multiplier,
    )
    cur = conn.execute(
        """
        INSERT INTO instruments
            (instrument_key, asset_type, symbol, currency, exchange, name,
             option_root, option_expiry, option_strike, option_type,
             option_multiplier, resolution_method, resolution_confidence)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(instrument_key)
        DO UPDATE SET
            exchange = COALESCE(excluded.exchange, instruments.exchange),
            name     = COALESCE(excluded.name, instruments.name),
            resolution_method = COALESCE(
                excluded.resolution_method, instruments.resolution_method
            ),
            resolution_confidence = COALESCE(
                excluded.resolution_confidence, instruments.resolution_confidence
            )
        RETURNING instrument_id
        """,
        (
            instrument_key, asset_type, symbol, currency, exchange, name,
            option_root, option_expiry, option_strike, option_type, option_multiplier,
            resolution_method, resolution_confidence,
        ),
    )
    return cur.fetchone()[0]
