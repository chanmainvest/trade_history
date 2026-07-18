"""Self-contained tests for the TD parser."""
from ledger.db import sqlite as sqlite_db
from ledger.ingest.identity_resolution import resolve_parse_result
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

    cad = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "CAD"
    )
    assert next(row for row in cad.transactions if row.txn_type == "buy").net_amount == -200.0

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
    adjusted_expiry = next(
        transaction
        for transaction in usd.transactions
        if transaction.txn_type == "option_expiration"
    )
    assert adjusted_expiry.instrument is not None
    assert adjusted_expiry.instrument.symbol == "BABA"
    assert adjusted_expiry.instrument.option_expiry == "2025-01-17"
    assert adjusted_expiry.quantity == -10
    assert validate_parse_result(result).is_valid


def test_td_name_only_buy_resolves_to_exact_same_statement_holding(tmp_path):
    result = TDParser().parse(load_fixture("td/modern_monthly.txt"))
    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )
    velo_buy = next(
        transaction
        for transaction in usd.transactions
        if "VELO3D" in (transaction.description or "")
    )
    assert velo_buy.quantity == 2_000
    assert velo_buy.price == 25
    assert velo_buy.net_amount == -50_009.99
    assert velo_buy.instrument is not None
    assert velo_buy.instrument.name == "VELO3D INC-NEW"
    assert velo_buy.instrument.resolution_method == "unresolved_printed_identity"

    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        counts = resolve_parse_result(conn, institution_code="TD_WB", result=result)

    assert counts["same_statement_holding"] == 1
    assert velo_buy.instrument is not None
    assert velo_buy.instrument.symbol == "VELO"
    assert velo_buy.resolution_method == "same_statement_holding"


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


def test_td_full_header_bundle_splits_every_month_with_complete_scopes():
    result = TDParser().parse(
        load_fixture("td/full_header_bundle_known_broken.txt")
    )
    assert result.errors == []
    assert {
        (statement.period_start, statement.period_end)
        for statement in result.statements
    } == {
        ("2020-01-01", "2020-01-31"),
        ("2020-02-01", "2020-02-29"),
    }
    assert all(
        {
            (scope.currency, scope.section_type, scope.completeness)
            for scope in statement.snapshot_sets
        } == {
            ("CAD", "cash", "complete"),
            ("CAD", "positions", "complete"),
        }
        for statement in result.statements
    )
    assert all(
        transaction.source_span and transaction.source_span.page_number == 1
        for statement in result.statements
        for transaction in statement.transactions
    )
    assert validate_parse_result(result).is_valid


def test_td_repeated_account_fragments_merge_into_one_scope_per_currency():
    result = TDParser().parse(load_fixture("td/repeated_account_fragment.txt"))
    assert result.errors == []
    assert len(result.statements) == 2
    cad = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "CAD"
    )
    assert len(cad.positions) == 1
    assert len(cad.transactions) == 2
    assert len(cad.cash_balances) == 1
    assert cad.cash_balances[0].opening_balance == 100.0
    assert cad.cash_balances[0].closing_balance == 115.0
    assert cad.transactions[-1].source_span and cad.transactions[-1].source_span.page_number == 3
    assert {
        (scope.currency, scope.section_type, scope.completeness)
        for scope in cad.snapshot_sets
    } == {
        ("CAD", "cash", "complete"),
        ("CAD", "positions", "complete"),
    }
    assert validate_parse_result(result).is_valid


def test_td_option_holding_skips_harmless_intervening_header_lines():
    pdf = load_fixture("td/modern_monthly.txt")
    pdf.pages = [
        page.replace(
            "\n20FE@35",
            "\nPage 1 of 2\nDescription Quantity\n20FE@35",
        )
        for page in pdf.pages
    ]
    result = TDParser().parse(pdf)
    usd = next(
        statement
        for statement in result.statements
        if statement.account.base_currency == "USD"
    )
    option = next(
        position.instrument
        for position in usd.positions
        if position.instrument.asset_type == "option"
    )
    assert option.option_expiry == "2026-02-20"
    assert validate_parse_result(result).is_valid


def test_td_summary_filename_emits_annual_statement():
    result = TDParser().parse(
        load_fixture("td/Statement_AB12CD_2023_summary.txt")
    )
    assert len(result.statements) == 1
    statement = result.statements[0]
    assert statement.statement_type == "annual"
    assert statement.period_start == "2023-01-01"
    assert statement.period_end == "2023-12-31"
