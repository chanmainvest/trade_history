"""Read-only Verify-workflow quality payload coverage."""
from __future__ import annotations

from ledger.api.routes import statements as statement_route
from ledger.db import sqlite as sqlite_db

from .db_fixtures import seed_cash, seed_position, seed_source, seed_statement


def test_statement_list_and_detail_report_quality_without_mutation(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        institution_id = sqlite_db.upsert_institution(conn, "TST", "Test Broker")
        account_id = sqlite_db.upsert_account(
            conn,
            institution_id=institution_id,
            account_number="A-1",
            account_type="Margin",
        )
        source_file_id = seed_source(conn, "Statements/Test/statement.pdf")
        statement_id = seed_statement(
            conn,
            account_id=account_id,
            source_file_id=source_file_id,
            period_end="2024-01-31",
        )
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=statement_id,
            instrument_id=instrument_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        cash_balance_id = seed_cash(
            conn,
            statement_id=statement_id,
            currency="CAD",
            opening_balance=90,
            closing_balance=95,
        )
        run_id = conn.execute(
            "SELECT active_ingestion_run_id FROM source_files WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()[0]
        evidence_id = conn.execute(
            "SELECT evidence_id FROM source_evidence WHERE source_file_id = ? LIMIT 1",
            (source_file_id,),
        ).fetchone()[0]
        summary_scope_id = sqlite_db.upsert_snapshot_set(
            conn,
            statement_id=statement_id,
            account_id=account_id,
            as_of_date="2024-01-31",
            currency="CAD",
            section_type="summary",
            scope_key="default",
            completeness="complete",
            evidence_id=evidence_id,
            reported_total=195,
            validation_status="valid",
        )
        sqlite_db.upsert_snapshot_set(
            conn,
            statement_id=statement_id,
            account_id=account_id,
            as_of_date="2024-01-31",
            currency="CAD",
            section_type="positions",
            scope_key="secondary",
            completeness="partial",
            evidence_id=None,
            reported_total=None,
            validation_status="warning",
        )
        conn.execute(
            """
            INSERT INTO transactions(
                account_id, statement_id, source_file_id, ingestion_run_id,
                trade_date, txn_type, currency, resolution_method, description, raw_line
            ) VALUES (?, ?, ?, ?, '2024-01-15', 'buy', 'CAD',
                      'unresolved_printed_identity', 'unresolved security', 'synthetic transaction evidence')
            """,
            (account_id, statement_id, source_file_id, run_id),
        )
        conn.execute(
            """
            INSERT INTO quarantine_transactions(
                source_file_id, ingestion_run_id, statement_id, account_id,
                occurrence, raw_line, reason
            ) VALUES (?, ?, ?, ?, 1, 'synthetic quarantine evidence', 'unparsed row')
            """,
            (source_file_id, run_id, statement_id, account_id),
        )
        cash_snapshot_set_id = conn.execute(
            "SELECT snapshot_set_id FROM cash_balances WHERE cash_balance_id = ?",
            (cash_balance_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO reconciliation_results(
                reconciliation_key, kind, account_id, statement_id, snapshot_set_id,
                currency, residual, tolerance, status, reason
            ) VALUES ('test:cash:residual', 'cash', ?, ?, ?, 'CAD', 2, 0.01,
                      'unexplained_residual', 'test residual')
            """,
            (account_id, statement_id, cash_snapshot_set_id),
        )
        conn.execute(
            """
            INSERT INTO reconciliation_results(
                reconciliation_key, kind, account_id, statement_id, snapshot_set_id,
                currency, residual, tolerance, status
            ) VALUES ('test:summary:ok', 'statement_total', ?, ?, ?, 'CAD', 0, 0.01,
                      'reconciled')
            """,
            (account_id, statement_id, summary_scope_id),
        )

    with sqlite_db.session(db_path) as conn:
        listed = statement_route._list_statement_rows(conn, 10)

    assert len(listed) == 1
    row = listed[0]
    assert row["parser_name"] == "synthetic-test"
    assert row["parser_version"] == "1"
    assert row["active_run_status"] == "active"
    assert row["scope_count"] == 4
    assert row["complete_scope_count"] == 3
    assert row["incomplete_scope_count"] == 1
    assert row["unresolved_identity_count"] == 1
    assert row["quarantine_count"] == 1
    assert row["unreconciled_count"] == 1
    assert row["quality_flags"] == ["unresolved", "incomplete", "unreconciled"]

    detail = statement_route._load_statement_rows(statement_id, path=db_path)

    assert detail is not None
    assert detail["statement"]["quality"]["quality_flags"] == row["quality_flags"]
    assert detail["source_file"]["active_run_status"] == "active"
    assert {scope["section_type"] for scope in detail["scopes"]} == {"positions", "cash", "summary"}
    assert detail["summary_totals"][0]["reported_total"] == 195.0
    assert {result["status"] for result in detail["reconciliation_results"]} == {
        "reconciled",
        "unexplained_residual",
    }
    assert {reference["kind"] for reference in detail["references"]} >= {
        "cash",
        "summary",
        "transaction",
        "position",
        "quarantine",
    }


def test_statement_list_leaves_v6_quality_facts_unavailable_for_legacy_schema(tmp_path):
    """An old read-only ledger must not be called complete just because it lacks v6 tables."""
    db_path = tmp_path / "legacy.sqlite"
    with sqlite_db.session(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE institutions (
                institution_id INTEGER PRIMARY KEY,
                code TEXT NOT NULL,
                display_name TEXT NOT NULL
            );
            CREATE TABLE accounts (
                account_id INTEGER PRIMARY KEY,
                institution_id INTEGER NOT NULL,
                account_number TEXT NOT NULL,
                account_type TEXT,
                nickname TEXT
            );
            CREATE TABLE source_files (
                source_file_id INTEGER PRIMARY KEY,
                relpath TEXT NOT NULL,
                sha256 TEXT,
                parser_name TEXT,
                parser_version TEXT,
                parse_status TEXT
            );
            CREATE TABLE statements (
                statement_id INTEGER PRIMARY KEY,
                source_file_id INTEGER NOT NULL,
                account_id INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                statement_type TEXT NOT NULL
            );
            INSERT INTO institutions VALUES (1, 'LEG', 'Legacy Broker');
            INSERT INTO accounts VALUES (1, 1, 'L-1', 'Margin', NULL);
            INSERT INTO source_files VALUES (
                1, 'Statements/Legacy/statement.pdf', 'digest', 'legacy', '1', 'ok'
            );
            INSERT INTO statements VALUES (1, 1, 1, '2020-01-01', '2020-01-31', 'monthly');
            """
        )
        rows = statement_route._list_statement_rows(conn, 10)

    assert len(rows) == 1
    assert rows[0]["scope_count"] == 0
    assert rows[0]["reconciliation_result_count"] == 0
    assert rows[0]["quality_flags"] == []


def test_statement_quality_batches_large_picker_lists_for_sqlite_limits():
    batches = list(statement_route._statement_id_batches(list(range(2_000))))

    assert [len(batch) for batch in batches] == [900, 900, 200]
    assert [statement_id for batch in batches for statement_id in batch] == list(range(2_000))


def test_statement_detail_does_not_leak_quarantine_from_sibling_statement(tmp_path):
    """One PDF can contain several statements; review rows remain statement-owned."""
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        institution_id = sqlite_db.upsert_institution(conn, "TST", "Test Broker")
        first_account = sqlite_db.upsert_account(
            conn,
            institution_id=institution_id,
            account_number="A-CAD",
            account_type="Margin",
        )
        second_account = sqlite_db.upsert_account(
            conn,
            institution_id=institution_id,
            account_number="A-USD",
            account_type="Margin",
            base_currency="USD",
        )
        source_id = seed_source(conn, "Statements/Test/two-accounts.pdf")
        first_statement = seed_statement(
            conn,
            account_id=first_account,
            source_file_id=source_id,
            period_end="2024-01-31",
        )
        second_statement = seed_statement(
            conn,
            account_id=second_account,
            source_file_id=source_id,
            period_end="2024-01-31",
        )
        run_id = conn.execute(
            "SELECT active_ingestion_run_id FROM source_files WHERE source_file_id = ?",
            (source_id,),
        ).fetchone()[0]
        conn.executemany(
            """
            INSERT INTO quarantine_transactions(
                source_file_id, ingestion_run_id, statement_id, account_id,
                occurrence, raw_line, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (source_id, run_id, first_statement, first_account, 1, "first row", "first issue"),
                (source_id, run_id, second_statement, second_account, 2, "second row", "second issue"),
                (source_id, run_id, None, None, 3, "source row", "unassigned source issue"),
            ],
        )

    first = statement_route._load_statement_rows(first_statement, path=db_path)
    second = statement_route._load_statement_rows(second_statement, path=db_path)

    assert first is not None and second is not None
    assert [row["reason"] for row in first["quarantine"]] == ["first issue"]
    assert [row["reason"] for row in second["quarantine"]] == ["second issue"]
