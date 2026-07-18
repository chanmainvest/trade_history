"""Self-contained tests for the RBC parser."""
from ledger.parsers.rbc import RBCParser
from ledger.parsers.validation import validate_parse_result

from .fixture_loader import load_fixture


def test_rbc_dual_currency_blocks_form_one_statement_with_complete_scopes():
    result = RBCParser().parse(load_fixture("rbc/monthly_dual_currency.txt"))
    assert result.errors == []
    assert len(result.statements) == 1
    statement = result.statements[0]
    assert statement.account.account_number == "111-22222-3-4"
    assert statement.account.base_currency == "CAD"
    assert statement.period_start == "2026-01-01"
    assert statement.period_end == "2026-01-30"
    assert {
        (scope.currency, scope.section_type, scope.completeness)
        for scope in statement.snapshot_sets
    } == {
        ("CAD", "cash", "complete"),
        ("CAD", "positions", "complete"),
        ("USD", "cash", "complete"),
        ("USD", "positions", "complete"),
    }
    assert validate_parse_result(result).is_valid


def test_rbc_holdings_dividend_option_and_cash():
    result = RBCParser().parse(load_fixture("rbc/monthly_dual_currency.txt"))
    statement = result.statements[0]
    cad_positions = [row for row in statement.positions if row.currency == "CAD"]
    assert {row.instrument.asset_type for row in cad_positions} == {
        "equity",
        "mutual_fund",
    }
    dividend = next(
        row
        for row in statement.transactions
        if row.txn_type == "dividend" and row.currency == "CAD"
    )
    assert dividend.net_amount == 50.0
    assert next(cash for cash in statement.cash_balances if cash.currency == "CAD").closing_balance == 1055.0

    option_transactions = [
        row
        for row in statement.transactions
        if row.instrument and row.instrument.asset_type == "option"
    ]
    assert option_transactions
    option = option_transactions[0].instrument
    assert option.option_expiry == "2026-02-20"
    assert option.option_strike == 35.0
    assert option.option_type == "CALL"
    exercise = next(
        row for row in option_transactions if row.txn_type == "option_exercise"
    )
    assert exercise.instrument is not None
    assert exercise.instrument.symbol == "TRP"
    assert exercise.quantity == -20
    assert all(row.source_span for row in statement.transactions)


def test_rbc_annual_performance_report():
    result = RBCParser().parse(load_fixture("rbc/2022_annual_report.txt"))
    assert result.errors == []
    assert len(result.statements) == 1
    statement = result.statements[0]
    assert statement.statement_type == "annual"
    assert statement.period_start == "2022-01-01"
    assert statement.period_end == "2022-12-31"
    rows = {row.currency: row for row in statement.annual_performance}
    assert rows["CAD"].ending_market_value == 103000.0
    assert rows["CAD"].money_weighted_1y == -2.0
    assert rows["USD"].since_date == "2022-03-28"
    assert rows["USD"].ending_market_value == 15900.0
    assert rows["USD"].money_weighted_since == -20.0
