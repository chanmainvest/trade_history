"""Self-contained tests for the TD parser."""
from ledger.parsers.td import TDParser
from ledger.parsers.validation import validate_parse_result

from .fixture_loader import load_fixture


def test_td_modern_dual_account_holdings_activity_and_cash():
    result = TDParser().parse(load_fixture("td/modern_monthly.txt"))
    assert result.errors == []
    assert sorted(statement.account.account_number for statement in result.statements) == [
        "AB12CD-CAD",
        "AB12CD-USD",
    ]
    for statement in result.statements:
        assert statement.period_start == "2025-10-01"
        assert statement.period_end == "2025-10-31"
        assert statement.positions
        assert statement.transactions
        assert statement.cash_balances

    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )
    option_positions = [
        position
        for position in usd.positions
        if position.instrument.asset_type == "option"
    ]
    assert option_positions
    assert option_positions[0].instrument.option_expiry == "2026-02-20"
    assert any(
        transaction.instrument
        and transaction.instrument.asset_type == "option"
        for transaction in usd.transactions
    )
    assert validate_parse_result(result).is_valid


def test_td_legacy_bundle_splits_every_month_and_currency():
    result = TDParser().parse(load_fixture("td/legacy_bundle.txt"))
    assert result.errors == []
    assert len(result.statements) == 4
    assert {
        (statement.period_start, statement.period_end)
        for statement in result.statements
    } == {
        ("2016-01-01", "2016-01-31"),
        ("2016-02-01", "2016-02-29"),
    }
    assert {statement.account.account_number for statement in result.statements} == {
        "ZX90YU-CAD",
        "ZX90YU-USD",
    }
    assert all(statement.positions for statement in result.statements)
    assert all(statement.cash_balances for statement in result.statements)
    assert validate_parse_result(result).is_valid


def test_td_full_header_bundle_is_detected_as_current_failure():
    result = TDParser().parse(
        load_fixture("td/full_header_bundle_known_broken.txt")
    )
    report = validate_parse_result(result)
    assert not report.is_valid
    assert any(
        issue.code == "transaction_date_outside_period"
        for issue in report.errors
    )


def test_td_summary_filename_emits_annual_statement():
    result = TDParser().parse(
        load_fixture("td/Statement_AB12CD_2023_summary.txt")
    )
    assert len(result.statements) == 1
    statement = result.statements[0]
    assert statement.statement_type == "annual"
    assert statement.period_start == "2023-01-01"
    assert statement.period_end == "2023-12-31"
