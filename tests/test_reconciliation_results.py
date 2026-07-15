"""Regression coverage for persisted, non-fabricating reconciliation results."""
from __future__ import annotations

from ledger.db import sqlite as sqlite_db
from ledger.ingest.pipeline import activate_source_result
from ledger.ingest.reconcile import rebuild_reconciliation_results, reconcile_after_ingest
from ledger.parsers.td import TDParser

from .db_fixtures import seed_cash, seed_position, seed_source, seed_statement
from .fixture_loader import load_fixture


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
        period_end=f"{month}-28",
        period_start=f"{month}-01",
    )


def _transaction(
    conn,
    *,
    account_id: int,
    statement_id: int,
    trade_date: str,
    txn_type: str,
    currency: str = "CAD",
    instrument_id: int | None = None,
    quantity: float | None = None,
    position_delta: float | None = None,
    net_amount: float | None = None,
    cash_delta: float | None = None,
    cash_effective_date: str | None = None,
) -> int:
    return int(
        conn.execute(
            """
            INSERT INTO transactions(
                account_id, statement_id, trade_date, txn_type, instrument_id,
                quantity, position_delta, net_amount, cash_delta,
                cash_effective_date, currency
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING transaction_id
            """,
            (
                account_id,
                statement_id,
                trade_date,
                txn_type,
                instrument_id,
                quantity,
                position_delta,
                net_amount,
                cash_delta,
                cash_effective_date or trade_date,
                currency,
            ),
        ).fetchone()[0]
    )


