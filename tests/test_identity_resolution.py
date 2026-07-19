from ledger.db import sqlite as sqlite_db
from ledger.ingest.identity_resolution import resolve_parse_result
from ledger.parsers.types import (
    ParsedAccount,
    ParsedInstrument,
    ParsedPosition,
    ParsedSnapshotSet,
    ParsedStatement,
    ParsedTxn,
    ParseResult,
)


def test_unresolved_name_tokens_are_never_persistable_tickers(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    unresolved = ParsedInstrument(
        asset_type="equity",
        symbol="TO",
        currency="CAD",
        name="TO GENERIC ACCOUNT",
        resolution_method="unresolved_printed_identity",
        resolution_confidence=0.0,
    )
    unresolved_position = ParsedInstrument(
        asset_type="equity",
        symbol="PRINTED_NAME_TOKEN",
        currency="CAD",
        name="PRINTED NAME TOKEN",
        resolution_method="unresolved_printed_identity",
        resolution_confidence=0.0,
    )
    statement = ParsedStatement(
        account=ParsedAccount("TEST-1", "Cash"),
        period_start="2024-01-01",
        period_end="2024-01-31",
        transactions=[
            ParsedTxn(
                trade_date="2024-01-05",
                settle_date=None,
                txn_type="transfer_out",
                instrument=unresolved,
                quantity=None,
                price=None,
                gross_amount=None,
                commission=None,
                other_fees=None,
                net_amount=-10.0,
                currency="CAD",
                description="TO GENERIC ACCOUNT",
                raw_line="Jan 5 Transfer TO GENERIC ACCOUNT -10.00",
            )
        ],
        positions=[
            ParsedPosition(
                instrument=unresolved_position,
                quantity=5.0,
                avg_cost=None,
                book_value=50.0,
                market_price=10.0,
                market_value=50.0,
                unrealized_pnl=None,
                currency="CAD",
                raw_line="PRINTED NAME TOKEN 5 10.00 50.00",
            )
        ],
        snapshot_sets=[
            ParsedSnapshotSet(
                "CAD", "positions", "complete", validation_status="valid"
            )
        ],
    )
    result = ParseResult("test", "1", statements=[statement])

    with sqlite_db.session(db_path) as conn:
        counts = resolve_parse_result(conn, institution_code="TST", result=result)

    assert counts == {
        "quarantined_unresolved_position": 1,
        "unresolved_printed_identity": 1,
    }
    assert statement.transactions[0].instrument is None
    assert statement.transactions[0].resolution_method == "unresolved_printed_identity"
    assert statement.positions == []
    assert statement.snapshot_sets[0].completeness == "unknown"
    assert statement.snapshot_sets[0].validation_status == "warning"
    assert statement.quarantine[0].reason == (
        "position identity unresolved; row not persisted"
    )


def test_exact_same_statement_symbol_uses_holding_asset_identity(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    holding = ParsedInstrument("equity", "QTIP", "CAD", name="Mackenzie US TIPS")
    transaction_instrument = ParsedInstrument(
        "etf", "QTIP", "CAD", name="Mackenzie US TIPS ETF"
    )
    statement = ParsedStatement(
        account=ParsedAccount("TEST-1", "Cash"),
        period_start="2025-03-01",
        period_end="2025-03-31",
        positions=[
            ParsedPosition(
                instrument=holding,
                quantity=3600,
                avg_cost=None,
                book_value=None,
                market_price=None,
                market_value=None,
                unrealized_pnl=None,
                currency="CAD",
            )
        ],
        transactions=[
            ParsedTxn(
                trade_date="2025-03-13",
                settle_date=None,
                txn_type="sell",
                instrument=transaction_instrument,
                quantity=-1200,
                price=84.37,
                gross_amount=None,
                commission=None,
                other_fees=None,
                net_amount=101237.12,
                currency="CAD",
                description="SOLD MACKENZIE US TIPS INDEX ETF QTIP",
                raw_line="SOLD MACKENZIE US TIPS INDEX ETF QTIP",
            )
        ],
    )
    result = ParseResult("test", "1", statements=[statement])

    with sqlite_db.session(db_path) as conn:
        counts = resolve_parse_result(conn, institution_code="RBC_DI", result=result)

    assert counts == {"printed_symbol": 1, "same_statement_symbol": 1}
    assert transaction_instrument.asset_type == "equity"
    assert transaction_instrument.resolution_method == "same_statement_symbol"


def test_option_contract_is_not_collapsed_to_underlying_listing(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    option = ParsedInstrument(
        asset_type="option",
        symbol="NTR",
        currency="CAD",
        option_root="NTR",
        option_expiry="2024-09-20",
        option_strike=75,
        option_type="PUT",
        option_multiplier=100,
    )
    statement = ParsedStatement(
        account=ParsedAccount("TEST-1", "Cash"),
        period_start="2024-05-01",
        period_end="2024-05-31",
        positions=[
            ParsedPosition(
                instrument=option,
                quantity=-30,
                avg_cost=None,
                book_value=None,
                market_price=1.91,
                market_value=-5730,
                unrealized_pnl=None,
                currency="CAD",
                raw_line="PUT .NTR 09/20/24 75 30- 1.910",
            )
        ],
    )
    result = ParseResult("test", "1", statements=[statement])

    with sqlite_db.session(db_path) as conn:
        counts = resolve_parse_result(conn, institution_code="RBC_DI", result=result)

    assert counts == {"printed_option_contract": 1}
    assert option.asset_type == "option"
    assert option.symbol == "NTR"
    assert option.option_expiry == "2024-09-20"
    assert option.option_strike == 75
    assert option.option_type == "PUT"
