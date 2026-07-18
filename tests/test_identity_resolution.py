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
