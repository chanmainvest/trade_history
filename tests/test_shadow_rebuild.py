"""Coverage for the non-destructive shadow-ledger workflow."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledger.db import sqlite as sqlite_db
from ledger.ingest.pipeline import run_ingest
from ledger.shadow import (
    _content_hash,
    build_shadow,
    cutover_shadow,
    export_curated_state,
    rollback_shadow,
    sign_off_report,
)

from .db_fixtures import seed_cash, seed_position, seed_source, seed_statement


def _seed_curated_source(path: Path) -> tuple[int, int, int]:
    sqlite_db.init_db(path)
    with sqlite_db.session(path) as conn:
        institution_id = sqlite_db.upsert_institution(conn, "TST", "Test Broker")
        account_id = 41
        conn.execute(
            """
            INSERT INTO accounts(
                account_id, institution_id, account_number, account_type, nickname,
                base_currency, opened_on, notes
            ) VALUES (?, ?, 'A-1', 'Margin', 'Long term', 'CAD', '2010-01-01', 'manual: account note')
            """,
            (account_id, institution_id),
        )
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        conn.execute(
            "INSERT INTO instrument_aliases(instrument_id, alias, institution_id) VALUES (?, 'ABC LTD', ?)",
            (instrument_id, institution_id),
        )
        conn.execute(
            """
            INSERT INTO instrument_identifier_lookups(
                identifier_type, asset_type, institution_code, normalized_name,
                display_name, currency, status, resolved_symbol, resolved_name, notes
            ) VALUES ('fund_code', 'mutual_fund', 'TST', 'MANUAL FUND', 'Manual Fund', 'CAD',
                      'resolved', 'MF001', 'Manual Fund', 'reviewed')
            """
        )
        source_id = seed_source(conn, "Statements/Test/2024-01.pdf")
        statement_id = seed_statement(
            conn,
            account_id=account_id,
            source_file_id=source_id,
            period_end="2024-01-31",
        )
        seed_position(
            conn,
            statement_id=statement_id,
            instrument_id=instrument_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        seed_cash(
            conn,
            statement_id=statement_id,
            currency="CAD",
            opening_balance=25,
            closing_balance=25,
        )
        conn.execute(
            """
            INSERT INTO initial_positions(
                account_id, as_of_date, instrument_id, quantity, avg_cost, currency, notes
            ) VALUES (?, '2023-12-01', ?, 3, 7, 'CAD', 'manual: opening lot')
            """,
            (account_id, instrument_id),
        )
        conn.execute(
            """
            INSERT INTO initial_cash(account_id, as_of_date, currency, balance, notes)
            VALUES (?, '2023-12-01', 'CAD', 40, 'manual: opening cash')
            """,
            (account_id,),
        )
        conn.execute(
            """
            INSERT INTO initial_cash(account_id, as_of_date, currency, balance, notes)
            VALUES (?, '2023-12-02', 'CAD', 50, NULL)
            """,
            (account_id,),
        )
        snapshot_set_id = conn.execute(
            "SELECT snapshot_set_id FROM position_snapshots WHERE statement_id = ?",
            (statement_id,),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO reconciliation_results(
                reconciliation_key, kind, account_id, statement_id, snapshot_set_id,
                instrument_id, currency, tolerance, status, reason
            ) VALUES ('review:position:note', 'position', ?, ?, ?, ?, 'CAD', 0.01,
                      'reconciled', 'reviewed annotation')
            """,
            (account_id, statement_id, snapshot_set_id, instrument_id),
        )
    path.with_name("config.json").write_text(
        json.dumps({"portfolios": [{"id": "long", "account_ids": [account_id]}]}),
        encoding="utf-8",
    )
    return account_id, instrument_id, statement_id


def test_export_curated_state_is_read_only_and_excludes_inferred_cash(tmp_path):
    source = tmp_path / "source.sqlite"
    _seed_curated_source(source)
    before = source.read_bytes()

    state = export_curated_state(source)

    assert source.read_bytes() == before
    assert state.counts() == {
        "accounts": 1,
        "aliases": 1,
        "identifier_lookups": 1,
        "initial_positions": 1,
        "initial_cash": 1,
        "annotations": 1,
        "annotation_components": 0,
        "unmapped_annotations": 0,
        "portfolio_config": 1,
        "portfolio_account_ids": 1,
        "unmapped_portfolio_account_ids": 0,
    }
    assert state.accounts[0]["nickname"] == "Long term"
    assert state.accounts[0]["opened_on"] == "2010-01-01"
    assert state.accounts[0]["notes"] == "manual: account note"
    assert state.initial_positions[0]["notes"] == "manual: opening lot"
    assert state.initial_cash[0]["notes"] == "manual: opening cash"
    assert state.config_sha256 is not None


