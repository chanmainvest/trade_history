"""Executable acceptance regressions for the extraction refactor phases."""
from __future__ import annotations

import pytest

from ledger.db import sqlite as sqlite_db
from ledger.ingest.pipeline import _record_source_file, _write_statement, activate_source_result
from ledger.parsers.cibc import CIBCParser
from ledger.parsers.rbc import RBCParser
from ledger.parsers.td import TDParser
from ledger.parsers.types import ParsedAccount, ParsedStatement, ParseResult
from ledger.pdf_text import PdfText

from .fixture_loader import load_fixture


def test_same_ordinary_instrument_upsert_returns_one_id(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        first = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="AAA",
            currency="CAD",
        )
        second = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="AAA",
            currency="CAD",
        )
    assert first == second


def test_rbc_cad_and_usd_children_both_survive_persistence(tmp_path):
    result = RBCParser().parse(load_fixture("rbc/monthly_dual_currency.txt"))
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    pdf = load_fixture("rbc/monthly_dual_currency.txt")
    with sqlite_db.session(db_path) as conn:
        source_file_id = _record_source_file(
            conn,
            pdf,
            parser_name="rbc",
            parser_version="2.0.0",
            parse_status="ok",
        )
        for statement in result.statements:
            _write_statement(
                conn,
                source_file_id=source_file_id,
                institution_code="RBC_DI",
                stmt=statement,
            )
        currencies = {
            row[0]
            for row in conn.execute(
                """
                SELECT DISTINCT ps.currency
                  FROM position_snapshots ps
                  JOIN statements s ON s.statement_id = ps.statement_id
                 WHERE s.source_file_id = ?
                """,
                (source_file_id,),
            )
        }
    assert currencies == {"CAD", "USD"}


def test_td_full_header_bundle_emits_every_month():
    result = TDParser().parse(
        load_fixture("td/full_header_bundle_known_broken.txt")
    )
    assert {
        (statement.period_start, statement.period_end)
        for statement in result.statements
    } == {
        ("2020-01-01", "2020-01-31"),
        ("2020-02-01", "2020-02-29"),
    }


def test_source_reparse_removes_obsolete_statement_outputs(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    pdf = PdfText(
        relpath="Statements/Test/source.pdf",
        page_count=1,
        pages=["synthetic statement text"],
        sha256="a" * 64,
        size_bytes=24,
    )
    account = ParsedAccount(account_number="A-1", account_type="Margin")
    first = ParseResult(
        parser_name="synthetic",
        parser_version="1",
        statements=[
            ParsedStatement(account=account, period_start="2024-01-01", period_end="2024-01-31"),
            ParsedStatement(account=account, period_start="2024-02-01", period_end="2024-02-29"),
        ],
    )
    replacement = ParseResult(
        parser_name="synthetic",
        parser_version="2",
        statements=[
            ParsedStatement(
                account=ParsedAccount(account_number="A-1", account_type="Margin"),
                period_start="2024-01-01",
                period_end="2024-01-31",
            )
        ],
    )
    with sqlite_db.session(db_path) as conn:
        first_activation = activate_source_result(
            conn,
            pdf=pdf,
            institution_code="TST",
            parser_name="synthetic",
            parser_version="1",
            result=first,
        )
        source_file_id = first_activation["source_file_id"]
        activate_source_result(
            conn,
            pdf=pdf,
            institution_code="TST",
            parser_name="synthetic",
            parser_version="2",
            result=replacement,
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM statements WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()[0]
    assert count == 1


def test_failed_attempt_preserves_last_good_source_activation(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    pdf = PdfText(
        relpath="Statements/Test/source.pdf",
        page_count=1,
        pages=["synthetic statement text"],
        sha256="a" * 64,
        size_bytes=24,
    )
    with sqlite_db.session(db_path) as conn:
        activation = activate_source_result(
            conn,
            pdf=pdf,
            institution_code="TST",
            parser_name="synthetic",
            parser_version="1",
            result=ParseResult(
                parser_name="synthetic",
                parser_version="1",
                statements=[
                    ParsedStatement(
                        account=ParsedAccount(account_number="A-1", account_type="Margin"),
                        period_start="2024-01-01",
                        period_end="2024-01-31",
                    )
                ],
            ),
        )
        source_file_id = activation["source_file_id"]
        active_before = sqlite_db.active_ingestion_run_id(conn, source_file_id)
        with pytest.raises(ValueError, match="cannot activate invalid parser result"):
            activate_source_result(
                conn,
                pdf=pdf,
                institution_code="TST",
                parser_name="synthetic",
                parser_version="2",
                result=ParseResult(
                    parser_name="synthetic",
                    parser_version="2",
                    errors=["fatal parser output"],
                ),
            )
        _record_source_file(
            conn,
            pdf,
            parser_name="synthetic",
            parser_version="2",
            parse_status="failed",
        )
        row = conn.execute(
            "SELECT parse_status, parser_version FROM source_files WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()
        active_after = sqlite_db.active_ingestion_run_id(conn, source_file_id)
        statement_count = conn.execute(
            "SELECT COUNT(*) FROM statements WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()[0]
    assert (row["parse_status"], row["parser_version"]) == ("ok", "1")
    assert active_after == active_before
    assert statement_count == 1


def test_missing_cash_number_is_quarantined_not_zero():
    fixture = load_fixture("cibc/monthly_dual_currency.txt")
    fixture.pages = [
        page.replace(
            "Nov 30 Closing Cash Balance $1,000.00",
            "Nov 30 Closing Cash Balance NOT AVAILABLE",
        )
        for page in fixture.pages
    ]
    result = CIBCParser().parse(fixture)
    statement = result.statements[0]
    assert not any(
        cash.currency == "CAD" and cash.closing_balance == 0
        for cash in statement.cash_balances
    )
    assert any("Closing Cash Balance NOT AVAILABLE" in raw for raw, _ in statement.quarantine)


def test_schema_persists_explicit_reconciliation_results(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "reconciliation_results" in tables
