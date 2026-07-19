"""Regression coverage for dated ticker changes across ledger consumers."""
from __future__ import annotations

import calendar

from ledger.db import sqlite as sqlite_db
from ledger.holdings import holdings_at
from ledger.ingest.reconcile import rebuild_reconciliation_results
from ledger.parsers.cibc import _classify_activity as classify_cibc
from ledger.parsers.hsbc import _classify_activity as classify_hsbc
from ledger.parsers.rbc import _classify_activity as classify_rbc
from ledger.parsers.td import _classify as classify_td
from ledger.ticker_changes import (
    explicit_ticker_change_symbols,
    record_ticker_change,
    ticker_segments,
)

from .db_fixtures import seed_position, seed_source, seed_statement


def _account(conn) -> int:
    institution_id = sqlite_db.upsert_institution(conn, "TST", "Test Broker")
    return sqlite_db.upsert_account(
        conn,
        institution_id=institution_id,
        account_number="A-1",
        account_type="Margin",
        base_currency="CAD",
    )


def _statement(conn, account_id: int, month: str) -> int:
    source_id = seed_source(conn, f"Statements/Test/{month}.pdf")
    year, month_number = (int(part) for part in month.split("-"))
    last_day = calendar.monthrange(year, month_number)[1]
    return seed_statement(
        conn,
        account_id=account_id,
        source_file_id=source_id,
        period_start=f"{month}-01",
        period_end=f"{month}-{last_day:02d}",
    )


def test_explicit_change_parser_rejects_direction_words_and_ambiguity():
    assert explicit_ticker_change_symbols("SYMBOL CHANGE FROM FB TO META") == (
        "FB",
        "META",
    )
    assert explicit_ticker_change_symbols("TRANSFER FROM 123 TO 456") is None
    assert explicit_ticker_change_symbols("NAME CHANGE ACME CORPORATION") is None


def test_all_broker_classifiers_retain_explicit_ticker_change_verbs():
    assert classify_cibc("Name Change", "Name Change FROM OLD TO NEW") == "name_change"
    assert classify_rbc("NAME CHANGE FROM", "OLD TO NEW") == "name_change"
    assert classify_td("Ticker Change FROM OLD TO NEW") == "name_change"
    assert classify_hsbc("NAME_CHANGE") == "name_change"


def test_ticker_change_reconciles_and_reconstructs_one_lineage(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        old_id = sqlite_db.upsert_instrument(
            conn, asset_type="equity", symbol="OLD", currency="CAD"
        )
        new_id = sqlite_db.upsert_instrument(
            conn, asset_type="equity", symbol="NEW", currency="CAD"
        )
        jan = _statement(conn, account_id, "2024-01")
        feb = _statement(conn, account_id, "2024-02")
        seed_position(
            conn,
            statement_id=jan,
            instrument_id=old_id,
            quantity=10,
            currency="CAD",
        )
        conn.execute(
            "UPDATE position_snapshots SET avg_cost = 8, book_value = 80 WHERE statement_id = ?",
            (jan,),
        )
        seed_position(
            conn,
            statement_id=feb,
            instrument_id=new_id,
            quantity=10,
            currency="CAD",
        )
        transaction_id = int(
            conn.execute(
                """
                INSERT INTO transactions(
                    account_id, statement_id, trade_date, txn_type,
                    instrument_id, currency, description, raw_line,
                    resolution_method, resolution_confidence
                ) VALUES (?, ?, '2024-02-10', 'name_change', ?, 'CAD',
                          'SYMBOL CHANGE FROM OLD TO NEW',
                          'SYMBOL CHANGE FROM OLD TO NEW',
                          'printed_ticker_change', 1.0)
                RETURNING transaction_id
                """,
                (account_id, feb, old_id),
            ).fetchone()[0]
        )
        record_ticker_change(
            conn,
            from_instrument_id=old_id,
            to_instrument_id=new_id,
            effective_date="2024-02-10",
            conversion_ratio=1.0,
            transaction_id=transaction_id,
            evidence_id=None,
            resolution_method="printed_ticker_change",
            resolution_confidence=1.0,
        )
        segments = ticker_segments(conn, "OLD")

    assert [(row.symbol, row.valid_from, row.valid_to) for row in segments] == [
        ("OLD", None, "2024-02-10"),
        ("NEW", "2024-02-10", None),
    ]

    rebuild_reconciliation_results(db_path)
    with sqlite_db.session(db_path) as conn:
        results = conn.execute(
            """
            SELECT i.symbol, rr.opening_value, rr.summed_deltas,
                   rr.expected_close, rr.reported_close, rr.status
              FROM reconciliation_results rr
              JOIN instruments i ON i.instrument_id = rr.instrument_id
             WHERE rr.kind = 'position' AND rr.statement_id = ?
             ORDER BY i.symbol
            """,
            (feb,),
        ).fetchall()
    assert [tuple(row) for row in results] == [
        ("NEW", 0.0, 10.0, 10.0, 10.0, "reconciled"),
        ("OLD", 10.0, -10.0, 0.0, 0.0, "reconciled"),
    ]

    rows = holdings_at("2024-02-15", path=db_path, market_path=tmp_path / "missing.duckdb")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "NEW"
    assert rows[0]["quantity"] == 10.0
    assert rows[0]["avg_cost"] == 8.0
    assert rows[0]["book_value"] == 80.0
    assert rows[0]["ticker_symbols"] == ["OLD", "NEW"]
    assert "OLD" in rows[0]["holding_key"]
