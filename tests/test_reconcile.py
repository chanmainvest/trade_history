from ledger.db import sqlite as sqlite_db
from ledger.ingest.reconcile import link_transfers, rebuild_position_transaction_links


def _seed_account(conn, account_number: str) -> int:
    institution_id = sqlite_db.upsert_institution(conn, "TST", "Test Broker")
    return sqlite_db.upsert_account(
        conn,
        institution_id=institution_id,
        account_number=account_number,
        account_type="Margin",
        base_currency="CAD",
    )


def _seed_statement(conn, account_id: int, relpath: str, period_end: str) -> int:
    source_file_id = conn.execute(
        "INSERT INTO source_files(relpath, parse_status) VALUES (?, 'ok') RETURNING source_file_id",
        (relpath,),
    ).fetchone()[0]
    return conn.execute(
        """
        INSERT INTO statements(source_file_id, account_id, period_start, period_end)
        VALUES (?, ?, ?, ?)
        RETURNING statement_id
        """,
        (source_file_id, account_id, period_end[:8] + "01", period_end),
    ).fetchone()[0]


def test_transfer_and_position_reconciliation_links_unambiguous_rows(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        from_account_id = _seed_account(conn, "A1")
        to_account_id = _seed_account(conn, "A2")
        from_statement_id = _seed_statement(conn, from_account_id, "Statements/Test/from.pdf", "2024-01-31")
        to_statement_id = _seed_statement(conn, to_account_id, "Statements/Test/to.pdf", "2024-01-31")
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        out_id = conn.execute(
            """
            INSERT INTO transactions(account_id, statement_id, trade_date, txn_type, instrument_id, quantity, currency)
            VALUES (?, ?, '2024-01-10', 'transfer_out', ?, 10, 'CAD')
            RETURNING transaction_id
            """,
            (from_account_id, from_statement_id, instrument_id),
        ).fetchone()[0]
        in_id = conn.execute(
            """
            INSERT INTO transactions(account_id, statement_id, trade_date, txn_type, instrument_id, quantity, currency)
            VALUES (?, ?, '2024-01-12', 'transfer_in', ?, 10, 'CAD')
            RETURNING transaction_id
            """,
            (to_account_id, to_statement_id, instrument_id),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO position_snapshots(statement_id, account_id, as_of_date, instrument_id, quantity, currency)
            VALUES (?, ?, '2024-01-31', ?, 90, 'CAD')
            """,
            (from_statement_id, from_account_id, instrument_id),
        )
        conn.execute(
            """
            INSERT INTO position_snapshots(statement_id, account_id, as_of_date, instrument_id, quantity, currency)
            VALUES (?, ?, '2024-01-31', ?, 10, 'CAD')
            """,
            (to_statement_id, to_account_id, instrument_id),
        )

    transfer_summary = link_transfers(db_path)
    position_summary = rebuild_position_transaction_links(db_path)

    assert transfer_summary["matched"] == 1
    assert position_summary == {"links": 2, "snapshots": 2}
    with sqlite_db.session(db_path) as conn:
        out_row = conn.execute(
            "SELECT counterpart_account_id, counterpart_txn_id FROM transactions WHERE transaction_id = ?",
            (out_id,),
        ).fetchone()
        in_row = conn.execute(
            "SELECT counterpart_account_id, counterpart_txn_id FROM transactions WHERE transaction_id = ?",
            (in_id,),
        ).fetchone()
        link_count = conn.execute("SELECT COUNT(*) FROM account_links").fetchone()[0]
        position_links = conn.execute(
            "SELECT quantity_attributed FROM position_transaction_links ORDER BY quantity_attributed"
        ).fetchall()

    assert out_row["counterpart_account_id"] == to_account_id
    assert out_row["counterpart_txn_id"] == in_id
    assert in_row["counterpart_account_id"] == from_account_id
    assert in_row["counterpart_txn_id"] == out_id
    assert link_count == 1
    assert [row["quantity_attributed"] for row in position_links] == [-10.0, 10.0]