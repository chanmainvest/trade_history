"""Self-contained tests for the HSBC parser."""
from ledger.parsers.hsbc import HSBCParser
from ledger.parsers.validation import validate_parse_result

from .fixture_loader import load_fixture


def test_hsbc_two_account_split_holdings_activity_and_cash():
    result = HSBCParser().parse(load_fixture("hsbc/monthly_two_accounts.txt"))
    assert result.errors == []
    assert len(result.statements) == 2
    assert sorted(statement.account.base_currency for statement in result.statements) == [
        "CAD",
        "USD",
    ]
    for statement in result.statements:
        assert statement.period_start == "2023-09-01"
        assert statement.period_end == "2023-09-30"
        assert statement.cash_balances
        assert statement.positions
        assert statement.transactions

    cad = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "CAD"
    )
    assert any(row.instrument.asset_type == "option" for row in cad.positions)
    buy = next(row for row in cad.transactions if row.txn_type == "buy")
    assert buy.net_amount == -200.0
    assert validate_parse_result(result).is_valid


def test_hsbc_continued_account_sections_are_merged():
    result = HSBCParser().parse(load_fixture("hsbc/continued_account.txt"))
    assert result.errors == []
    assert len(result.statements) == 1
    statement = result.statements[0]
    assert statement.account.account_number == "2B-3CDE-E"
    assert statement.positions
    assert statement.transactions
    assert statement.cash_balances[0].closing_balance == 200.0
