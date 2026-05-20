from ledger.api.routes.monthly import _holdings_at
from ledger.db import sqlite as sqlite_db
from ledger.ingest.initials import infer_initials


def _seed_account(conn):
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
        ("Statements/Test/sample.pdf",),
    ).fetchone()[0]
    statement_id = conn.execute(
        """
        INSERT INTO statements(source_file_id, account_id, period_start, period_end)
        VALUES (?, ?, '2024-01-01', '2024-01-31')
        RETURNING statement_id
        """,
        (source_file_id, account_id),
    ).fetchone()[0]
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
        conn.execute(
            """
            INSERT INTO position_snapshots(statement_id, account_id, as_of_date, instrument_id, quantity, currency)
            VALUES (?, ?, '2024-01-31', ?, 100, 'CAD')
            """,
            (statement_id, account_id, inferred_instrument_id),
        )
        conn.execute(
            """
            INSERT INTO position_snapshots(statement_id, account_id, as_of_date, instrument_id, quantity, currency)
            VALUES (?, ?, '2024-01-31', ?, 10, 'CAD')
            """,
            (statement_id, account_id, curated_instrument_id),
        )
        conn.execute(
            """
            INSERT INTO transactions(account_id, trade_date, txn_type, instrument_id, quantity, net_amount, currency)
            VALUES (?, '2024-01-15', 'buy', ?, 20, -200, 'CAD')
            """,
            (account_id, inferred_instrument_id),
        )
        conn.execute(
            """
            INSERT INTO cash_balances(statement_id, account_id, as_of_date, currency, closing_balance)
            VALUES (?, ?, '2024-01-31', 'CAD', 1000)
            """,
            (statement_id, account_id),
        )
        conn.execute(
            """
            INSERT INTO cash_balances(statement_id, account_id, as_of_date, currency, closing_balance)
            VALUES (?, ?, '2024-01-31', 'USD', 500)
            """,
            (statement_id, account_id),
        )
        annual_source_id = conn.execute(
            "INSERT INTO source_files(relpath, parse_status) VALUES (?, 'ok') RETURNING source_file_id",
            ("Statements/Test/annual.pdf",),
        ).fetchone()[0]
        annual_statement_id = conn.execute(
            """
            INSERT INTO statements(source_file_id, account_id, period_start, period_end, statement_type)
            VALUES (?, ?, '2023-01-01', '2023-12-31', 'annual')
            RETURNING statement_id
            """,
            (annual_source_id, account_id),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO cash_balances(statement_id, account_id, as_of_date, currency, closing_balance)
            VALUES (?, ?, '2023-12-31', 'USD', 9999)
            """,
            (annual_statement_id, account_id),
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
        conn.execute(
            """
            INSERT INTO position_snapshots(statement_id, account_id, as_of_date, instrument_id, quantity, currency)
            VALUES (?, ?, '2024-01-31', ?, 100, 'CAD')
            """,
            (statement_id, account_id, instrument_id),
        )
        later_source_id = conn.execute(
            "INSERT INTO source_files(relpath, parse_status) VALUES (?, 'ok') RETURNING source_file_id",
            ("Statements/Test/empty-annual.pdf",),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO statements(source_file_id, account_id, period_start, period_end, statement_type)
            VALUES (?, ?, '2024-02-01', '2024-02-28', 'annual')
            """,
            (later_source_id, account_id),
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
    assert after_empty_statement[0]["as_of_date"] == "2024-01-31"
