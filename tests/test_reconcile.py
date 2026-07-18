from ledger.db import sqlite as sqlite_db
from ledger.ingest.reconcile import (
    link_transfers,
    rebuild_position_transaction_links,
    resolve_trade_instruments_from_holdings,
)

from .db_fixtures import seed_position, seed_source, seed_statement


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
    source_file_id = seed_source(conn, relpath)
    return seed_statement(
        conn,
        account_id=account_id,
        source_file_id=source_file_id,
        period_end=period_end,
    )


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
        seed_position(
            conn,
            statement_id=from_statement_id,
            instrument_id=instrument_id,
            quantity=90,
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=to_statement_id,
            instrument_id=instrument_id,
            quantity=10,
            currency="CAD",
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


def test_name_only_trades_resolve_from_unique_observed_holding_names(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _seed_account(conn, "A1")
        statement_id = _seed_statement(
            conn, account_id, "Statements/Test/names.pdf", "2024-03-31"
        )
        mcewen_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="MUX",
            currency="CAD",
            name="MCEWEN INC COMMON STOCK",
        )
        seed_position(
            conn,
            statement_id=statement_id,
            instrument_id=mcewen_id,
            quantity=800,
            currency="CAD",
        )
        transaction_id = conn.execute(
            """
            INSERT INTO transactions(
                account_id, statement_id, trade_date, txn_type, quantity,
                position_delta, currency, description, resolution_method,
                resolution_confidence
            ) VALUES (?, ?, '2024-03-27', 'buy', 800, 800, 'CAD',
                      'MCEWEN MINING INC', 'unresolved_printed_identity', 0.0)
            RETURNING transaction_id
            """,
            (account_id, statement_id),
        ).fetchone()[0]

    first = resolve_trade_instruments_from_holdings(db_path)
    second = resolve_trade_instruments_from_holdings(db_path)

    assert first["resolved_account"] == 1
    assert first["resolved"] == 1
    assert second["reset"] == 1
    assert second["resolved"] == 1
    with sqlite_db.session(db_path) as conn:
        resolved = conn.execute(
            """
            SELECT instrument_id, resolution_method, resolution_confidence,
                   resolution_evidence_id
              FROM transactions
             WHERE transaction_id = ?
            """,
            (transaction_id,),
        ).fetchone()
    assert resolved["instrument_id"] == mcewen_id
    assert resolved["resolution_method"] == "account_holding_name"
    assert resolved["resolution_confidence"] == 0.86
    assert resolved["resolution_evidence_id"] is not None


def test_name_resolution_rejects_generic_ambiguity_and_wrong_currency(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _seed_account(conn, "A1")
        statement_id = _seed_statement(
            conn, account_id, "Statements/Test/ambiguous.pdf", "2024-06-30"
        )
        for symbol, currency in (("EWW", "USD"), ("EWM", "USD"), ("TECK.B", "CAD")):
            instrument_id = sqlite_db.upsert_instrument(
                conn,
                asset_type="etf" if symbol.startswith("EW") else "equity",
                symbol=symbol,
                currency=currency,
                name="ISHARES INC" if symbol.startswith("EW") else "TECK RESOURCES LIMITED",
            )
            seed_position(
                conn,
                statement_id=statement_id,
                instrument_id=instrument_id,
                quantity=10,
                currency=currency,
            )
        for description, currency in (("ISHARES INC", "USD"), ("TECK RESOURCES LIMITED", "USD")):
            conn.execute(
                """
                INSERT INTO transactions(
                    account_id, statement_id, trade_date, txn_type, quantity,
                    position_delta, currency, description, resolution_method,
                    resolution_confidence
                ) VALUES (?, ?, '2024-06-10', 'buy', 10, 10, ?, ?,
                          'unresolved_printed_identity', 0.0)
                """,
                (account_id, statement_id, currency, description),
            )

    summary = resolve_trade_instruments_from_holdings(db_path)

    assert summary["resolved"] == 0
    assert summary["ambiguous"] == 1
    assert summary["unmatched"] == 1
    with sqlite_db.session(db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE instrument_id IS NULL"
        ).fetchone()[0] == 2


def test_name_resolution_uses_strict_portfolio_wide_fallback(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        source_account = _seed_account(conn, "A1")
        observed_account = _seed_account(conn, "A2")
        source_statement = _seed_statement(
            conn, source_account, "Statements/Test/source.pdf", "2024-01-31"
        )
        observed_statement = _seed_statement(
            conn, observed_account, "Statements/Test/observed.pdf", "2024-01-31"
        )
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="TECK.B",
            currency="CAD",
            name="TECK RESOURCES LIMITED",
        )
        seed_position(
            conn,
            statement_id=observed_statement,
            instrument_id=instrument_id,
            quantity=100,
            currency="CAD",
        )
        transaction_id = conn.execute(
            """
            INSERT INTO transactions(
                account_id, statement_id, trade_date, txn_type, quantity,
                position_delta, currency, description, resolution_method,
                resolution_confidence
            ) VALUES (?, ?, '2024-01-10', 'buy', 100, 100, 'CAD',
                      'TECK RESOURCES LIMITED', 'unresolved_printed_identity', 0.0)
            RETURNING transaction_id
            """,
            (source_account, source_statement),
        ).fetchone()[0]

    summary = resolve_trade_instruments_from_holdings(db_path)

    assert summary["resolved_portfolio"] == 1
    with sqlite_db.session(db_path) as conn:
        resolved = conn.execute(
            "SELECT instrument_id, resolution_method FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
    assert tuple(resolved) == (instrument_id, "portfolio_holding_name")