def _fake_rebuild(target: Path, _statements: Path, _root: Path, _logs: Path) -> dict[str, object]:
    with sqlite_db.session(target) as conn:
        account_id = conn.execute("SELECT account_id FROM accounts WHERE account_number = 'A-1'").fetchone()[0]
        instrument_id = conn.execute("SELECT instrument_id FROM instruments WHERE symbol = 'ABC'").fetchone()[0]
        source_id = seed_source(conn, "Statements/Test/2024-01.pdf")
        statement_id = seed_statement(
            conn,
            account_id=account_id,
            source_file_id=source_id,
            period_end="2024-01-31",
        )
        seed_position(
            conn,
            statement_id=statement_id,
            instrument_id=instrument_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        seed_cash(
            conn,
            statement_id=statement_id,
            currency="CAD",
            opening_balance=25,
            closing_balance=25,
        )
    return {"fake": True}


def test_shadow_build_preserves_curated_state_and_requires_signed_cutover(tmp_path):
    source = tmp_path / "source.sqlite"
    _seed_curated_source(source)
    source_before = source.read_bytes()
    statements = tmp_path / "Statements"
    statements.mkdir()
    target = tmp_path / "ledger.vnext.sqlite"
    report = tmp_path / "ledger.vnext.report.json"

    result = build_shadow(
        source_db=source,
        target_db=target,
        statements_dir=statements,
        report_path=report,
        repo_root=tmp_path,
        rebuild_runner=_fake_rebuild,
    )

    assert source.read_bytes() == source_before
    assert result["reproducibility"]["status"] == "passed"
    assert result["pdf_manifest"]["before"] == result["pdf_manifest"]["after"]
    assert result["source"]["statement_coverage"][0]["account_ref"] != "A-1"
    assert result["shadow"]["scope_coverage"]
    assert "A-1" not in json.dumps(result)
    assert target.exists()
    assert (tmp_path / "ledger.vnext.config.json").exists()
    with sqlite_db.session(target) as conn:
        assert conn.execute("SELECT account_id FROM accounts WHERE account_number = 'A-1'").fetchone()[0] == 41
        assert conn.execute("SELECT nickname FROM accounts WHERE account_number = 'A-1'").fetchone()[0] == "Long term"
        assert conn.execute("SELECT opened_on FROM accounts WHERE account_number = 'A-1'").fetchone()[0] == "2010-01-01"
        assert conn.execute("SELECT notes FROM accounts WHERE account_number = 'A-1'").fetchone()[0] == "manual: account note"
        assert conn.execute("SELECT COUNT(*) FROM instrument_aliases WHERE alias = 'ABC LTD'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM initial_positions WHERE notes = 'manual: opening lot'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM initial_cash WHERE notes = 'manual: opening cash'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM initial_cash WHERE notes LIKE 'inferred:%'").fetchone()[0] >= 1
        assert conn.execute("SELECT COUNT(*) FROM reconciliation_results WHERE reconciliation_key = 'review:position:note'").fetchone()[0] == 1
    companion_config = json.loads((tmp_path / "ledger.vnext.config.json").read_text(encoding="utf-8"))
    assert companion_config["portfolios"][0]["account_ids"] == [41]

    baseline_hash = _content_hash(target)
    with sqlite_db.session(target) as conn:
        conn.execute("UPDATE position_snapshots SET market_value = market_value + 1")
    assert _content_hash(target) != baseline_hash
    with sqlite_db.session(target) as conn:
        conn.execute("UPDATE position_snapshots SET market_value = market_value - 1")
        conn.execute(
            "UPDATE reconciliation_results SET reason = 'fingerprint probe' "
            "WHERE reconciliation_key = 'review:position:note'"
        )
    assert _content_hash(target) != baseline_hash
    with sqlite_db.session(target) as conn:
        conn.execute(
            "UPDATE reconciliation_results SET reason = 'reviewed annotation' "
            "WHERE reconciliation_key = 'review:position:note'"
        )
    assert _content_hash(target) == baseline_hash

    signed = sign_off_report(report, reviewer="reviewer", confirmation="fixtures reviewed")
    assert signed["manual_review"]["status"] == "signed_off"
    cutover = cutover_shadow(
        source_db=source,
        shadow_db=target,
        report_path=report,
        backend_stopped=True,
        confirm_live_db="source.sqlite",
    )
    backup = Path(cutover["backup_db"])
    assert backup.read_bytes() == source_before
    assert source.exists() and not target.exists()

    rollback_shadow(
        live_db=source,
        backup_db=backup,
        backend_stopped=True,
        confirm_live_db="source.sqlite",
    )
    assert source.read_bytes() == source_before


def test_ingest_runner_accepts_an_isolated_database_path(tmp_path):
    live = tmp_path / "live.sqlite"
    target = tmp_path / "target.sqlite"
    statements = tmp_path / "Statements"
    logs = tmp_path / "shadow-logs"
    statements.mkdir()
    sqlite_db.init_db(live)
    live_before = live.read_bytes()

    result = run_ingest(
        path=target,
        statements_dir=statements,
        repo_root=tmp_path,
        log_dir=logs,
    )

    assert result["scanned"] == 0
    assert target.exists()
    assert logs.exists()
    assert live.read_bytes() == live_before


def test_shadow_sign_off_requires_acknowledgement_for_unmapped_portfolio_account(tmp_path):
    source = tmp_path / "source.sqlite"
    _seed_curated_source(source)
    source.with_name("config.json").write_text(
        json.dumps({"portfolios": [{"id": "invalid", "account_ids": [999]}]}),
        encoding="utf-8",
    )
    statements = tmp_path / "Statements"
    statements.mkdir()
    report = tmp_path / "ledger.vnext.report.json"

    result = build_shadow(
        source_db=source,
        target_db=tmp_path / "ledger.vnext.sqlite",
        statements_dir=statements,
        report_path=report,
        repo_root=tmp_path,
        rebuild_runner=_fake_rebuild,
    )

    assert result["curated_state"]["exported"]["unmapped_portfolio_account_ids"] == 1
    with pytest.raises(ValueError, match="unmapped curated state"):
        sign_off_report(report, reviewer="reviewer", confirmation="fixtures reviewed")
    signed = sign_off_report(
        report,
        reviewer="reviewer",
        confirmation="invalid preference reviewed",
        acknowledge_unmapped=True,
    )
    assert signed["manual_review"]["acknowledged_unmapped"] is True
