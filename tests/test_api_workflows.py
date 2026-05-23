from __future__ import annotations

import json
from contextlib import contextmanager

import duckdb
from fastapi.testclient import TestClient

from ledger.api.app import app
from ledger.api.routes import config as config_route
from ledger.api.routes import monthly as monthly_route
from ledger.api.routes import statements as statements_route
from ledger.api.routes.monthly import _holdings_at
from ledger.api.routes.performance import _total_rows
from ledger.db import sqlite as sqlite_db
from ledger.ingest.pipeline import _record_source_file, _unchanged_source_file_id, _write_statement
from ledger.parsers.types import ParsedAccount, ParsedStatement
from ledger.pdf_text import PdfText


def _seed_account(conn, *, source_relpath: str = "Statements/Test/sample.pdf"):
    institution_id = sqlite_db.upsert_institution(conn, "TST", "Test Broker")
    account_id = sqlite_db.upsert_account(
        conn,
        institution_id=institution_id,
        account_number="A1",
        account_type="Margin",
        base_currency="CAD",
    )
    source_file_id = conn.execute(
        "INSERT INTO source_files(relpath, parse_status) VALUES (?, 'ok') RETURNING source_file_id",
        (source_relpath,),
    ).fetchone()[0]
    return account_id, source_file_id


def _seed_statement(conn, account_id: int, source_file_id: int, period_end: str) -> int:
    return conn.execute(
        """
        INSERT INTO statements(source_file_id, account_id, period_start, period_end)
        VALUES (?, ?, ?, ?)
        RETURNING statement_id
        """,
        (source_file_id, account_id, period_end[:8] + "01", period_end),
    ).fetchone()[0]


