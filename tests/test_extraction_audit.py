from __future__ import annotations

import json

from click.testing import CliRunner

from ledger.cli import main
from ledger.ingest.audit import audit_extraction

from .fixture_loader import FIXTURES


def test_fixture_corpus_audit_is_read_only_deterministic_and_passes_phase_four_parser_gates(
    tmp_path,
):
    output = tmp_path / "audit.jsonl"
    summary = audit_extraction(statements_dir=FIXTURES, output=output)
    first_bytes = output.read_bytes()

    assert summary["files"] == 14
    assert summary["duplicate_statement_keys"] == 0
    assert summary["invalid_files"] == 0
    assert summary["validation_errors"] == 0
    assert summary["counts"]["statements"] == 18
    assert summary["cash_checks"] > 0
    assert summary["cash_unbalanced"] == 0
    assert summary["position_unbalanced"] == 0
    # The annual RBC fixture and two parser-only HSBC edge fixtures are in
    # generic fixture folders without production broker recognition headers.
    assert summary["unclaimed_files"] == 3

    repeated = audit_extraction(statements_dir=FIXTURES, output=output)
    assert repeated == summary
    assert output.read_bytes() == first_bytes

    records = [
        json.loads(line)
        for line in output.read_text(encoding="utf-8").splitlines()
    ]
    assert records[-1]["record_type"] == "summary"
    assert not any("raw_line" in record for record in records)


def test_audit_cli_supports_error_gate(tmp_path):
    output = tmp_path / "audit.jsonl"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "audit",
            "extraction",
            "--statements-dir",
            str(FIXTURES),
            "--output",
            str(output),
            "--fail-on-errors",
        ],
    )
    assert result.exit_code != 0
    assert "extraction audit found fatal issues" in result.output
    assert output.exists()
