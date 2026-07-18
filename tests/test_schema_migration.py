"""Pre-v6 database migration coverage for the Phase 2 data model."""
from __future__ import annotations

import sqlite3

import pytest

from ledger.db import sqlite as sqlite_db
from ledger.ingest.pipeline import _record_source_file, _write_statement
from ledger.parsers.types import (
    ParsedAccount,
    ParsedCashBalance,
    ParsedInstrument,
    ParsedPosition,
    ParsedSnapshotSet,
    ParsedStatement,
    ParsedTxn,
)
from ledger.pdf_text import PdfText


def _create_v5_database(path) -> None:
    conn = sqlite_db.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE institutions (
                institution_id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL
            );
            CREATE TABLE accounts (
                account_id INTEGER PRIMARY KEY AUTOINCREMENT,
                institution_id INTEGER NOT NULL REFERENCES institutions(institution_id),
                account_number TEXT NOT NULL,
                account_type TEXT,
                nickname TEXT,
                base_currency TEXT NOT NULL DEFAULT 'CAD',
                opened_on TEXT,
                closed_on TEXT,
                notes TEXT,
                UNIQUE(institution_id, account_number)
            );
            CREATE TABLE instruments (
                instrument_id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                exchange TEXT,
                currency TEXT NOT NULL,
                name TEXT,
                cusip TEXT,
                isin TEXT,
                option_root TEXT,
                option_expiry TEXT,
                option_strike REAL,
                option_type TEXT,
                option_multiplier INTEGER DEFAULT 100,
                UNIQUE(asset_type, symbol, currency, option_expiry, option_strike, option_type)
            );
            CREATE TABLE source_files (
                source_file_id INTEGER PRIMARY KEY AUTOINCREMENT,
                relpath TEXT NOT NULL UNIQUE,
                sha256 TEXT,
                size_bytes INTEGER,
                page_count INTEGER,
                is_image_only INTEGER NOT NULL DEFAULT 0,
                parser_name TEXT,
                parser_version TEXT,
                parsed_at TEXT,
                parse_status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE TABLE statements (
                statement_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file_id INTEGER NOT NULL REFERENCES source_files(source_file_id),
                account_id INTEGER NOT NULL REFERENCES accounts(account_id),
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                statement_type TEXT NOT NULL DEFAULT 'monthly',
                UNIQUE(source_file_id, account_id, period_end)
            );
            CREATE TABLE transactions (
                transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(account_id),
                statement_id INTEGER REFERENCES statements(statement_id),
                source_file_id INTEGER REFERENCES source_files(source_file_id),
                trade_date TEXT NOT NULL,
                settle_date TEXT,
                txn_type TEXT NOT NULL,
                instrument_id INTEGER REFERENCES instruments(instrument_id),
                quantity REAL,
                price REAL,
                gross_amount REAL,
                commission REAL DEFAULT 0,
                other_fees REAL DEFAULT 0,
                net_amount REAL,
                currency TEXT NOT NULL,
                counterpart_account_id INTEGER,
                counterpart_txn_id INTEGER,
                tax_country TEXT,
                tax_rate REAL,
                description TEXT,
                raw_line TEXT,
                parser_confidence REAL DEFAULT 1.0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE quarantine_transactions (
                quarantine_id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file_id INTEGER REFERENCES source_files(source_file_id),
                account_id INTEGER REFERENCES accounts(account_id),
                raw_line TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE position_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                statement_id INTEGER NOT NULL REFERENCES statements(statement_id),
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
                UNIQUE(statement_id, instrument_id)
            );
            CREATE TABLE cash_balances (
                cash_balance_id INTEGER PRIMARY KEY AUTOINCREMENT,
                statement_id INTEGER NOT NULL REFERENCES statements(statement_id),
                account_id INTEGER NOT NULL REFERENCES accounts(account_id),
                as_of_date TEXT NOT NULL,
                currency TEXT NOT NULL,
                opening_balance REAL,
                closing_balance REAL NOT NULL,
                UNIQUE(statement_id, currency)
            );
            CREATE TABLE position_transaction_links (
                link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL REFERENCES position_snapshots(snapshot_id) ON DELETE CASCADE,
                transaction_id INTEGER NOT NULL REFERENCES transactions(transaction_id) ON DELETE CASCADE,
                quantity_attributed REAL NOT NULL,
                UNIQUE(snapshot_id, transaction_id)
            );
            CREATE TABLE initial_positions (
                initial_id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(account_id),
                as_of_date TEXT NOT NULL,
                instrument_id INTEGER NOT NULL REFERENCES instruments(instrument_id),
                quantity REAL NOT NULL,
                avg_cost REAL,
                currency TEXT NOT NULL,
                notes TEXT,
                UNIQUE(account_id, as_of_date, instrument_id)
            );
            CREATE TABLE initial_cash (
                initial_cash_id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL REFERENCES accounts(account_id),
                as_of_date TEXT NOT NULL,
                currency TEXT NOT NULL,
                balance REAL NOT NULL,
                UNIQUE(account_id, as_of_date, currency)
            );
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES ('schema_version', '5');
            """
        )
        conn.execute("INSERT INTO institutions(code, display_name) VALUES ('TST', 'Test')")
        conn.execute(
            "INSERT INTO accounts(institution_id, account_number) VALUES (1, 'A-1')"
        )
        # Nullable option fields make these two ordinary equities distinct in v5.
        conn.execute(
            "INSERT INTO instruments(asset_type, symbol, currency) VALUES ('equity', 'ABC', 'CAD')"
        )
        conn.execute(
            "INSERT INTO instruments(asset_type, symbol, currency) VALUES ('equity', 'ABC', 'CAD')"
        )
        conn.execute(
            """
            INSERT INTO source_files(relpath, sha256, parser_name, parser_version, parsed_at, parse_status)
            VALUES ('Statements/Test/legacy.pdf', 'legacy-sha', 'legacy', '5.0',
                    '2024-02-01T00:00:00+00:00', 'ok')
            """
        )
        conn.execute(
            """
            INSERT INTO statements(source_file_id, account_id, period_start, period_end)
            VALUES (1, 1, '2024-01-01', '2024-01-31')
            """
        )
        conn.execute(
            """
            INSERT INTO transactions(
                account_id, statement_id, source_file_id, trade_date, txn_type,
                instrument_id, quantity, net_amount, currency, raw_line
            ) VALUES (1, 1, 1, '2024-01-10', 'buy', 2, 5, -50, 'CAD', 'BUY ABC')
            """
        )
        conn.execute(
            """
            INSERT INTO position_snapshots(
                statement_id, account_id, as_of_date, instrument_id, quantity,
                market_value, currency, raw_line
            ) VALUES (1, 1, '2024-01-31', 1, 5, 55, 'CAD', 'ABC 5')
            """
        )
        conn.execute(
            """
            INSERT INTO cash_balances(
                statement_id, account_id, as_of_date, currency, opening_balance, closing_balance
            ) VALUES (1, 1, '2024-01-31', 'CAD', 100, 50)
            """
        )
        conn.execute(
            """
            INSERT INTO position_transaction_links(snapshot_id, transaction_id, quantity_attributed)
            VALUES (1, 1, 5)
            """
        )
        conn.execute(
            """
            INSERT INTO initial_positions(account_id, as_of_date, instrument_id, quantity, currency)
            VALUES (1, '2023-12-31', 2, 7, 'CAD')
            """
        )
        conn.execute(
            """
            INSERT INTO quarantine_transactions(source_file_id, account_id, raw_line, reason)
            VALUES (1, 1, 'UNKNOWN LEGACY ROW', 'legacy parser gap')
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_init_db_migrates_v5_identity_runs_scopes_and_evidence(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    _create_v5_database(db_path)

    sqlite_db.init_db(db_path)

    with sqlite_db.session(db_path) as conn:
        assert conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0] == "8"
        assert conn.execute(
            "SELECT COUNT(*) FROM instrument_ticker_changes"
        ).fetchone()[0] == 0
        instruments = conn.execute(
            "SELECT instrument_id, instrument_key FROM instruments"
        ).fetchall()
        assert len(instruments) == 1
        canonical_id = instruments[0]["instrument_id"]
        assert instruments[0]["instrument_key"].startswith("ik1|EQUITY|ABC|CAD")

        transaction = conn.execute(
            """
            SELECT instrument_id, ingestion_run_id, evidence_id, position_delta,
                   cash_delta, cash_effective_date
              FROM transactions
            """
        ).fetchone()
        assert dict(transaction) == {
            "instrument_id": canonical_id,
            "ingestion_run_id": 1,
            "evidence_id": transaction["evidence_id"],
            "position_delta": 5.0,
            "cash_delta": -50.0,
            "cash_effective_date": "2024-01-10",
        }
        assert transaction["evidence_id"] is not None

        statement = conn.execute(
            "SELECT ingestion_run_id, statement_key FROM statements"
        ).fetchone()
        assert statement["ingestion_run_id"] == 1
        assert statement["statement_key"].startswith("sk1:")

        scope = conn.execute(
            "SELECT completeness, can_clear_omitted FROM snapshot_sets WHERE section_type = 'positions'"
        ).fetchone()
        assert tuple(scope) == ("unknown", 0)
        assert conn.execute(
            "SELECT COUNT(*) FROM position_snapshots WHERE evidence_id IS NOT NULL"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM cash_balances WHERE evidence_id IS NOT NULL"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM quarantine_transactions WHERE evidence_id IS NOT NULL"
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM position_transaction_links").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'source_pages'"
        ).fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE transactions SET currency = 'EUR'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE transactions SET trade_date = '2024-02-30'")
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_reconciliation_schema_records_residual_without_adjustment(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        institution_id = sqlite_db.upsert_institution(conn, "TST", "Test")
        account_id = sqlite_db.upsert_account(
            conn,
            institution_id=institution_id,
            account_number="A-1",
        )
        conn.execute(
            """
            INSERT INTO reconciliation_results(
                reconciliation_key, kind, account_id, currency, opening_value,
                summed_deltas, expected_close, reported_close, residual,
                tolerance, status, reason
            ) VALUES ('test:residual', 'cash', ?, 'CAD', 100, -40, 60, 55,
                      -5, 0.01, 'unexplained_residual', 'missing source row')
            """,
            (account_id,),
        )
        result = conn.execute(
            "SELECT expected_close, reported_close, residual, status FROM reconciliation_results"
        ).fetchone()
        transaction_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    assert tuple(result) == (60.0, 55.0, -5.0, "unexplained_residual")
    assert transaction_count == 0


def test_writer_persists_canonical_identity_scoped_rows_and_evidence(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    statement = ParsedStatement(
        account=ParsedAccount(account_number="A-1", account_type="Margin"),
        period_start="2024-01-01",
        period_end="2024-01-31",
        snapshot_sets=[
            ParsedSnapshotSet("CAD", "positions", "complete"),
            ParsedSnapshotSet("CAD", "cash", "complete"),
        ],
        transactions=[
            ParsedTxn(
                trade_date="2024-01-10",
                settle_date="2024-01-12",
                txn_type="buy",
                instrument=ParsedInstrument("equity", "ABC", "CAD"),
                quantity=5,
                price=10,
                gross_amount=50,
                commission=0,
                other_fees=0,
                net_amount=-50,
                currency="CAD",
                description="buy ABC",
                raw_line="Jan 10 Buy ABC 5 10.00 -50.00",
            ),
            ParsedTxn(
                trade_date="2024-01-15",
                settle_date=None,
                txn_type="stock_split",
                instrument=ParsedInstrument("equity", "ABC", "CAD"),
                quantity=2,
                price=None,
                gross_amount=None,
                commission=None,
                other_fees=None,
                net_amount=None,
                currency="CAD",
                description="generic stock split",
                raw_line="Jan 15 ABC stock split 2",
            ),
        ],
        positions=[
            ParsedPosition(
                instrument=ParsedInstrument("equity", "ABC", "CAD"),
                quantity=5,
                avg_cost=10,
                book_value=50,
                market_price=11,
                market_value=55,
                unrealized_pnl=5,
                currency="CAD",
                raw_line="ABC 5 10.00 55.00",
            )
        ],
        cash_balances=[
            ParsedCashBalance(
                currency="CAD",
                opening_balance=100,
                closing_balance=50,
                raw_line="Opening 100.00\nClosing 50.00",
            )
        ],
    )
    pdf = PdfText(
        relpath="Statements/Test/writer.pdf",
        page_count=1,
        pages=["synthetic"],
        sha256="c" * 64,
        size_bytes=9,
    )

    with sqlite_db.session(db_path) as conn:
        source_file_id = _record_source_file(
            conn,
            pdf,
            parser_name="synthetic",
            parser_version="1",
            parse_status="ok",
        )
        _write_statement(
            conn,
            source_file_id=source_file_id,
            institution_code="TST",
            stmt=statement,
        )
        row = conn.execute(
            """
            SELECT i.instrument_key, t.position_delta, t.cash_delta,
                   t.cash_effective_date,
                   t.evidence_id AS transaction_evidence_id,
                   ps.evidence_id AS position_evidence_id,
                   cb.evidence_id AS cash_evidence_id,
                   ss.completeness, ss.can_clear_omitted
              FROM transactions t
              JOIN instruments i ON i.instrument_id = t.instrument_id
               JOIN position_snapshots ps ON ps.statement_id = t.statement_id
               JOIN cash_balances cb ON cb.statement_id = t.statement_id
               JOIN snapshot_sets ss ON ss.snapshot_set_id = ps.snapshot_set_id
             WHERE t.txn_type = 'buy'
            """
        ).fetchone()
        generic_split_delta = conn.execute(
            "SELECT position_delta FROM transactions WHERE txn_type = 'stock_split'"
        ).fetchone()[0]
        cash_evidence = conn.execute(
            """
            SELECT raw_text FROM source_evidence
             WHERE evidence_id = (SELECT evidence_id FROM cash_balances)
            """
        ).fetchone()[0]

    assert row["instrument_key"].startswith("ik1|EQUITY|ABC|CAD")
    assert (
        row["position_delta"],
        row["cash_delta"],
        row["cash_effective_date"],
    ) == (5.0, -50.0, "2024-01-12")
    evidence_ids = {
        row["transaction_evidence_id"],
        row["position_evidence_id"],
        row["cash_evidence_id"],
    }
    assert None not in evidence_ids
    assert len(evidence_ids) == 3
    assert row["completeness"] == "complete"
    assert row["can_clear_omitted"] == 1
    assert cash_evidence == "Opening 100.00\nClosing 50.00"
    assert generic_split_delta is None


def test_failed_attempt_keeps_active_run_pointer(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    pdf = PdfText(
        relpath="Statements/Test/attempts.pdf",
        page_count=1,
        pages=["synthetic"],
        sha256="d" * 64,
        size_bytes=9,
    )
    with sqlite_db.session(db_path) as conn:
        source_file_id = _record_source_file(
            conn,
            pdf,
            parser_name="synthetic",
            parser_version="1",
            parse_status="ok",
        )
        active_before = sqlite_db.active_ingestion_run_id(conn, source_file_id)
        _record_source_file(
            conn,
            pdf,
            parser_name="synthetic",
            parser_version="2",
            parse_status="failed",
        )
        active_after = sqlite_db.active_ingestion_run_id(conn, source_file_id)
        statuses = [
            row[0]
            for row in conn.execute(
                "SELECT status FROM ingestion_runs WHERE source_file_id = ? "
                "ORDER BY ingestion_run_id",
                (source_file_id,),
            )
        ]

    assert active_after == active_before
    assert statuses == ["active", "failed"]
