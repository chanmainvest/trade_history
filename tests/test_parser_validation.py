from __future__ import annotations

from copy import deepcopy
from typing import cast

from ledger.parsers.layout import quarantine_unsupported_rows
from ledger.parsers.types import (
    ParsedAccount,
    ParsedCashBalance,
    ParsedInstrument,
    ParsedPosition,
    ParsedScopeIssue,
    ParsedSnapshotSet,
    ParsedStatement,
    ParsedTxn,
    ParseResult,
    SourceSpan,
    TxnType,
)
from ledger.parsers.validation import validate_parse_result


def _result() -> ParseResult:
    instrument = ParsedInstrument(
        asset_type="equity",
        symbol="AAA",
        currency="CAD",
        name="Synthetic Alpha",
    )
    statement = ParsedStatement(
        account=ParsedAccount("SYNTH-1", "Margin", "CAD"),
        period_start="2024-01-01",
        period_end="2024-01-31",
        transactions=[
            ParsedTxn(
                trade_date="2024-01-10",
                settle_date=None,
                txn_type="buy",
                instrument=instrument,
                quantity=10,
                price=20,
                gross_amount=200,
                commission=0,
                other_fees=0,
                net_amount=-200,
                currency="CAD",
                description="Synthetic buy",
                raw_line="Jan 10 Buy Synthetic Alpha 10 20.00 -200.00",
            )
        ],
        positions=[
            ParsedPosition(
                instrument=instrument,
                quantity=10,
                avg_cost=20,
                book_value=200,
                market_price=21,
                market_value=210,
                unrealized_pnl=10,
                currency="CAD",
                raw_line="Synthetic Alpha AAA 10 20.00 210.00",
            )
        ],
        cash_balances=[
            ParsedCashBalance(
                currency="CAD",
                opening_balance=1000,
                closing_balance=800,
            )
        ],
    )
    return ParseResult("synthetic", "1.0.0", [statement])


def test_valid_result_has_only_current_contract_warnings():
    report = validate_parse_result(_result())
    assert report.is_valid
    assert {issue.code for issue in report.warnings} == {
        "cash_source_evidence_unavailable",
        "snapshot_scope_undeclared",
    }


def test_duplicate_statement_key_is_fatal_before_persistence():
    result = _result()
    result.statements.append(deepcopy(result.statements[0]))
    report = validate_parse_result(result)
    assert not report.is_valid
    assert any(issue.code == "duplicate_statement_key" for issue in report.errors)


def test_declared_complete_scopes_remove_legacy_scope_warnings():
    result = _result()
    statement = result.statements[0]
    statement.cash_balances[0].raw_line = "Opening 1000.00\nClosing 800.00"
    statement.snapshot_sets = [
        ParsedSnapshotSet("CAD", "positions", "complete"),
        ParsedSnapshotSet("CAD", "cash", "complete"),
    ]

    report = validate_parse_result(result)

    assert report.is_valid
    assert report.warnings == []


def test_out_of_period_date_and_noncanonical_type_are_fatal():
    result = _result()
    transaction = result.statements[0].transactions[0]
    transaction.trade_date = "2024-02-01"
    transaction.txn_type = cast(TxnType, "split")
    report = validate_parse_result(result)
    assert {issue.code for issue in report.errors} >= {
        "transaction_date_outside_period",
        "invalid_transaction_type",
    }


def test_explicit_staged_unresolved_movement_can_reach_name_reconciliation():
    result = _result()
    transaction = result.statements[0].transactions[0]
    transaction.instrument = None
    transaction.resolution_method = "unresolved_printed_identity"
    transaction.resolution_confidence = 0.0

    report = validate_parse_result(result)

    assert "position_movement_without_instrument" not in {
        issue.code for issue in report.errors
    }
    assert report.is_valid


def test_option_identity_and_currency_mismatch_are_fatal():
    result = _result()
    transaction = result.statements[0].transactions[0]
    transaction.instrument = ParsedInstrument(
        asset_type="option",
        symbol="AAA",
        currency="USD",
        option_root="AAA",
        option_expiry=None,
        option_strike=None,
        option_type=None,
    )
    report = validate_parse_result(result)
    assert {issue.code for issue in report.errors} >= {
        "instrument_currency_mismatch",
        "incomplete_option_identity",
        "invalid_option_type",
        "missing_numeric",
    }


def test_unsupported_pending_and_incomplete_option_rows_are_quarantined():
    result = _result()
    statement = result.statements[0]
    statement.snapshot_sets = [
        ParsedSnapshotSet("CAD", "positions", "complete"),
        ParsedSnapshotSet("CAD", "cash", "complete"),
    ]
    statement.transactions[0].trade_date = "2024-02-01"
    statement.positions[0].instrument = ParsedInstrument(
        asset_type="option",
        symbol="AAA",
        currency="CAD",
        option_root="AAA",
        option_expiry=None,
        option_strike=None,
        option_type="CALL",
    )

    quarantine_unsupported_rows(result)

    assert statement.transactions == []
    assert statement.positions == []
    assert {
        item.reason
        for item in statement.quarantine
    } == {
        "transaction date is outside the statement period; pending-row model unavailable",
        "option identity is incomplete: option_expiry, option_strike",
    }
    assert statement.snapshot_sets[0].completeness == "unknown"
    assert validate_parse_result(result).is_valid


def test_parser_reported_errors_are_fatal():
    result = _result()
    result.errors.append("section could not be parsed")
    report = validate_parse_result(result)
    assert not report.is_valid
    assert any(issue.code == "parser_reported_error" for issue in report.errors)


def test_physical_page_membership_is_required_when_source_page_count_is_known():
    result = _result()

    report = validate_parse_result(result, page_count=2)

    assert {issue.code for issue in report.errors} == {"missing_statement_pages"}


def test_source_evidence_must_belong_to_the_statement_pages():
    result = _result()
    statement = result.statements[0]
    statement.page_numbers = (1,)
    statement.page_assignment_method = "parser_explicit"
    statement.transactions[0].source_span = SourceSpan(
        raw_text=statement.transactions[0].raw_line or "",
        page_number=2,
    )

    report = validate_parse_result(result, page_count=2)

    assert "source_span_outside_statement_pages" in {
        issue.code for issue in report.errors
    }


def test_incomplete_scope_requires_structured_blocking_issue():
    result = _result()
    statement = result.statements[0]
    statement.snapshot_sets = [
        ParsedSnapshotSet("CAD", "positions", "unknown"),
        ParsedSnapshotSet("CAD", "cash", "complete"),
    ]

    report = validate_parse_result(result)
    assert "incomplete_scope_without_issue" in {
        issue.code for issue in report.errors
    }

    statement.snapshot_sets[0].issues.append(ParsedScopeIssue(
        issue_code="section_not_fully_recognized",
        severity="error",
        detail={"reason": "synthetic fixture"},
        blocks_completeness=True,
    ))
    assert validate_parse_result(result).is_valid
