"""Regression coverage for the Phase 3 staged source activation contract."""
from __future__ import annotations

from copy import deepcopy

import pytest

from ledger.db import sqlite as sqlite_db
from ledger.ingest import pipeline
from ledger.ingest.pipeline import (
    _unchanged_source_file_id,
    activate_source_result,
    export_active_ingestion_logs,
)
from ledger.parsers.td import TDParser
from ledger.parsers.types import (
    ParsedAccount,
    ParsedCashBalance,
    ParsedInstrument,
    ParsedPosition,
    ParsedQuarantine,
    ParsedSnapshotSet,
    ParsedStatement,
    ParsedTxn,
    ParseResult,
    SourceSpan,
)
from ledger.pdf_text import PdfText


def _pdf(*, sha256: str = "source-sha") -> PdfText:
    return PdfText(
        relpath="Statements/Test/source.pdf",
        page_count=1,
        pages=["synthetic statement text"],
        sha256=sha256,
        size_bytes=24,
    )


def _statement(
    *,
    period_start: str = "2024-01-01",
    period_end: str = "2024-01-31",
    symbol: str = "ABC",
    asset_type: str = "equity",
    name: str | None = None,
    quarantine: bool = False,
) -> ParsedStatement:
    instrument = ParsedInstrument(asset_type, symbol, "CAD", name=name)
    return ParsedStatement(
        account=ParsedAccount(account_number="A-1", account_type="Margin"),
        period_start=period_start,
        period_end=period_end,
        snapshot_sets=[
            ParsedSnapshotSet("CAD", "positions", "complete"),
            ParsedSnapshotSet("CAD", "cash", "complete"),
        ],
        transactions=[
            ParsedTxn(
                trade_date=period_start,
                settle_date=period_start,
                txn_type="buy",
                instrument=instrument,
                quantity=2,
                price=10,
                gross_amount=20,
                commission=0,
                other_fees=0,
                net_amount=-20,
                currency="CAD",
                description=name or symbol,
                raw_line=f"BUY {name or symbol}",
                source_span=SourceSpan(raw_text=f"BUY {name or symbol}", page_number=1),
            )
        ],
        positions=[
            ParsedPosition(
                instrument=ParsedInstrument(asset_type, symbol, "CAD", name=name),
                quantity=2,
                avg_cost=10,
                book_value=20,
                market_price=11,
                market_value=22,
                unrealized_pnl=2,
                currency="CAD",
                raw_line=f"{name or symbol} 2",
                source_span=SourceSpan(raw_text=f"{name or symbol} 2", page_number=1),
            )
        ],
        cash_balances=[
            ParsedCashBalance(
                currency="CAD",
                opening_balance=100,
                closing_balance=80,
                raw_line="Opening 100 / Closing 80",
                source_span=SourceSpan(raw_text="Opening 100 / Closing 80", page_number=1),
            )
        ],
        quarantine=(
            [
                ParsedQuarantine(
                    raw_line="PRIVATE UNPARSED DETAIL",
                    reason="synthetic parser gap",
                    source_span=SourceSpan(raw_text="PRIVATE UNPARSED DETAIL", page_number=1),
                )
            ]
            if quarantine
            else []
        ),
    )


def _result(*statements: ParsedStatement) -> ParseResult:
    return ParseResult(
        parser_name="td",
        parser_version=TDParser.VERSION,
        statements=list(statements),
    )


def _activate(conn, result: ParseResult, *, pdf: PdfText | None = None) -> dict:
    return activate_source_result(
        conn,
        pdf=pdf or _pdf(),
        institution_code="TST",
        parser_name="td",  # a registered parser, so the cache contract is testable
        parser_version=TDParser.VERSION,
        result=result,
    )


