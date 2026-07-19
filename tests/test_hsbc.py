"""Self-contained tests for the HSBC parser."""
from ledger.db import sqlite as sqlite_db
from ledger.ingest.identity_resolution import resolve_parse_result
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
    assert {
        (scope.section_type, scope.completeness)
        for scope in cad.snapshot_sets
    } == {
        ("cash", "complete"),
        ("positions", "complete"),
    }
    assert buy.source_span and buy.source_span.page_number == 1
    assert all(row.instrument.symbol not in {"CAD", "USD"} for row in cad.positions)
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
    assert statement.positions[0].source_span and statement.positions[0].source_span.page_number == 1
    assert statement.transactions[0].source_span and statement.transactions[0].source_span.page_number == 2
    assert statement.cash_balances[0].source_span and statement.cash_balances[0].source_span.page_number == 2


def test_hsbc_name_only_holding_resolves_and_parenthesized_cash_stays_negative(
    tmp_path,
):
    result = HSBCParser().parse(
        load_fixture("hsbc/name_only_holdings_negative_cash.txt")
    )
    statement = result.statements[0]

    assert statement.cash_balances[0].opening_balance == 8949.29
    assert statement.cash_balances[0].closing_balance == -96022.38
    assert len(statement.positions) == 1
    assert statement.positions[0].instrument.resolution_method == (
        "unresolved_printed_identity"
    )

    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        resolve_parse_result(conn, institution_code="HSBC_IDI", result=result)

    assert statement.positions[0].instrument.symbol == "CASH"
    assert statement.positions[0].instrument.asset_type == "etf"
    assert statement.snapshot_sets[0].completeness == "complete"


def test_hsbc_fx_conversion_and_refund_complete_the_cash_equation():
    result = HSBCParser().parse(load_fixture("hsbc/cash_fx_refund.txt"))
    statement = result.statements[0]
    transactions = {row.txn_type: row for row in statement.transactions}

    assert transactions["deposit"].net_amount == 270000.0
    assert transactions["fx_conversion"].net_amount == -270000.0
    assert transactions["adjustment"].net_amount == 10.0
    assert sum(row.net_amount or 0.0 for row in statement.transactions) == 10.0
    assert statement.cash_balances[0].opening_balance == 100.0
    assert statement.cash_balances[0].closing_balance == 110.0
