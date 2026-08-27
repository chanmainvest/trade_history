from __future__ import annotations

import json
from datetime import date

import duckdb

from ledger import holdings as holdings_service
from ledger.api.routes import config as config_route
from ledger.api.routes import monthly as monthly_route
from ledger.api.routes import transactions as transactions_route
from ledger.api.routes import viz as viz_route
from ledger.api.routes.monthly import _holdings_at
from ledger.api.routes.performance import _total_rows
from ledger.db import sqlite as sqlite_db
from ledger.ingest.pipeline import _record_source_file, _unchanged_source_file_id, _write_statement
from ledger.parsers.td import TDParser
from ledger.parsers.types import ParsedAccount, ParsedStatement
from ledger.pdf_text import PdfText

from .db_fixtures import _seed_evidence, seed_cash, seed_position, seed_source, seed_statement


def test_transactions_include_opening_positions_without_fake_source_links(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id, source_id = _seed_account(conn)
        statement_id = _seed_statement(conn, account_id, source_id, "2024-01-31")
        instrument_id = sqlite_db.upsert_instrument(
            conn, asset_type="equity", symbol="ABC", currency="CAD"
        )
        conn.execute(
            """
            INSERT INTO initial_positions(
                account_id, as_of_date, instrument_id, quantity, currency, notes
            ) VALUES (?, '2023-12-31', ?, 5, 'CAD', 'inferred: synthetic')
            """,
            (account_id, instrument_id),
        )
        transaction_id = conn.execute(
            """
            INSERT INTO transactions(
                account_id, statement_id, trade_date, txn_type,
                instrument_id, quantity, currency
            ) VALUES (?, ?, '2024-01-10', 'buy', ?, 2, 'CAD')
            RETURNING transaction_id
            """,
            (account_id, statement_id, instrument_id),
        ).fetchone()[0]
        evidence_id = _seed_evidence(
            conn, statement_id=statement_id, row_kind="transaction", row_index=0
        )
        conn.execute(
            "UPDATE transactions SET evidence_id = ? WHERE transaction_id = ?",
            (evidence_id, transaction_id),
        )
        source = conn.execute(
            "SELECT active_ingestion_run_id, sha256 FROM source_files WHERE source_file_id = ?",
            (source_id,),
        ).fetchone()
        page_id = conn.execute(
            """
            INSERT INTO source_pages(
                source_file_id, ingestion_run_id, extractor_version,
                page_number, width, height
            ) VALUES (?, ?, 'fixture', 1, 612, 792)
            RETURNING source_page_id
            """,
            (source_id, source["active_ingestion_run_id"]),
        ).fetchone()[0]
        line_id = conn.execute(
            """
            INSERT INTO source_lines(
                source_page_id, line_number, raw_text,
                normalized_text_hash, x0, top, x1, bottom
            ) VALUES (?, 1, 'synthetic transaction evidence', ?, 10, 20, 200, 30)
            RETURNING source_line_id
            """,
            (page_id, "a" * 64),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO source_evidence_geometry(
                evidence_id, extractor_version, source_sha256, status
            ) VALUES (?, 'fixture', ?, 'exact')
            """,
            (evidence_id, source["sha256"]),
        )
        conn.execute(
            "INSERT INTO source_evidence_lines(evidence_id, source_line_id, ordinal) "
            "VALUES (?, ?, 0)",
            (evidence_id, line_id),
        )
    monkeypatch.setattr(transactions_route.sqlite_db, "SQLITE_PATH", db_path)

    response = transactions_route.list_transactions(
        start=None,
        end=None,
        institution=None,
        account_id=None,
        symbol=None,
        txn_type=None,
        min_abs_amount=None,
        limit=100,
    )

    assert [row["row_kind"] for row in response["rows"]] == [
        "transaction",
        "initial_position",
    ]
    transaction, initial = response["rows"]
    assert transaction["statement_id"] == statement_id
    assert transaction["transaction_id"] is not None
    assert transaction["source_ref"] == {
        "statement_id": statement_id,
        "kind": "transaction",
        "id": transaction_id,
        "geometry_status": "exact",
        "page_numbers": [1],
        "linkable": True,
    }
    assert initial["txn_type"] == "initial_position"
    assert initial["statement_id"] is None
    assert initial["transaction_id"] is None
    assert initial["source_ref"] is None


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


def test_config_route_drops_legacy_display_currency(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"theme": "light", "display_currency": "USD", "hide_money": False}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_route, "_CONFIG_PATH", config_path)

    current = config_route.get_config()
    assert current["theme"] == "light"
    assert "display_currency" not in current

    saved = config_route.put_config({"display_currency": "CAD", "hide_money": True})
    assert saved["hide_money"] is True
    assert "display_currency" not in saved
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert "display_currency" not in persisted


def test_quarantine_rows_are_replaced_on_reingest(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    statement = ParsedStatement(
        account=ParsedAccount(account_number="A1", account_type="Margin", base_currency="CAD"),
        period_start="2024-01-01",
        period_end="2024-01-31",
        quarantine=[("mystery row", "unparsed")],
    )
    with sqlite_db.session(db_path) as conn:
        _seed_account(conn)
        pdf = PdfText(
            relpath="Statements/Test/sample.pdf",
            page_count=1,
            pages=["test"],
            sha256="a" * 64,
            size_bytes=3,
        )
        source_file_id = _record_source_file(
            conn,
            pdf,
            parser_name="td",
            parser_version=TDParser.VERSION,
            parse_status="partial",
        )
        _write_statement(conn, source_file_id=source_file_id, institution_code="TST", stmt=statement)
        _write_statement(conn, source_file_id=source_file_id, institution_code="TST", stmt=statement)
        count = conn.execute("SELECT COUNT(*) FROM quarantine_transactions").fetchone()[0]
    assert count == 1


def test_unchanged_source_file_is_skippable_only_after_successful_parse(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    pdf = PdfText(
        relpath="Statements/Test/sample.pdf",
        page_count=1,
        pages=["test"],
        sha256="a" * 64,
        size_bytes=3,
    )
    with sqlite_db.session(db_path) as conn:
        source_file_id = _record_source_file(
            conn,
            pdf,
            parser_name="td",
            parser_version=TDParser.VERSION,
            parse_status="ok",
        )
        assert _unchanged_source_file_id(
            conn, relpath=pdf.relpath, sha256="a" * 64
        ) == source_file_id
        _record_source_file(
            conn,
            pdf,
            parser_name="td",
            parser_version="future-version",
            parse_status="failed",
        )
        assert _unchanged_source_file_id(
            conn, relpath=pdf.relpath, sha256="a" * 64
        ) == source_file_id


def test_performance_total_clears_omitted_sold_out_positions_and_includes_cash(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id, jan_source_id = _seed_account(conn, source_relpath="Statements/Test/jan.pdf")
        feb_source_id = seed_source(conn, "Statements/Test/feb.pdf")
        jan_statement_id = _seed_statement(conn, account_id, jan_source_id, "2024-01-31")
        feb_statement_id = _seed_statement(conn, account_id, feb_source_id, "2024-02-29")
        abc_id = sqlite_db.upsert_instrument(conn, asset_type="equity", symbol="ABC", currency="CAD")
        xyz_id = sqlite_db.upsert_instrument(conn, asset_type="equity", symbol="XYZ", currency="CAD")
        seed_position(
            conn,
            statement_id=jan_statement_id,
            instrument_id=abc_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=feb_statement_id,
            instrument_id=xyz_id,
            quantity=5,
            market_value=50,
            currency="CAD",
        )
        seed_cash(
            conn,
            statement_id=jan_statement_id,
            currency="CAD",
            closing_balance=25,
        )
        seed_cash(
            conn,
            statement_id=feb_statement_id,
            currency="CAD",
            closing_balance=30,
        )

    abc_rows = _total_rows(symbol="ABC", include_cash=False, path=db_path)
    by_date = {row["as_of_date"]: row["market_value"] for row in abc_rows}
    assert by_date["2024-01-31"] == 100.0
    assert by_date["2024-02-29"] == 0.0

    total_rows = _total_rows(path=db_path)
    total_by_date = {row["as_of_date"]: row["market_value"] for row in total_rows if row["currency"] == "CAD"}
    assert total_by_date["2024-01-31"] == 125.0
    assert total_by_date["2024-02-29"] == 80.0


def test_monthly_holding_uses_one_snapshot_per_account_instrument_on_duplicate_date(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id, first_source_id = _seed_account(conn, source_relpath="Statements/Test/first.pdf")
        second_source_id = seed_source(conn, "Statements/Test/second.pdf")
        first_statement_id = _seed_statement(conn, account_id, first_source_id, "2024-01-31")
        second_statement_id = _seed_statement(conn, account_id, second_source_id, "2024-01-31")
        instrument_id = sqlite_db.upsert_instrument(conn, asset_type="equity", symbol="ABC", currency="CAD")
        seed_position(
            conn,
            statement_id=first_statement_id,
            instrument_id=instrument_id,
            quantity=100,
            market_value=1000,
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=second_statement_id,
            instrument_id=instrument_id,
            quantity=125,
            market_value=1250,
            currency="CAD",
        )

    rows = _holdings_at("2024-01-31", [], path=db_path)
    assert len(rows) == 1
    assert rows[0]["quantity"] == 125.0
    assert rows[0]["market_value"] == 1250.0


def test_incomplete_scope_never_clears_prior_complete_holdings(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id, first_source_id = _seed_account(
            conn,
            source_relpath="Statements/Test/complete.pdf",
        )
        second_source_id = seed_source(conn, "Statements/Test/partial.pdf")
        first_statement_id = _seed_statement(
            conn,
            account_id,
            first_source_id,
            "2024-01-31",
        )
        second_statement_id = _seed_statement(
            conn,
            account_id,
            second_source_id,
            "2024-02-29",
        )
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
            statement_id=first_statement_id,
            instrument_id=abc_id,
            quantity=10,
            market_value=100,
            currency="CAD",
            completeness="complete",
        )
        seed_position(
            conn,
            statement_id=second_statement_id,
            instrument_id=xyz_id,
            quantity=5,
            market_value=50,
            currency="CAD",
            completeness="partial",
        )
        seed_cash(
            conn,
            statement_id=second_statement_id,
            currency="CAD",
            closing_balance=10,
            completeness="complete",
        )

    rows = _holdings_at("2024-02-29", [], path=db_path)
    assert [
        (row["symbol"], row["quantity"])
        for row in rows
        if row["asset_type"] != "cash"
    ] == [("ABC", 10.0)]
    performance = _total_rows(path=db_path)
    february = {
        row["currency"]: row["market_value"]
        for row in performance
        if row["as_of_date"] == "2024-02-29"
    }
    assert february == {"CAD": 110.0}


def test_monthly_holdings_include_cash_rows_and_converted_totals(tmp_path, monkeypatch):
    db_path = tmp_path / "ledger.sqlite"
    duck_path = tmp_path / "market.duckdb"
    sqlite_db.init_db(db_path)
    con = duckdb.connect(str(duck_path))
    try:
        con.execute(
            """
            CREATE TABLE fx_rates(
                base VARCHAR,
                quote VARCHAR,
                rate_date DATE,
                rate DOUBLE,
                PRIMARY KEY(base, quote, rate_date)
            )
            """
        )
        con.execute("INSERT INTO fx_rates VALUES ('USD', 'CAD', '2024-01-31', 1.25)")
        con.execute("INSERT INTO fx_rates VALUES ('CAD', 'USD', '2024-01-31', 0.8)")
    finally:
        con.close()
    monkeypatch.setattr(monthly_route, "DUCKDB_PATH", duck_path)

    with sqlite_db.session(db_path) as conn:
        account_id, source_id = _seed_account(conn)
        statement_id = _seed_statement(conn, account_id, source_id, "2024-01-31")
        instrument_id = sqlite_db.upsert_instrument(conn, asset_type="equity", symbol="ABC", currency="CAD")
        seed_position(
            conn,
            statement_id=statement_id,
            instrument_id=instrument_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        seed_cash(
            conn,
            statement_id=statement_id,
            currency="CAD",
            closing_balance=25,
        )
        seed_cash(
            conn,
            statement_id=statement_id,
            currency="USD",
            closing_balance=10,
        )

    rows = _holdings_at("2024-01-31", [], path=db_path)
    cash_rows = {row["symbol"]: row for row in rows if row["asset_type"] == "cash"}
    assert cash_rows["CAD Cash"]["market_value"] == 25.0
    assert cash_rows["USD Cash"]["market_value"] == 10.0

    totals = monthly_route._snapshot_totals(rows, "2024-01-31")
    assert totals["native"] == {"CAD": 125.0, "USD": 10.0}
    assert totals["combined"]["CAD"] == 137.5
    assert totals["combined"]["USD"] == 110.0


def test_monthly_dates_lists_available_snapshot_dates(tmp_path, monkeypatch):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id, source_id = _seed_account(conn)
        january = _seed_statement(conn, account_id, source_id, "2024-01-31")
        february = _seed_statement(conn, account_id, source_id, "2024-02-29")
        instrument_id = sqlite_db.upsert_instrument(
            conn, asset_type="equity", symbol="ABC", currency="CAD"
        )
        seed_position(
            conn,
            statement_id=january,
            instrument_id=instrument_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=february,
            instrument_id=instrument_id,
            quantity=11,
            market_value=110,
            currency="CAD",
        )

    from ledger.holdings import holding_dates as real_holding_dates

    monkeypatch.setattr(
        monthly_route,
        "holding_dates",
        lambda account_ids: real_holding_dates(account_ids, path=db_path),
    )
    assert monthly_route.dates(account_id=None) == {
        "dates": ["2024-01-31", "2024-02-29"]
    }
    assert monthly_route.dates(account_id="999999") == {"dates": []}


def test_composite_holding_contract_is_shared_across_monthly_performance_and_viz(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "ledger.sqlite"
    market_path = tmp_path / "market.duckdb"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id, january_source_id = _seed_account(
            conn,
            source_relpath="Statements/Test/composite-jan.pdf",
        )
        february_source_id = seed_source(
            conn,
            "Statements/Test/composite-feb.pdf",
        )
        january = _seed_statement(conn, account_id, january_source_id, "2024-01-31")
        february = _seed_statement(conn, account_id, february_source_id, "2024-02-29")
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
            quantity=5,
            market_value=50,
            currency="CAD",
        )
        february_default_snapshot = seed_position(
            conn,
            statement_id=february,
            instrument_id=instrument_id,
            quantity=10,
            market_value=100,
            currency="CAD",
        )
        context = conn.execute(
            "SELECT account_id, period_end FROM statements WHERE statement_id = ?",
            (february,),
        ).fetchone()
        evidence_id = _seed_evidence(
            conn,
            statement_id=february,
            row_kind="position",
            row_index=instrument_id + 10_000,
        )
        secondary_set_id = sqlite_db.upsert_snapshot_set(
            conn,
            statement_id=february,
            account_id=context["account_id"],
            as_of_date=context["period_end"],
            currency="CAD",
            section_type="positions",
            scope_key="secondary",
            completeness="complete",
            evidence_id=evidence_id,
            reported_total=None,
            validation_status="valid",
        )
        secondary_snapshot = conn.execute(
            """
            INSERT INTO position_snapshots(
                statement_id, snapshot_set_id, evidence_id, account_id, as_of_date,
                instrument_id, quantity, avg_cost, book_value, market_price,
                market_value, unrealized_pnl, currency, raw_line
            ) VALUES (?, ?, ?, ?, ?, ?, 20, 11, 220, 13, 260, 40, 'CAD',
                      'synthetic secondary position evidence')
            RETURNING snapshot_id
            """,
            (
                february,
                secondary_set_id,
                evidence_id,
                account_id,
                context["period_end"],
                instrument_id,
            ),
        ).fetchone()[0]
        conn.execute(
            """
            UPDATE position_snapshots
               SET avg_cost = 8, book_value = 80, market_price = 10,
                   market_value = 100, unrealized_pnl = 20
             WHERE snapshot_id = ?
            """,
            (february_default_snapshot,),
        )

    market = duckdb.connect(str(market_path))
    try:
        market.execute(
            "CREATE TABLE daily_prices("
            "symbol VARCHAR, close DOUBLE, adj_close DOUBLE, trade_date DATE)"
        )
        market.execute(
            "INSERT INTO daily_prices VALUES ('ABC', 12, 12, '2024-02-29')"
        )
    finally:
        market.close()

    monkeypatch.setattr(holdings_service.sqlite_db, "SQLITE_PATH", db_path)
    monkeypatch.setattr(holdings_service, "DUCKDB_PATH", market_path)
    monkeypatch.setattr(monthly_route, "DUCKDB_PATH", market_path)
    monkeypatch.setattr(viz_route, "_resolve_as_of", lambda month_end: "2024-02-29")
    monkeypatch.setattr(viz_route, "_symbol_profiles", lambda symbols: {})
    monkeypatch.setattr(
        viz_route,
        "_symbol_performance",
        lambda symbols, as_of, period: {symbol: None for symbol in symbols},
    )
    monkeypatch.setattr(viz_route, "_price_data_through", lambda: "2024-02-29")

    snapshot = monthly_route.snapshot(month_end=date(2024, 2, 29), account_id=None)
    assert len(snapshot["rows"]) == 1
    composite = snapshot["rows"][0]
    assert composite["quantity"] == 30.0
    assert composite["holding_state"] == "incomplete"
    assert composite["is_reported"] is False
    assert composite["source_ref"] is None
    assert composite["scope_key"] is None
    assert composite["checkpoint_date"] is None
    assert composite["checkpoint_statement_id"] is None
    assert composite["checkpoint_snapshot_set_id"] is None
    assert composite["reconciliation_status"] is None
    assert composite["provenance"]["type"] == "multiple_checkpoints"
    assert composite["provenance"]["checkpoint"] is None
    assert len(composite["provenance"]["checkpoints"]) == 2
    assert {
        contributor["source_ref"]["id"]
        for contributor in composite["provenance"]["checkpoints"]
    } == {february_default_snapshot, secondary_snapshot}
    assert composite["avg_cost"] is None
    assert composite["book_value"] is None
    assert composite["unrealized_pnl"] is None
    assert composite["market_price"] == 12.0
    assert composite["market_value"] == 360.0
    assert composite["price_status"] == "market"
    assert snapshot["totals"]["native"] == {"CAD": 360.0}

    diff = monthly_route.diff(
        a=date(2024, 1, 31),
        b=date(2024, 2, 29),
        account_id=None,
    )
    assert diff["rows"] == [
        {
            "holding_key": composite["holding_key"],
            "account_id": account_id,
            "account_number": "A1",
            "institution_code": "TST",
            "instrument_key": composite["instrument_key"],
            "symbol": "ABC",
            "asset_type": "equity",
            "currency": "CAD",
            "option_expiry": None,
            "option_strike": None,
            "option_type": None,
            "qty_a": 5.0,
            "qty_b": 30.0,
            "qty_delta": 25.0,
            "mv_a": 50.0,
            "mv_b": 360.0,
        }
    ]

    performance = _total_rows(path=db_path)
    assert {
        row["currency"]: row["market_value"]
        for row in performance
        if row["as_of_date"] == "2024-02-29"
    } == {"CAD": 360.0}
    visualisation = viz_route.holdings_by_sector(
        month_end=date(2024, 2, 29),
        account_id=None,
        period="1m",
    )
    assert len(visualisation["rows"]) == 1
    assert visualisation["rows"][0]["currency"] == "CAD"
    assert visualisation["rows"][0]["market_value"] == 360.0
    assert viz_route._held_symbols_at("2024-02-29", [], path=db_path) == ["ABC"]
