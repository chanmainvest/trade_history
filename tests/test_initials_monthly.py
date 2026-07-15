from ledger.api.routes.monthly import _holdings_at
from ledger.db import sqlite as sqlite_db
from ledger.ingest.initials import infer_initials

from .db_fixtures import seed_cash, seed_position, seed_source, seed_statement


def _seed_account(conn):
    institution_id = sqlite_db.upsert_institution(conn, "TST", "Test Broker")
    account_id = sqlite_db.upsert_account(
        conn,
        institution_id=institution_id,
        account_number="A1",
        account_type="Margin",
        base_currency="CAD",
    )
    source_file_id = seed_source(conn, "Statements/Test/sample.pdf")
    statement_id = seed_statement(
        conn,
        account_id=account_id,
        source_file_id=source_file_id,
        period_start="2024-01-01",
        period_end="2024-01-31",
    )
    return account_id, statement_id


def test_init_db_adds_initial_cash_notes_to_existing_database(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    conn = sqlite_db.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE initial_cash (
                initial_cash_id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                as_of_date TEXT NOT NULL,
                currency TEXT NOT NULL,
                balance REAL NOT NULL,
                UNIQUE(account_id, as_of_date, currency)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    sqlite_db.init_db(db_path)

    conn = sqlite_db.connect(db_path)
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(initial_cash)")}
    finally:
        conn.close()
    assert "notes" in columns


def test_infer_initials_preserves_curated_rows_and_tags_cash(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id, statement_id = _seed_account(conn)
        inferred_instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        curated_instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="XYZ",
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=statement_id,
            instrument_id=inferred_instrument_id,
            quantity=100,
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=statement_id,
            instrument_id=curated_instrument_id,
            quantity=10,
            currency="CAD",
        )
        conn.execute(
            """
            INSERT INTO transactions(account_id, trade_date, txn_type, instrument_id, quantity, net_amount, currency)
            VALUES (?, '2024-01-15', 'buy', ?, 20, -200, 'CAD')
            """,
            (account_id, inferred_instrument_id),
        )
        seed_cash(
            conn,
            statement_id=statement_id,
            currency="CAD",
            closing_balance=1000,
        )
        seed_cash(
            conn,
            statement_id=statement_id,
            currency="USD",
            closing_balance=500,
        )
        annual_source_id = seed_source(conn, "Statements/Test/annual.pdf")
        annual_statement_id = seed_statement(
            conn,
            account_id=account_id,
            source_file_id=annual_source_id,
            period_start="2023-01-01",
            period_end="2023-12-31",
            statement_type="annual",
        )
        seed_cash(
            conn,
            statement_id=annual_statement_id,
            currency="USD",
            closing_balance=9999,
        )
        conn.execute(
            """
            INSERT INTO initial_positions(account_id, as_of_date, instrument_id, quantity, currency, notes)
            VALUES (?, '2024-01-30', ?, 999, 'CAD', 'manual: reviewed opening lot')
            """,
            (account_id, curated_instrument_id),
        )
        conn.execute(
            """
            INSERT INTO initial_cash(account_id, as_of_date, currency, balance, notes)
            VALUES (?, '2024-01-30', 'CAD', 999, 'manual: reviewed opening cash')
            """,
            (account_id,),
        )
        conn.execute(
            """
            INSERT INTO initial_cash(account_id, as_of_date, currency, balance, notes)
            VALUES (?, '2024-01-30', 'USD', 123, NULL)
            """,
            (account_id,),
        )

    summary = infer_initials(db_path)

    assert summary == {"positions": 1, "cash": 1}
    with sqlite_db.session(db_path) as conn:
        position_rows = conn.execute(
            """
            SELECT inst.symbol, ip.quantity, ip.notes
              FROM initial_positions ip
              JOIN instruments inst ON inst.instrument_id = ip.instrument_id
             ORDER BY inst.symbol
            """
        ).fetchall()
        cash_rows = conn.execute(
            "SELECT currency, balance, notes FROM initial_cash ORDER BY currency"
        ).fetchall()

    assert [(r["symbol"], r["quantity"], r["notes"].split(":", 1)[0]) for r in position_rows] == [
        ("ABC", 80.0, "inferred"),
        ("XYZ", 999.0, "manual"),
    ]
    assert [(r["currency"], r["balance"], r["notes"].split(":", 1)[0]) for r in cash_rows] == [
        ("CAD", 999.0, "manual"),
        ("USD", 500.0, "inferred"),
    ]

    assert infer_initials(db_path) == {"positions": 1, "cash": 1}


def test_holdings_reconstruct_before_first_snapshot_and_after_empty_statement(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id, statement_id = _seed_account(conn)
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        conn.execute(
            """
            INSERT INTO initial_positions(account_id, as_of_date, instrument_id, quantity, currency, notes)
            VALUES (?, '2023-12-31', ?, 50, 'CAD', 'manual: opening')
            """,
            (account_id, instrument_id),
        )
        conn.execute(
            """
            INSERT INTO transactions(account_id, trade_date, txn_type, instrument_id, quantity, currency)
            VALUES (?, '2024-01-10', 'buy', ?, 10, 'CAD')
            """,
            (account_id, instrument_id),
        )
        seed_position(
            conn,
            statement_id=statement_id,
            instrument_id=instrument_id,
            quantity=100,
            currency="CAD",
        )
        later_source_id = seed_source(conn, "Statements/Test/empty-annual.pdf")
        seed_statement(
            conn,
            account_id=account_id,
            source_file_id=later_source_id,
            period_start="2024-02-01",
            period_end="2024-02-28",
            statement_type="annual",
        )
        conn.execute(
            """
            INSERT INTO transactions(account_id, trade_date, txn_type, instrument_id, quantity, currency)
            VALUES (?, '2024-02-15', 'sell', ?, 20, 'CAD')
            """,
            (account_id, instrument_id),
        )

    before_first = _holdings_at("2024-01-15", [], path=db_path)
    after_empty_statement = _holdings_at("2024-03-01", [], path=db_path)

    assert before_first[0]["quantity"] == 60.0
    assert before_first[0]["as_of_date"] == "2024-01-15"
    assert after_empty_statement[0]["quantity"] == 80.0
    assert after_empty_statement[0]["as_of_date"] == "2024-03-01"
    assert after_empty_statement[0]["checkpoint_date"] == "2024-01-31"
    assert after_empty_statement[0]["is_reconstructed"] is True