def test_source_activation_replaces_all_prior_derived_rows(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        first = _activate(
            conn,
            _result(
                _statement(),
                _statement(period_start="2024-02-01", period_end="2024-02-29", symbol="XYZ"),
            ),
        )
        prior_run_id = first["ingestion_run_id"]
        replacement = _activate(conn, _result(_statement(symbol="XYZ")))
        source_file_id = replacement["source_file_id"]

        assert conn.execute(
            "SELECT COUNT(*) FROM statements WHERE source_file_id = ?", (source_file_id,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE source_file_id = ?", (source_file_id,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM position_snapshots WHERE statement_id IN "
            "(SELECT statement_id FROM statements WHERE source_file_id = ?)",
            (source_file_id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM ingestion_runs WHERE ingestion_run_id = ?", (prior_run_id,)
        ).fetchone()[0] == 0
        assert sqlite_db.active_ingestion_run_id(conn, source_file_id) == replacement["ingestion_run_id"]


def test_stage_write_failure_rolls_back_to_the_previous_active_extraction(tmp_path, monkeypatch):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        first = _activate(conn, _result(_statement(symbol="ABC")))
        source_file_id = first["source_file_id"]
        active_before = sqlite_db.active_ingestion_run_id(conn, source_file_id)
        old_source = conn.execute(
            "SELECT sha256, parser_version, parse_status FROM source_files WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()

        original_write = pipeline._write_statement

        def fail_after_first_row(*args, **kwargs):
            original_write(*args, **kwargs)
            raise RuntimeError("synthetic staged writer failure")

        monkeypatch.setattr(pipeline, "_write_statement", fail_after_first_row)
        with pytest.raises(RuntimeError, match="synthetic staged writer failure"):
            _activate(
                conn,
                _result(_statement(symbol="XYZ")),
                pdf=_pdf(sha256="replacement-sha"),
            )

        active_after = sqlite_db.active_ingestion_run_id(conn, source_file_id)
        rows = conn.execute(
            """
            SELECT i.symbol
              FROM transactions t
              JOIN instruments i ON i.instrument_id = t.instrument_id
             WHERE t.source_file_id = ?
            """,
            (source_file_id,),
        ).fetchall()
        source_after = conn.execute(
            "SELECT sha256, parser_version, parse_status FROM source_files WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()

    assert active_after == active_before
    assert [row["symbol"] for row in rows] == ["ABC"]
    assert tuple(source_after) == tuple(old_source)


def test_forced_reingest_keeps_active_hash_counts_and_instruments_stable(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    original = _result(_statement(symbol="ABC"))
    with sqlite_db.session(db_path) as conn:
        first = _activate(conn, deepcopy(original))
        first_counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("statements", "transactions", "position_snapshots", "cash_balances", "instruments")
        }
        second = _activate(conn, deepcopy(original))
        second_counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("statements", "transactions", "position_snapshots", "cash_balances", "instruments")
        }
        active_hash = conn.execute(
            "SELECT content_hash FROM ingestion_runs WHERE ingestion_run_id = ?",
            (second["ingestion_run_id"],),
        ).fetchone()[0]

    assert first["content_hash"] == second["content_hash"] == active_hash
    assert first_counts == second_counts
    assert second_counts["instruments"] == 1


def test_cache_invalidation_includes_parser_and_reviewed_resolver_state(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        activation = _activate(conn, _result(_statement()))
        source_file_id = activation["source_file_id"]
        assert _unchanged_source_file_id(conn, relpath=_pdf().relpath, sha256=_pdf().sha256) == source_file_id

        conn.execute(
            "UPDATE ingestion_runs SET parser_version = 'stale' WHERE ingestion_run_id = ?",
            (activation["ingestion_run_id"],),
        )
        assert _unchanged_source_file_id(conn, relpath=_pdf().relpath, sha256=_pdf().sha256) is None
        conn.execute(
            "UPDATE ingestion_runs SET parser_version = ? WHERE ingestion_run_id = ?",
            (TDParser.VERSION, activation["ingestion_run_id"]),
        )

        institution_id = sqlite_db.upsert_institution(conn, "TST", "Test")
        target_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="CANON",
            currency="CAD",
        )
        conn.execute(
            "INSERT INTO instrument_aliases(instrument_id, alias, institution_id) VALUES (?, ?, ?)",
            (target_id, "Reviewed Alias", institution_id),
        )
        assert _unchanged_source_file_id(conn, relpath=_pdf().relpath, sha256=_pdf().sha256) is None


def test_reviewed_alias_is_recorded_and_quarantine_log_is_an_idempotent_index(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    logs = tmp_path / "logs"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        institution_id = sqlite_db.upsert_institution(conn, "TST", "Test")
        target_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="FUND1",
            currency="CAD",
        )
        conn.execute(
            "INSERT INTO instrument_aliases(instrument_id, alias, institution_id) VALUES (?, ?, ?)",
            (target_id, "MYSTERY FUND", institution_id),
        )
        activation = _activate(
            conn,
            _result(
                _statement(
                    symbol="MYSTERY_FUND",
                    asset_type="mutual_fund",
                    name="Mystery Fund",
                    quarantine=True,
                )
            ),
        )
        resolution = conn.execute(
            """
            SELECT i.symbol, t.resolution_method, t.resolution_confidence,
                   t.resolution_evidence_id
              FROM transactions t
              JOIN instruments i ON i.instrument_id = t.instrument_id
             WHERE t.ingestion_run_id = ?
            """,
            (activation["ingestion_run_id"],),
        ).fetchone()

    first_export = export_active_ingestion_logs(path=db_path, log_dir=logs)
    first_content = (logs / "quarantine.jsonl").read_text(encoding="utf-8")
    second_export = export_active_ingestion_logs(path=db_path, log_dir=logs)
    second_content = (logs / "quarantine.jsonl").read_text(encoding="utf-8")
    attempts_content = (logs / "ingestion_attempts.jsonl").read_text(encoding="utf-8")

    assert tuple(resolution) == ("FUND1", "reviewed_alias", 1.0, resolution["resolution_evidence_id"])
    assert resolution["resolution_evidence_id"] is not None
    assert first_export == second_export == {"attempts": 1, "quarantine": 1, "skipped": 0}
    assert first_content == second_content
    assert "PRIVATE UNPARSED DETAIL" not in first_content
    assert "PRIVATE UNPARSED DETAIL" not in attempts_content
    assert '"ingestion_run_id"' in first_content
    assert '"evidence_id"' in first_content
