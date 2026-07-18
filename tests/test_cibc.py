"""Self-contained tests for the CIBC parser."""
from __future__ import annotations

from ledger.parsers.cibc import CIBCParser
from ledger.parsers.validation import validate_parse_result

from .fixture_loader import load_fixture


def test_cibc_dual_currency_activity_holdings_and_cash():
    pdf = load_fixture("cibc/monthly_dual_currency.txt")
    parser = CIBCParser()
    assert parser.can_handle("CIBC Invest Direct", pdf.pages[0])

    result = parser.parse(pdf)
    assert result.errors == []
    assert len(result.statements) == 1
    statement = result.statements[0]
    assert statement.account.account_number == "111-22222"
    assert statement.period_start == "2023-11-01"
    assert statement.period_end == "2023-11-30"
    assert {cash.currency for cash in statement.cash_balances} == {"CAD", "USD"}
    assert {row.txn_type for row in statement.transactions} >= {
        "buy",
        "sell",
        "dividend",
    }
    assert any(
        row.instrument and row.instrument.asset_type == "option"
        for row in statement.transactions
    )
    assert {row.instrument.asset_type for row in statement.positions} >= {
        "equity",
        "mutual_fund",
        "option",
    }
    assert {
        (scope.currency, scope.section_type, scope.completeness)
        for scope in statement.snapshot_sets
    } == {
        ("CAD", "cash", "complete"),
        ("CAD", "positions", "complete"),
        ("USD", "cash", "complete"),
        ("USD", "positions", "complete"),
    }
    assert all(row.source_span and row.source_span.page_number == 1 for row in statement.transactions)
    assert all(row.source_span and row.source_span.page_number == 1 for row in statement.positions)
    assert all(row.source_span and row.source_span.page_number == 1 for row in statement.cash_balances)
    transfers = [row for row in statement.transactions if row.txn_type.startswith("transfer_")]
    account_transfers = [row for row in transfers if row.instrument is None]
    assert [(row.txn_type, row.instrument) for row in account_transfers] == [
        ("transfer_out", None),
        ("transfer_in", None),
    ]
    assert all("REFERENCE" not in (row.description or "") for row in transfers)
    aaa_transfer = next(
        row for row in transfers
        if row.instrument and row.instrument.symbol == "AAA"
    )
    assert aaa_transfer.quantity == 25
    xyz_transfer = next(
        row for row in transfers
        if row.instrument and row.instrument.asset_type == "option"
    )
    assert xyz_transfer.txn_type == "transfer_out"
    assert xyz_transfer.quantity == -3
    assert any(
        item.reason == "unclaimed activity-like row" and "REFERENCE" in item.raw_line
        for item in statement.quarantine
    )
    gamma = next(
        row for row in statement.transactions
        if row.instrument and row.instrument.symbol == "GAM"
    )
    assert gamma.quantity == 12_895.048
    assert gamma.net_amount == -177_441.02
    rio_expiry = next(
        row for row in statement.transactions
        if row.txn_type == "option_expiration"
        and row.instrument
        and row.instrument.symbol == "RIO"
    )
    assert rio_expiry.quantity == 20
    abc_expiry = next(
        row for row in statement.transactions
        if row.txn_type == "option_expiration"
        and row.instrument
        and row.instrument.symbol == "ABC"
    )
    assert abc_expiry.quantity is None
    assert any(
        item.reason == "option event has no printed contract quantity"
        and "CALL .ABC" in item.raw_line
        for item in statement.quarantine
    )
    assert validate_parse_result(result).is_valid


def test_cibc_tfsa_and_option_position():
    result = CIBCParser().parse(load_fixture("cibc/tfsa_option.txt"))
    assert result.errors == []
    statement = result.statements[0]
    assert statement.account.account_number == "333-44444"
    assert statement.account.account_type == "TFSA"
    assert statement.period_end == "2022-08-31"
    option = statement.positions[0].instrument
    assert option.asset_type == "option"
    assert option.option_root == "BCE"
    assert option.option_expiry == "2022-09-16"
    assert option.option_strike == 65.0


def test_cibc_tax_documents_are_explicitly_skipped_not_invalid():
    pdf = load_fixture("cibc/tfsa_option.txt")
    pdf.relpath = "tests/fixtures/cibc/Tax-Document_123.pdf"

    result = CIBCParser().parse(pdf)

    assert result.status == "skipped"
    assert result.skip_reason == "tax document; no brokerage statement extraction"
    assert result.errors == []
    assert result.statements == []
