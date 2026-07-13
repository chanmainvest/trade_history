from __future__ import annotations

from copy import deepcopy
from typing import cast

from ledger.parsers.types import (
    ParsedAccount,
    ParsedCashBalance,
    ParsedInstrument,
    ParsedPosition,
    ParsedStatement,
    ParsedTxn,
    ParseResult,
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
        "snapshot_completeness_unavailable",
    }


def test_duplicate_statement_key_is_fatal_before_persistence():
    result = _result()
    result.statements.append(deepcopy(result.statements[0]))
    report = validate_parse_result(result)
    assert not report.is_valid
    assert any(issue.code == "duplicate_statement_key" for issue in report.errors)


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


def test_parser_reported_errors_are_fatal():
    result = _result()
    result.errors.append("section could not be parsed")
    report = validate_parse_result(result)
    assert not report.is_valid
    assert any(issue.code == "parser_reported_error" for issue in report.errors)
