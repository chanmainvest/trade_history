"""Tests for GET /export/yahoo-csv (Yahoo Finance portfolio lots export)."""
from __future__ import annotations

from datetime import date

from ledger import holdings as holdings_service
from ledger.api.routes import export as export_route
from ledger.db import sqlite as sqlite_db

from .db_fixtures import seed_cash, seed_position, seed_source, seed_statement


def _seed_account(conn, *, source_relpath: str = "Statements/Test/sample.pdf"):
    institution_id = sqlite_db.upsert_institution(conn, "TST", "Test Broker")
    account_id = sqlite_db.upsert_account(
        conn,
        institution_id=institution_id,
        account_number="A1",
        account_type="Margin",
        base_currency="CAD",
    )
    source_file_id = seed_source(conn, source_relpath)
    return account_id, source_file_id


def _seed_statement(conn, account_id: int, source_file_id: int, period_end: str) -> int:
    return seed_statement(
        conn,
        account_id=account_id,
        source_file_id=source_file_id,
        period_end=period_end,
    )


def test_yahoo_csv_exports_mapped_holdings_and_defaults_to_latest(tmp_path, monkeypatch):
    db_path = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(sqlite_db, "SQLITE_PATH", db_path)
    monkeypatch.setattr(holdings_service, "DUCKDB_PATH", tmp_path / "absent.duckdb")
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id, source_id = _seed_account(conn)
        statement_id = _seed_statement(conn, account_id, source_id, "2024-01-31")
        mapped_id = sqlite_db.upsert_instrument(
            conn, asset_type="etf", symbol="ABC", currency="CAD", market_symbol="ABC.TO"
        )
        unmapped_id = sqlite_db.upsert_instrument(
            conn, asset_type="equity", symbol="XYZ", currency="CAD"
        )
        no_cost_id = sqlite_db.upsert_instrument(
            conn, asset_type="equity", symbol="NOCOST", currency="CAD", market_symbol="NOCOST.TO"
        )
        seed_position(
            conn, statement_id=statement_id, instrument_id=mapped_id,
            quantity=10, market_value=250, currency="CAD",
        )
        conn.execute(
            "UPDATE position_snapshots SET avg_cost = 21.5 WHERE instrument_id = ?",
            (mapped_id,),
        )
        seed_position(
            conn, statement_id=statement_id, instrument_id=unmapped_id,
            quantity=5, market_value=50, currency="CAD",
        )
        seed_position(
            conn, statement_id=statement_id, instrument_id=no_cost_id,
            quantity=1, market_value=10, currency="CAD",
        )
        seed_cash(conn, statement_id=statement_id, currency="CAD", closing_balance=25)

    response = export_route.yahoo_csv()
    lines = response.body.decode("utf-8").strip().splitlines()
    assert lines[0] == "Symbol,Trade Date,Purchase Price,Quantity"
    assert "ABC.TO,,21.5,10" in lines
    assert "NOCOST.TO,,,1" in lines
    assert "XYZ" not in response.body.decode("utf-8")
    assert response.headers["x-yahoo-export-skipped"] == "1:XYZ"
    assert response.media_type == "text/csv; charset=utf-8"
    assert 'filename="yahoo_portfolio_20240131.csv"' in response.headers["content-disposition"]


def test_yahoo_csv_zeroes_short_and_skips_incomplete_rows_and_honours_account_filter(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "ledger.sqlite"
    monkeypatch.setattr(sqlite_db, "SQLITE_PATH", db_path)
    monkeypatch.setattr(holdings_service, "DUCKDB_PATH", tmp_path / "absent.duckdb")
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        institution_id = sqlite_db.upsert_institution(conn, "TST", "Test Broker")
        account_a = sqlite_db.upsert_account(
            conn,
            institution_id=institution_id,
            account_number="A1",
            account_type="Margin",
            base_currency="CAD",
        )
        account_b = sqlite_db.upsert_account(
            conn,
            institution_id=institution_id,
            account_number="A2",
            account_type="Margin",
            base_currency="CAD",
        )
        source_id = seed_source(conn, "Statements/Test/sample.pdf")
        statement_a = _seed_statement(conn, account_a, source_id, "2024-01-31")
        statement_b = _seed_statement(conn, account_b, source_id, "2024-01-31")
        # A later partial positions scope marks the PART holding incomplete.
        statement_b2 = _seed_statement(conn, account_b, source_id, "2024-02-29")
        held_id = sqlite_db.upsert_instrument(
            conn, asset_type="etf", symbol="HELD", currency="CAD", market_symbol="HELD.TO"
        )
        short_id = sqlite_db.upsert_instrument(
            conn, asset_type="equity", symbol="SHRT", currency="CAD", market_symbol="SHRT.TO"
        )
        partial_id = sqlite_db.upsert_instrument(
            conn, asset_type="equity", symbol="PART", currency="CAD", market_symbol="PART.TO"
        )
        seed_position(
            conn, statement_id=statement_a, instrument_id=held_id,
            quantity=10, market_value=100, currency="CAD",
        )
        seed_position(
            conn, statement_id=statement_b, instrument_id=short_id,
            quantity=-3, market_value=-30, currency="CAD",
        )
        seed_position(
            conn, statement_id=statement_b, instrument_id=partial_id,
            quantity=7, market_value=70, currency="CAD",
        )
        seed_position(
            conn, statement_id=statement_b2, instrument_id=partial_id,
            quantity=7, market_value=70, currency="CAD", completeness="partial",
        )

    response = export_route.yahoo_csv(
        month_end=date(2024, 1, 31), account_id=str(account_a)
    )
    lines = response.body.decode("utf-8").strip().splitlines()
    assert lines == ["Symbol,Trade Date,Purchase Price,Quantity", "HELD.TO,,,10"]
    # Account B rows are filtered out entirely by the account_id parameter.
    assert "SHRT" not in response.body.decode("utf-8")
    assert "PART" not in response.body.decode("utf-8")

    response_all = export_route.yahoo_csv(month_end=date(2024, 2, 29))
    lines_all = response_all.body.decode("utf-8").strip().splitlines()
    assert lines_all == [
        "Symbol,Trade Date,Purchase Price,Quantity",
        "HELD.TO,,,10",
        "SHRT.TO,,,0",
    ]
    skipped = response_all.headers["x-yahoo-export-skipped"]
    assert skipped == "1:PART"