def test_position_results_store_components_residuals_and_are_idempotent(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        jan = _statement(conn, account_id, "2024-01")
        feb = _statement(conn, account_id, "2024-02")
        mar = _statement(conn, account_id, "2024-03")
        seed_position(conn, statement_id=jan, instrument_id=instrument_id, quantity=10, currency="CAD")
        seed_position(conn, statement_id=feb, instrument_id=instrument_id, quantity=12, currency="CAD")
        seed_position(conn, statement_id=mar, instrument_id=instrument_id, quantity=11, currency="CAD")
        conn.execute(
            """
            INSERT INTO reconciliation_results(
                reconciliation_key, kind, account_id, currency, status
            ) VALUES ('reviewed:position-note', 'position', ?, 'CAD', 'not_applicable')
            """,
            (account_id,),
        )
        buy_id = _transaction(
            conn,
            account_id=account_id,
            statement_id=feb,
            trade_date="2024-02-10",
            txn_type="buy",
            instrument_id=instrument_id,
            quantity=2,
            position_delta=2,
        )

    first = rebuild_reconciliation_results(db_path)
    second = rebuild_reconciliation_results(db_path)

    assert first["positions"]["reconciled"] == 1
    assert first["positions"]["unexplained_residual"] == 1
    assert second["cleared"] == first["positions"]["results"]
    with sqlite_db.session(db_path) as conn:
        feb_result = conn.execute(
            """
            SELECT reconciliation_id, opening_value, summed_deltas,
                   expected_close, reported_close, residual, status
              FROM reconciliation_results
             WHERE kind = 'position' AND statement_id = ?
            """,
            (feb,),
        ).fetchone()
        mar_result = conn.execute(
            """
            SELECT residual, status
              FROM reconciliation_results
             WHERE kind = 'position' AND statement_id = ?
            """,
            (mar,),
        ).fetchone()
        components = conn.execute(
            """
            SELECT transaction_id, delta
              FROM reconciliation_components
             WHERE reconciliation_id = ?
            """,
            (feb_result["reconciliation_id"],),
        ).fetchall()
        manual_result_count = conn.execute(
            """
            SELECT COUNT(*) FROM reconciliation_results
             WHERE reconciliation_key = 'reviewed:position-note'
            """
        ).fetchone()[0]

    assert (
        feb_result["opening_value"],
        feb_result["summed_deltas"],
        feb_result["expected_close"],
        feb_result["reported_close"],
        feb_result["residual"],
        feb_result["status"],
    ) == (10.0, 2.0, 12.0, 12.0, 0.0, "reconciled")
    assert (mar_result["residual"], mar_result["status"]) == (-1.0, "unexplained_residual")
    assert [(row["transaction_id"], row["delta"]) for row in components] == [(buy_id, 2.0)]
    assert manual_result_count == 1


def test_cash_results_cover_statement_and_adjacent_checkpoint_equations(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        jan = _statement(conn, account_id, "2024-01")
        feb = _statement(conn, account_id, "2024-02")
        mar = _statement(conn, account_id, "2024-03")
        seed_cash(conn, statement_id=jan, currency="CAD", opening_balance=100, closing_balance=90)
        seed_cash(conn, statement_id=feb, currency="CAD", opening_balance=90, closing_balance=85)
        seed_cash(conn, statement_id=mar, currency="CAD", opening_balance=85, closing_balance=85)
        jan_txn = _transaction(
            conn,
            account_id=account_id,
            statement_id=jan,
            trade_date="2024-01-10",
            txn_type="buy",
            net_amount=-10,
            cash_delta=-10,
        )
        _transaction(
            conn,
            account_id=account_id,
            statement_id=feb,
            trade_date="2024-02-10",
            txn_type="fee",
            net_amount=-5,
            cash_delta=-5,
        )
        _transaction(
            conn,
            account_id=account_id,
            statement_id=mar,
            trade_date="2024-03-10",
            txn_type="buy",
        )

    summary = rebuild_reconciliation_results(db_path)

    assert summary["cash"]["reconciled"] == 4
    assert summary["cash"]["incomplete_input"] == 1
    with sqlite_db.session(db_path) as conn:
        jan_direct = conn.execute(
            """
            SELECT reconciliation_id, expected_close, reported_close, status
              FROM reconciliation_results
             WHERE reconciliation_key LIKE 'recon:v1:cash:statement:%'
               AND statement_id = ?
            """,
            (jan,),
        ).fetchone()
        feb_continuity = conn.execute(
            """
            SELECT opening_value, expected_close, reported_close, residual, status
              FROM reconciliation_results
             WHERE reconciliation_key LIKE 'recon:v1:cash:continuity:%'
               AND statement_id = ?
            """,
            (feb,),
        ).fetchone()
        mar_direct = conn.execute(
            """
            SELECT status, reason
              FROM reconciliation_results
             WHERE reconciliation_key LIKE 'recon:v1:cash:statement:%'
               AND statement_id = ?
            """,
            (mar,),
        ).fetchone()
        jan_components = conn.execute(
            """
            SELECT transaction_id, delta
              FROM reconciliation_components
             WHERE reconciliation_id = ?
            """,
            (jan_direct["reconciliation_id"],),
        ).fetchall()

    assert (jan_direct["expected_close"], jan_direct["reported_close"], jan_direct["status"]) == (
        90.0,
        90.0,
        "reconciled",
    )
    assert (
        feb_continuity["opening_value"],
        feb_continuity["expected_close"],
        feb_continuity["reported_close"],
        feb_continuity["residual"],
        feb_continuity["status"],
    ) == (90.0, 90.0, 90.0, 0.0, "reconciled")
    assert mar_direct["status"] == "incomplete_input"
    assert "no cash delta" in mar_direct["reason"]
    assert [(row["transaction_id"], row["delta"]) for row in jan_components] == [(jan_txn, -10.0)]


def test_cash_results_use_effective_date_across_statement_rows(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        jan = _statement(conn, account_id, "2024-01")
        feb = _statement(conn, account_id, "2024-02")
        seed_cash(conn, statement_id=jan, currency="CAD", opening_balance=100, closing_balance=100)
        seed_cash(conn, statement_id=feb, currency="CAD", opening_balance=100, closing_balance=90)
        settled_in_feb = _transaction(
            conn,
            account_id=account_id,
            statement_id=jan,
            trade_date="2024-01-31",
            cash_effective_date="2024-02-01",
            txn_type="buy",
            net_amount=-10,
            cash_delta=-10,
        )

    summary = rebuild_reconciliation_results(db_path)

    assert summary["cash"]["reconciled"] == 3
    with sqlite_db.session(db_path) as conn:
        jan_direct = conn.execute(
            """
            SELECT expected_close, status FROM reconciliation_results
             WHERE reconciliation_key LIKE 'recon:v1:cash:statement:%'
               AND statement_id = ?
            """,
            (jan,),
        ).fetchone()
        feb_direct = conn.execute(
            """
            SELECT reconciliation_id, expected_close, status
              FROM reconciliation_results
             WHERE reconciliation_key LIKE 'recon:v1:cash:statement:%'
               AND statement_id = ?
            """,
            (feb,),
        ).fetchone()
        feb_components = conn.execute(
            """
            SELECT transaction_id, delta FROM reconciliation_components
             WHERE reconciliation_id = ?
            """,
            (feb_direct["reconciliation_id"],),
        ).fetchall()

    assert (jan_direct["expected_close"], jan_direct["status"]) == (100.0, "reconciled")
    assert (feb_direct["expected_close"], feb_direct["status"]) == (90.0, "reconciled")
    assert [(row["transaction_id"], row["delta"]) for row in feb_components] == [
        (settled_in_feb, -10.0)
    ]


def test_position_result_quarantines_legacy_underdetermined_corporate_action(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="ABC",
            currency="CAD",
        )
        jan = _statement(conn, account_id, "2024-01")
        feb = _statement(conn, account_id, "2024-02")
        seed_position(conn, statement_id=jan, instrument_id=instrument_id, quantity=10, currency="CAD")
        seed_position(conn, statement_id=feb, instrument_id=instrument_id, quantity=10, currency="CAD")
        _transaction(
            conn,
            account_id=account_id,
            statement_id=feb,
            trade_date="2024-02-10",
            txn_type="stock_split",
            instrument_id=instrument_id,
            quantity=2,
        )

    summary = rebuild_reconciliation_results(db_path)

    assert summary["positions"]["incomplete_input"] == 1
    with sqlite_db.session(db_path) as conn:
        result = conn.execute(
            """
            SELECT expected_close, residual, status, reason
              FROM reconciliation_results
             WHERE kind = 'position' AND statement_id = ?
            """,
            (feb,),
        ).fetchone()

    assert result["expected_close"] is None
    assert result["residual"] is None
    assert result["status"] == "incomplete_input"
    assert "lack a position delta" in result["reason"]


def test_statement_total_and_incomplete_scope_results_are_explicit(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _account(conn)
        first = _statement(conn, account_id, "2024-01")
        second = _statement(conn, account_id, "2024-02")
        alpha = sqlite_db.upsert_instrument(conn, asset_type="equity", symbol="AAA", currency="CAD")
        beta = sqlite_db.upsert_instrument(conn, asset_type="equity", symbol="BBB", currency="CAD")
        seed_position(
            conn,
            statement_id=first,
            instrument_id=alpha,
            quantity=3,
            currency="CAD",
            market_value=60,
        )
        seed_position(
            conn,
            statement_id=first,
            instrument_id=beta,
            quantity=2,
            currency="CAD",
            market_value=40,
        )
        first_scope = conn.execute(
            """
            SELECT snapshot_set_id FROM snapshot_sets
             WHERE statement_id = ? AND section_type = 'positions'
            """,
            (first,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE snapshot_sets SET reported_total = 100 WHERE snapshot_set_id = ?",
            (first_scope,),
        )
        seed_cash(
            conn,
            statement_id=first,
            currency="CAD",
            opening_balance=20,
            closing_balance=20,
        )
        summary_scope = conn.execute(
            """
            INSERT INTO snapshot_sets(
                statement_id, account_id, as_of_date, currency, section_type,
                scope_key, completeness, reported_total, validation_status
            )
            SELECT statement_id, account_id, period_end, 'CAD', 'summary',
                   'default', 'complete', 120, 'valid'
              FROM statements
             WHERE statement_id = ?
            RETURNING snapshot_set_id
            """,
            (first,),
        ).fetchone()[0]
        seed_position(
            conn,
            statement_id=second,
            instrument_id=alpha,
            quantity=3,
            currency="CAD",
            completeness="unknown",
        )

    summary = rebuild_reconciliation_results(db_path)

    assert summary["statement_totals"]["reconciled"] == 2
    assert summary["positions"]["incomplete_input"] == 2
    with sqlite_db.session(db_path) as conn:
        total = conn.execute(
            """
            SELECT expected_close, reported_close, residual, status
              FROM reconciliation_results
             WHERE kind = 'statement_total' AND snapshot_set_id = ?
            """,
            (first_scope,),
        ).fetchone()
        portfolio_total = conn.execute(
            """
            SELECT expected_close, reported_close, residual, status
              FROM reconciliation_results
             WHERE kind = 'statement_total' AND snapshot_set_id = ?
            """,
            (summary_scope,),
        ).fetchone()
        incomplete = conn.execute(
            """
            SELECT status, reason
              FROM reconciliation_results
             WHERE kind = 'position' AND statement_id = ?
            """,
            (second,),
        ).fetchone()

    assert tuple(total) == (100.0, 100.0, 0.0, "reconciled")
    assert tuple(portfolio_total) == (120.0, 120.0, 0.0, "reconciled")
    assert incomplete["status"] == "incomplete_input"
    assert incomplete["reason"] == "current position scope is not complete"


def test_td_golden_fixture_persists_only_explained_or_missing_first_checkpoints(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    pdf = load_fixture("td/full_header_bundle_known_broken.txt")
    result = TDParser().parse(pdf)
    with sqlite_db.session(db_path) as conn:
        activate_source_result(
            conn,
            pdf=pdf,
            institution_code="TD",
            parser_name=TDParser.NAME,
            parser_version=TDParser.VERSION,
            result=result,
        )

    summary = reconcile_after_ingest(db_path)

    assert summary["results"]["positions"].get("unexplained_residual", 0) == 0
    assert summary["results"]["cash"].get("unexplained_residual", 0) == 0
    with sqlite_db.session(db_path) as conn:
        statuses = {
            row[0]
            for row in conn.execute(
                "SELECT status FROM reconciliation_results"
            ).fetchall()
        }
    assert statuses <= {"reconciled", "missing_prior_checkpoint"}
