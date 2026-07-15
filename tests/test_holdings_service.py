"""Regression coverage for the canonical read-only holdings service."""
from __future__ import annotations

import duckdb

from ledger import holdings as holdings_service
from ledger.api.routes import monthly as monthly_route
from ledger.api.routes.performance import _total_rows
from ledger.api.routes.viz import _held_symbols_at
from ledger.db import sqlite as sqlite_db
from ledger.holdings import holdings_at

from .db_fixtures import seed_cash, seed_position, seed_source, seed_statement


def _account(conn, number: str = "A-1") -> int:
    institution_id = sqlite_db.upsert_institution(conn, "TST", "Test Broker")
    return sqlite_db.upsert_account(
        conn,
        institution_id=institution_id,
        account_number=number,
        account_type="Margin",
        base_currency="CAD",
    )


def _statement(conn, account_id: int, month: str) -> int:
    source_id = seed_source(conn, f"Statements/Test/{month}.pdf")
    return seed_statement(
        conn,
        account_id=account_id,
        source_file_id=source_id,
        period_start=f"{month}-01",
        period_end=f"{month}-28",
    )


def _transaction(
    conn,
    *,
    account_id: int,
    statement_id: int,
    trade_date: str,
    txn_type: str,
    instrument_id: int | None,
    quantity: float | None,
    position_delta: float | None,
    cash_delta: float | None,
    cash_effective_date: str,
    currency: str,
) -> None:
    conn.execute(
        """
        INSERT INTO transactions(
            account_id, statement_id, trade_date, txn_type, instrument_id,
            quantity, position_delta, net_amount, cash_delta,
            cash_effective_date, currency
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            statement_id,
            trade_date,
            txn_type,
            instrument_id,
            quantity,
            position_delta,
            cash_delta,
            cash_delta,
            cash_effective_date,
            currency,
        ),
    )


def _seed_reconciled_position_result(conn, *, account_id: int, instrument_id: int) -> None:
    snapshot_set_id = conn.execute(
        "SELECT snapshot_set_id FROM position_snapshots WHERE instrument_id = ?",
        (instrument_id,),
    ).fetchone()[0]
    conn.execute(
        """
        INSERT INTO reconciliation_results(
            reconciliation_key, kind, account_id, snapshot_set_id, instrument_id,
            currency, tolerance, status
        ) VALUES (?, 'position', ?, ?, ?, 'CAD', 0.00000001, 'reconciled')
        """,
        (f"recon:v1:position:{snapshot_set_id}:{instrument_id}", account_id, snapshot_set_id, instrument_id),
    )


def test_holdings_reprices_post_checkpoint_movement_without_recomputing_cost(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    market_path = tmp_path / "market.duckdb"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        jan = _statement(conn, account_id, "2024-01")
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=jan,
            instrument_id=instrument_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        conn.execute(
            "UPDATE position_snapshots SET market_price = 10, avg_cost = 8, book_value = 80"
        )
        seed_cash(conn, statement_id=jan, currency="CAD", opening_balance=50, closing_balance=50)
        _seed_reconciled_position_result(conn, account_id=account_id, instrument_id=instrument_id)
        _transaction(
            conn,
            account_id=account_id,
            statement_id=jan,
            trade_date="2024-02-05",
            txn_type="buy",
            instrument_id=instrument_id,
            quantity=2,
            position_delta=2,
            cash_delta=-20,
            cash_effective_date="2024-02-06",
            currency="CAD",
        )
    con = duckdb.connect(str(market_path))
    try:
        con.execute(
            "CREATE TABLE daily_prices(symbol VARCHAR, close DOUBLE, adj_close DOUBLE, trade_date DATE)"
        )
        con.execute("INSERT INTO daily_prices VALUES ('ABC', 12, 12, '2024-02-10')")
    finally:
        con.close()

    rows = holdings_at("2024-02-15", path=db_path, market_path=market_path)
    security = next(row for row in rows if row["asset_type"] == "equity")
    cash = next(row for row in rows if row["asset_type"] == "cash")

    assert security["quantity"] == 12.0
    assert security["market_price"] == 12.0
    assert security["market_value"] == 144.0
    assert security["price_date"] == "2024-02-10"
    assert security["price_status"] == "market"
    assert security["checkpoint_date"] == "2024-01-28"
    assert security["checkpoint_statement_id"] == jan
    assert security["is_reported"] is False
    assert security["is_reconstructed"] is True
    assert security["holding_state"] == "reconstructed"
    assert security["reconciliation_status"] == "reconciled"
    assert security["book_value"] is None
    assert security["unrealized_pnl"] is None
    assert cash["quantity"] == 30.0
    assert cash["holding_state"] == "reconstructed"


def test_incomplete_scope_keeps_prior_anchor_but_marks_the_holding(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        jan = _statement(conn, account_id, "2024-01")
        feb = _statement(conn, account_id, "2024-02")
        abc_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        xyz_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="XYZ",
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=jan,
            instrument_id=abc_id,
            quantity=10,
            market_value=100,
            currency="CAD",
            completeness="complete",
        )
        seed_position(
            conn,
            statement_id=feb,
            instrument_id=xyz_id,
            quantity=5,
            market_value=50,
            currency="CAD",
            completeness="partial",
        )

    rows = holdings_at("2024-02-28", path=db_path)

    assert [(row["symbol"], row["quantity"]) for row in rows] == [("ABC", 10.0)]
    assert rows[0]["holding_state"] == "incomplete"
    assert "incomplete_position_scope_after_checkpoint" in rows[0]["quality_warnings"]


def test_unscoped_movements_are_not_fanned_out_across_complete_scopes(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        january = _statement(conn, account_id, "2024-01")
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=january,
            instrument_id=instrument_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        seed_cash(
            conn,
            statement_id=january,
            currency="CAD",
            opening_balance=100,
            closing_balance=100,
        )
        evidence_id = conn.execute(
            "SELECT evidence_id FROM position_snapshots LIMIT 1"
        ).fetchone()[0]
        position_scope_id = sqlite_db.upsert_snapshot_set(
            conn,
            statement_id=january,
            account_id=account_id,
            as_of_date="2024-01-28",
            currency="CAD",
            section_type="positions",
            scope_key="secondary",
            completeness="complete",
            evidence_id=evidence_id,
            reported_total=None,
            validation_status="valid",
        )
        cash_scope_id = sqlite_db.upsert_snapshot_set(
            conn,
            statement_id=january,
            account_id=account_id,
            as_of_date="2024-01-28",
            currency="CAD",
            section_type="cash",
            scope_key="secondary",
            completeness="complete",
            evidence_id=evidence_id,
            reported_total=None,
            validation_status="valid",
        )
        conn.execute(
            """
            INSERT INTO position_snapshots(
                statement_id, snapshot_set_id, evidence_id, account_id, as_of_date,
                instrument_id, quantity, market_value, currency
            ) VALUES (?, ?, ?, ?, '2024-01-28', ?, 20, 200, 'CAD')
            """,
            (january, position_scope_id, evidence_id, account_id, instrument_id),
        )
        conn.execute(
            """
            INSERT INTO cash_balances(
                statement_id, snapshot_set_id, evidence_id, account_id, as_of_date,
                currency, opening_balance, closing_balance
            ) VALUES (?, ?, ?, ?, '2024-01-28', 'CAD', 200, 200)
            """,
            (january, cash_scope_id, evidence_id, account_id),
        )
        _transaction(
            conn,
            account_id=account_id,
            statement_id=january,
            trade_date="2024-02-05",
            txn_type="buy",
            instrument_id=instrument_id,
            quantity=2,
            position_delta=2,
            cash_delta=-10,
            cash_effective_date="2024-02-05",
            currency="CAD",
        )

    rows = holdings_at("2024-02-15", path=db_path)
    security = next(row for row in rows if row["asset_type"] == "equity")
    cash = next(row for row in rows if row["asset_type"] == "cash")

    assert security["quantity"] == 30.0
    assert cash["quantity"] == 300.0
    assert security["holding_state"] == "incomplete"
    assert cash["holding_state"] == "incomplete"
    assert "ambiguous_position_scope_transaction" in security["quality_warnings"]
    assert "ambiguous_cash_scope_transaction" in cash["quality_warnings"]


def test_option_does_not_use_its_underlying_quote_as_a_contract_price(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    market_path = tmp_path / "market.duckdb"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        january = _statement(conn, account_id, "2024-01")
        option_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="option",
            symbol="ABC",
            currency="CAD",
            option_root="ABC",
            option_expiry="2024-06-21",
            option_strike=100,
            option_type="CALL",
        )
        seed_position(
            conn,
            statement_id=january,
            instrument_id=option_id,
            quantity=1,
            market_value=5,
            currency="CAD",
        )
        conn.execute("UPDATE position_snapshots SET market_price = 5")
        _transaction(
            conn,
            account_id=account_id,
            statement_id=january,
            trade_date="2024-02-05",
            txn_type="option_buy_to_open",
            instrument_id=option_id,
            quantity=1,
            position_delta=1,
            cash_delta=-5,
            cash_effective_date="2024-02-05",
            currency="CAD",
        )
    con = duckdb.connect(str(market_path))
    try:
        con.execute(
            "CREATE TABLE daily_prices(symbol VARCHAR, close DOUBLE, adj_close DOUBLE, trade_date DATE)"
        )
        con.execute("INSERT INTO daily_prices VALUES ('ABC', 100, 100, '2024-02-10')")
    finally:
        con.close()

    option = holdings_at("2024-02-15", path=db_path, market_path=market_path)[0]

    assert option["quantity"] == 2.0
    assert option["market_price"] == 5.0
    assert option["market_value"] == 10.0
    assert option["price_status"] == "stale_checkpoint"
    assert "stale_checkpoint_price" in option["quality_warnings"]


def test_monthly_diff_preserves_cad_usd_identity_and_consumers_share_holdings(tmp_path, monkeypatch):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        jan = _statement(conn, account_id, "2024-01")
        feb = _statement(conn, account_id, "2024-02")
        cad_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        usd_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="USD",
        )
        seed_position(
            conn,
            statement_id=jan,
            instrument_id=cad_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=jan,
            instrument_id=usd_id,
            quantity=5,
            market_value=50,
            currency="USD",
        )
        seed_position(
            conn,
            statement_id=feb,
            instrument_id=cad_id,
            quantity=12,
            market_value=120,
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=feb,
            instrument_id=usd_id,
            quantity=4,
            market_value=40,
            currency="USD",
        )

    monkeypatch.setattr(holdings_service.sqlite_db, "SQLITE_PATH", db_path)
    monthly_diff = monthly_route.diff(a="2024-01-28", b="2024-02-28", account_id=None)
    february_holdings = holdings_at("2024-02-28", path=db_path)
    performance = _total_rows(path=db_path)
    performance_february = {
        row["currency"]: row["market_value"]
        for row in performance
        if row["as_of_date"] == "2024-02-28"
    }

    assert len(monthly_diff["rows"]) == 2
    assert {row["currency"] for row in monthly_diff["rows"]} == {"CAD", "USD"}
    assert len({row["holding_key"] for row in monthly_diff["rows"]}) == 2
    assert _held_symbols_at("2024-02-28", [], path=db_path) == ["ABC"]
    assert performance_february == {
        "CAD": sum(
            row["market_value"] or 0.0
            for row in february_holdings
            if row["currency"] == "CAD"
        ),
        "USD": sum(
            row["market_value"] or 0.0
            for row in february_holdings
            if row["currency"] == "USD"
        ),
    }