def test_upload_sanitizes_filename_and_rejects_non_pdf_bytes(tmp_path, monkeypatch):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    original_session = sqlite_db.session

    @contextmanager
    def session(path=None):
        with original_session(path or db_path) as conn:
            yield conn

    statements_dir = tmp_path / "Statements"
    monkeypatch.setattr(statements_route, "STATEMENTS_DIR", statements_dir)
    monkeypatch.setattr(statements_route, "PARSER_DRAFT_DIR", tmp_path / "parser_drafts")
    monkeypatch.setattr(statements_route.sqlite_db, "session", session)

    client = TestClient(app)
    bad = client.post(
        "/statements/upload",
        files={"file": ("statement.pdf", b"not a pdf", "application/pdf")},
    )
    assert bad.status_code == 400

    ok = client.post(
        "/statements/upload",
        files={"file": ("../..\\escape.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
    )
    assert ok.status_code == 200
    uploads_dir = statements_dir / "uploads"
    saved = list(uploads_dir.iterdir())
    assert len(saved) == 1
    assert saved[0].is_relative_to(uploads_dir)
    assert saved[0].name.endswith("_escape.pdf")
    assert ok.json()["review"]["parse_status"] == "image_only"

    draft = client.post(
        "/statements/draft-parser",
        json={"sha256": ok.json()["sha256"], "institution_folder": "uploads"},
    )
    assert draft.status_code == 200
    assert draft.json()["status"] == "prompt_created"
    assert (tmp_path / "parser_drafts" / ok.json()["sha256"][:12] / "prompt.md").exists()


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
            sha256="abc",
            size_bytes=3,
        )
        source_file_id = _record_source_file(
            conn,
            pdf,
            parser_name="TestParser",
            parser_version="1",
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
        sha256="abc",
        size_bytes=3,
    )
    with sqlite_db.session(db_path) as conn:
        source_file_id = _record_source_file(
            conn,
            pdf,
            parser_name="TestParser",
            parser_version="1",
            parse_status="ok",
        )
        assert _unchanged_source_file_id(conn, relpath=pdf.relpath, sha256="abc") == source_file_id
        _record_source_file(
            conn,
            pdf,
            parser_name="TestParser",
            parser_version="1",
            parse_status="failed",
        )
        assert _unchanged_source_file_id(conn, relpath=pdf.relpath, sha256="abc") is None


def test_performance_total_clears_omitted_sold_out_positions_and_includes_cash(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id, jan_source_id = _seed_account(conn, source_relpath="Statements/Test/jan.pdf")
        feb_source_id = conn.execute(
            "INSERT INTO source_files(relpath, parse_status) VALUES (?, 'ok') RETURNING source_file_id",
            ("Statements/Test/feb.pdf",),
        ).fetchone()[0]
        jan_statement_id = _seed_statement(conn, account_id, jan_source_id, "2024-01-31")
        feb_statement_id = _seed_statement(conn, account_id, feb_source_id, "2024-02-29")
        abc_id = sqlite_db.upsert_instrument(conn, asset_type="equity", symbol="ABC", currency="CAD")
        xyz_id = sqlite_db.upsert_instrument(conn, asset_type="equity", symbol="XYZ", currency="CAD")
        conn.execute(
            """
            INSERT INTO position_snapshots(statement_id, account_id, as_of_date, instrument_id, quantity, market_value, currency)
            VALUES (?, ?, '2024-01-31', ?, 10, 100, 'CAD')
            """,
            (jan_statement_id, account_id, abc_id),
        )
        conn.execute(
            """
            INSERT INTO position_snapshots(statement_id, account_id, as_of_date, instrument_id, quantity, market_value, currency)
            VALUES (?, ?, '2024-02-29', ?, 5, 50, 'CAD')
            """,
            (feb_statement_id, account_id, xyz_id),
        )
        conn.execute(
            """
            INSERT INTO cash_balances(statement_id, account_id, as_of_date, currency, closing_balance)
            VALUES (?, ?, '2024-01-31', 'CAD', 25)
            """,
            (jan_statement_id, account_id),
        )
        conn.execute(
            """
            INSERT INTO cash_balances(statement_id, account_id, as_of_date, currency, closing_balance)
            VALUES (?, ?, '2024-02-29', 'CAD', 30)
            """,
            (feb_statement_id, account_id),
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
        second_source_id = conn.execute(
            "INSERT INTO source_files(relpath, parse_status) VALUES (?, 'ok') RETURNING source_file_id",
            ("Statements/Test/second.pdf",),
        ).fetchone()[0]
        first_statement_id = _seed_statement(conn, account_id, first_source_id, "2024-01-31")
        second_statement_id = _seed_statement(conn, account_id, second_source_id, "2024-01-31")
        instrument_id = sqlite_db.upsert_instrument(conn, asset_type="equity", symbol="ABC", currency="CAD")
        conn.execute(
            """
            INSERT INTO position_snapshots(statement_id, account_id, as_of_date, instrument_id, quantity, market_value, currency)
            VALUES (?, ?, '2024-01-31', ?, 100, 1000, 'CAD')
            """,
            (first_statement_id, account_id, instrument_id),
        )
        conn.execute(
            """
            INSERT INTO position_snapshots(statement_id, account_id, as_of_date, instrument_id, quantity, market_value, currency)
            VALUES (?, ?, '2024-01-31', ?, 125, 1250, 'CAD')
            """,
            (second_statement_id, account_id, instrument_id),
        )

    rows = _holdings_at("2024-01-31", [], path=db_path)
    assert len(rows) == 1
    assert rows[0]["quantity"] == 125.0
    assert rows[0]["market_value"] == 1250.0


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
        conn.execute(
            """
            INSERT INTO position_snapshots(statement_id, account_id, as_of_date, instrument_id, quantity, market_value, currency)
            VALUES (?, ?, '2024-01-31', ?, 10, 100, 'CAD')
            """,
            (statement_id, account_id, instrument_id),
        )
        conn.execute(
            """
            INSERT INTO cash_balances(statement_id, account_id, as_of_date, currency, closing_balance)
            VALUES (?, ?, '2024-01-31', 'CAD', 25)
            """,
            (statement_id, account_id),
        )
        conn.execute(
            """
            INSERT INTO cash_balances(statement_id, account_id, as_of_date, currency, closing_balance)
            VALUES (?, ?, '2024-01-31', 'USD', 10)
            """,
            (statement_id, account_id),
        )

    rows = _holdings_at("2024-01-31", [], path=db_path)
    cash_rows = {row["symbol"]: row for row in rows if row["asset_type"] == "cash"}
    assert cash_rows["CAD Cash"]["market_value"] == 25.0
    assert cash_rows["USD Cash"]["market_value"] == 10.0

    totals = monthly_route._snapshot_totals(rows, "2024-01-31")
    assert totals["native"] == {"CAD": 125.0, "USD": 10.0}
    assert totals["combined"]["CAD"] == 137.5
    assert totals["combined"]["USD"] == 110.0
