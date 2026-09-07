from ledger.db import sqlite as sqlite_db
from ledger.ingest.reconcile import (
    _cash_components,
    _position_interval_replay,
    link_transfers,
    pair_corporate_action_legs,
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


def test_manual_resolution_source_is_not_reset_by_auto_pass(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _seed_account(conn, "A1")
        statement_id = _seed_statement(
            conn, account_id, "Statements/Test/manual.pdf", "2024-04-30"
        )
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="equity",
            symbol="MAN",
            currency="CAD",
            name="MANUAL REVIEW INC",
        )
        seed_position(
            conn,
            statement_id=statement_id,
            instrument_id=instrument_id,
            quantity=50,
            currency="CAD",
        )
        transaction_id = conn.execute(
            """
            INSERT INTO transactions(
                account_id, statement_id, trade_date, txn_type, quantity,
                position_delta, currency, description, resolution_method,
                resolution_confidence, resolution_source, instrument_id
            ) VALUES (?, ?, '2024-04-12', 'buy', 50, 50, 'CAD',
                      'MANUAL REVIEW INC', 'account_holding_name', 1.0,
                      'manual', ?)
            RETURNING transaction_id
            """,
            (account_id, statement_id, instrument_id),
        ).fetchone()[0]

    summary = resolve_trade_instruments_from_holdings(db_path)

    assert summary["reset"] == 0
    with sqlite_db.session(db_path) as conn:
        row = conn.execute(
            """
            SELECT instrument_id, resolution_method, resolution_source
              FROM transactions
             WHERE transaction_id = ?
            """,
            (transaction_id,),
        ).fetchone()
    assert row["instrument_id"] == instrument_id
    assert row["resolution_method"] == "account_holding_name"
    assert row["resolution_source"] == "manual"


def _seed_merger_pair(conn, account_id: int, statement_id: int) -> tuple[int, int, int, int]:
    from_id = sqlite_db.upsert_instrument(
        conn, asset_type="equity", symbol="OLDF", currency="CAD",
    )
    to_id = sqlite_db.upsert_instrument(
        conn, asset_type="equity", symbol="NEWF", currency="CAD",
    )
    out_id = conn.execute(
        """
        INSERT INTO transactions(account_id, statement_id, trade_date, txn_type,
                                 instrument_id, quantity, currency)
        VALUES (?, ?, '2024-01-10', 'merger', ?, -1000, 'CAD')
        RETURNING transaction_id
        """,
        (account_id, statement_id, from_id),
    ).fetchone()[0]
    in_id = conn.execute(
        """
        INSERT INTO transactions(account_id, statement_id, trade_date, txn_type,
                                 instrument_id, quantity, currency)
        VALUES (?, ?, '2024-01-10', 'merger', ?, 1583, 'CAD')
        RETURNING transaction_id
        """,
        (account_id, statement_id, to_id),
    ).fetchone()[0]
    return from_id, to_id, out_id, in_id


def test_pair_corporate_action_legs_links_same_instrument_pairs_on_one_date(tmp_path):
    """A multi-fund conversion prints one same-instrument pair per fund."""
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _seed_account(conn, "A1")
        statement_id = _seed_statement(conn, account_id, "Statements/Test/m.pdf", "2024-01-31")
        fund_ids = [
            sqlite_db.upsert_instrument(conn, asset_type="mutual_fund", symbol=f"ATL{n}", currency="CAD")
            for n in (1, 2, 3)
        ]
        leg_ids = []
        for fund_id in fund_ids:
            for qty in (-500, 500):
                leg_ids.append(conn.execute(
                    """
                    INSERT INTO transactions(account_id, statement_id, trade_date,
                                             txn_type, instrument_id, quantity, currency)
                    VALUES (?, ?, '2024-02-01', 'merger', ?, ?, 'CAD')
                    RETURNING transaction_id
                    """,
                    (account_id, statement_id, fund_id, qty),
                ).fetchone()[0])

        stats = pair_corporate_action_legs(conn)

        assert stats["pairs"] == 3
        assert stats["legs_linked"] == 6
        assert stats["ambiguous_dates"] == 0
        links = dict(
            conn.execute(
                "SELECT transaction_id, counterpart_txn_id FROM transactions "
                "WHERE transaction_id IN (?, ?, ?, ?, ?, ?)",
                leg_ids,
            ).fetchall()
        )
        assert all(links[leg_ids[i]] == leg_ids[i + 1] for i in range(0, 6, 2))
        assert all(links[leg_ids[i + 1]] == leg_ids[i] for i in range(0, 6, 2))
        # Same-instrument pairs derive no ratio row.
        assert conn.execute(
            "SELECT COUNT(*) FROM instrument_journal_pairs"
        ).fetchone()[0] == 0


def test_pair_corporate_action_legs_links_printed_pairs(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _seed_account(conn, "A1")
        statement_id = _seed_statement(conn, account_id, "Statements/Test/m.pdf", "2024-01-31")
        from_id, to_id, out_id, in_id = _seed_merger_pair(conn, account_id, statement_id)

        stats = pair_corporate_action_legs(conn)

        assert stats["pairs"] == 1
        assert stats["legs_linked"] == 2
        links = dict(
            conn.execute(
                "SELECT transaction_id, counterpart_txn_id FROM transactions "
                "WHERE transaction_id IN (?, ?)",
                (out_id, in_id),
            ).fetchall()
        )
        assert links[out_id] == in_id and links[in_id] == out_id
        pair = conn.execute(
            "SELECT from_instrument_id, to_instrument_id, conversion_ratio, status "
            "FROM instrument_journal_pairs"
        ).fetchone()
        assert pair["from_instrument_id"] == from_id
        assert pair["to_instrument_id"] == to_id
        assert abs(pair["conversion_ratio"] - 1.583) < 1e-9
        assert pair["status"] == "catalog"

        # Idempotent: linked legs are never re-paired.
        assert pair_corporate_action_legs(conn)["pairs"] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM instrument_journal_pairs"
        ).fetchone()[0] == 1


def test_interval_replay_resolves_paired_merger_legs(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _seed_account(conn, "A1")
        statement_id = _seed_statement(conn, account_id, "Statements/Test/m.pdf", "2024-01-31")
        from_id, to_id, _out_id, _in_id = _seed_merger_pair(conn, account_id, statement_id)
        pair_corporate_action_legs(conn)

        from_key, to_key = (
            row["instrument_key"]
            for row in conn.execute(
                "SELECT instrument_key FROM instruments WHERE instrument_id IN (?, ?) "
                "ORDER BY instrument_id",
                (from_id, to_id),
            ).fetchall()
        )
        balances, components, missing, _ids, _renames = _position_interval_replay(
            conn,
            account_id=account_id,
            currency="CAD",
            prior_checkpoint="2023-12-31",
            current_checkpoint="2024-01-31",
            prior_rows={from_key: (from_id, 1000.0)},
        )
        assert missing == {} or all(count == 0 for count in missing.values())
        assert abs(balances[from_key] - 0.0) < 1e-9
        assert abs(balances[to_key] - 1583.0) < 1e-9
        flat = [delta for deltas in components.values() for _txn, delta in deltas]
        assert any(abs(delta + 1000.0) < 1e-9 for delta in flat)
        assert any(abs(delta - 1583.0) < 1e-9 for delta in flat)


def test_interval_replay_keeps_inconsistent_pair_ratios_missing(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _seed_account(conn, "A1")
        statement_id = _seed_statement(conn, account_id, "Statements/Test/m.pdf", "2024-01-31")
        from_id, to_id, _out_id, _in_id = _seed_merger_pair(conn, account_id, statement_id)
        pair_corporate_action_legs(conn)
        # A wrong recorded ratio must not fabricate effects.
        conn.execute(
            "UPDATE instrument_journal_pairs SET conversion_ratio = 9.9"
        )

        from_key, _to_key = (
            row["instrument_key"]
            for row in conn.execute(
                "SELECT instrument_key FROM instruments WHERE instrument_id IN (?, ?) "
                "ORDER BY instrument_id",
                (from_id, to_id),
            ).fetchall()
        )
        _balances, _components, missing, _ids, _renames = _position_interval_replay(
            conn,
            account_id=account_id,
            currency="CAD",
            prior_checkpoint="2023-12-31",
            current_checkpoint="2024-01-31",
            prior_rows={from_key: (from_id, 1000.0)},
        )
        assert sum(missing.values()) >= 2


def test_same_instrument_merger_legs_resolve_without_pair_row(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _seed_account(conn, "A1")
        statement_id = _seed_statement(conn, account_id, "Statements/Test/m.pdf", "2024-01-31")
        instrument_id = sqlite_db.upsert_instrument(
            conn, asset_type="equity", symbol="URAC", currency="USD",
        )
        conn.execute(
            """
            INSERT INTO transactions(account_id, statement_id, trade_date, txn_type,
                                     instrument_id, quantity, currency)
            VALUES (?, ?, '2024-01-10', 'merger', ?, -15000, 'USD')
            RETURNING transaction_id
            """,
            (account_id, statement_id, instrument_id),
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO transactions(account_id, statement_id, trade_date, txn_type,
                                     instrument_id, quantity, currency)
            VALUES (?, ?, '2024-01-10', 'merger', ?, 15000, 'USD')
            RETURNING transaction_id
            """,
            (account_id, statement_id, instrument_id),
        ).fetchone()[0]

        stats = pair_corporate_action_legs(conn)

        assert stats["pairs"] == 1 and stats["legs_linked"] == 2
        # Same-instrument pairs are forbidden by schema: printed equal-and-
        # opposite quantities carry the deltas, so no ratio row is needed.
        assert conn.execute(
            "SELECT COUNT(*) FROM instrument_journal_pairs"
        ).fetchone()[0] == 0

        key = conn.execute(
            "SELECT instrument_key FROM instruments WHERE instrument_id = ?",
            (instrument_id,),
        ).fetchone()[0]
        balances, _components, missing, _ids, _renames = _position_interval_replay(
            conn,
            account_id=account_id,
            currency="USD",
            prior_checkpoint="2023-12-31",
            current_checkpoint="2024-01-31",
            prior_rows={key: (instrument_id, 15000.0)},
        )
        assert missing == {} or all(count == 0 for count in missing.values())
        assert abs(balances[key] - 15000.0) < 1e-9


def test_interval_replay_attributes_drip_echo_to_printing_statement(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _seed_account(conn, "A1")
        jan_id = _seed_statement(conn, account_id, "Statements/Test/jan.pdf", "2024-01-31")
        feb_id = _seed_statement(conn, account_id, "Statements/Test/feb.pdf", "2024-02-29")
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="mutual_fund",
            symbol="TDB3087C",
            currency="CAD",
            name="TD DIV INCM-D /NL",
        )
        seed_position(
            conn,
            statement_id=jan_id,
            instrument_id=instrument_id,
            quantity=100,
            currency="CAD",
        )
        seed_position(
            conn,
            statement_id=feb_id,
            instrument_id=instrument_id,
            quantity=103,
            currency="CAD",
        )
        # TD prints February's DRIP on the February statement dated January 30:
        # the printed date precedes the prior checkpoint, but the units settle
        # inside February's interval.
        conn.execute(
            """
            INSERT INTO transactions(
                account_id, statement_id, trade_date, txn_type, instrument_id,
                quantity, position_delta, net_amount, cash_delta, currency
            ) VALUES (?, ?, '2024-01-30', 'reinvest_dividend', ?, 3, 3, 0, 0, 'CAD')
            """,
            (account_id, feb_id, instrument_id),
        )

        key = conn.execute(
            "SELECT instrument_key FROM instruments WHERE instrument_id = ?",
            (instrument_id,),
        ).fetchone()[0]
        prior_rows = {key: (instrument_id, 100.0)}

        attributed, _components, missing, _ids, _renames = _position_interval_replay(
            conn,
            account_id=account_id,
            currency="CAD",
            prior_checkpoint="2024-01-31",
            current_checkpoint="2024-02-29",
            prior_rows=prior_rows,
            scope_statement_id=feb_id,
        )
        leaked, _c2, _m2, _i2, _r2 = _position_interval_replay(
            conn,
            account_id=account_id,
            currency="CAD",
            prior_checkpoint="2024-01-31",
            current_checkpoint="2024-02-29",
            prior_rows=prior_rows,
            scope_statement_id=jan_id,
        )

    assert missing == {} or all(count == 0 for count in missing.values())
    assert abs(attributed[key] - 103.0) < 1e-9
    # Anchoring the echo to a different statement's interval must not apply it.
    assert abs(leaked[key] - 100.0) < 1e-9


def test_name_resolution_maps_drip_echo_to_fund_holding(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _seed_account(conn, "A1")
        statement_id = _seed_statement(
            conn, account_id, "Statements/Test/drip.pdf", "2024-02-29"
        )
        fund_id = sqlite_db.upsert_instrument(
            conn,
            asset_type="mutual_fund",
            symbol="TDB3087C",
            currency="CAD",
            name="TD DIV INCM-D /NL",
        )
        seed_position(
            conn,
            statement_id=statement_id,
            instrument_id=fund_id,
            quantity=1692.193,
            currency="CAD",
        )
        transaction_id = conn.execute(
            """
            INSERT INTO transactions(
                account_id, statement_id, trade_date, txn_type, quantity,
                net_amount, cash_delta, currency, description, resolution_method,
                resolution_confidence
            ) VALUES (?, ?, '2024-01-30', 'reinvest_dividend', 2.526, 0, 0, 'CAD',
                      'TD DIV INCM-D /NL''FRAC 2.526 0.00 56,317.71',
                      'unresolved_printed_identity', 0.0)
            RETURNING transaction_id
            """,
            (account_id, statement_id),
        ).fetchone()[0]

    resolve_trade_instruments_from_holdings(db_path)

    with sqlite_db.session(db_path) as conn:
        resolved = conn.execute(
            "SELECT instrument_id FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
    assert resolved["instrument_id"] == fund_id


def test_cash_components_skip_blank_cash_in_kind_rows(tmp_path):
    db_path = tmp_path / "ledger.sqlite"
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        account_id = _seed_account(conn, "A1")
        statement_id = _seed_statement(conn, account_id, "Statements/Test/c.pdf", "2024-01-31")

        def _txn(txn_type: str, cash: float | None) -> None:
            conn.execute(
                """
                INSERT INTO transactions(
                    account_id, statement_id, trade_date, txn_type, quantity,
                    net_amount, cash_delta, currency
                )
                VALUES (?, ?, '2024-01-15', ?, 10, ?, ?, 'CAD')
                """,
                (account_id, statement_id, txn_type, cash, cash),
            )

        # In-kind movements whose cash cells printed blank: no cash effect,
        # and never a "missing cash delta" complaint.
        _txn("journal", None)
        _txn("option_assignment", None)
        _txn("option_expiration", None)
        _txn("transfer_out", None)
        # A transfer that prints an amount is a real cash movement.
        _txn("transfer_in", 500.0)
        # Cash events of cash-only types must still surface as missing when
        # their cash figure was not extracted.
        _txn("dividend", None)
        _txn("sell", -700.0)

        components, missing = _cash_components(
            conn,
            account_id=account_id,
            currency="CAD",
            period_start="2024-01-01",
            period_end="2024-01-31",
        )

    assert sorted(delta for _tid, delta in components) == [-700.0, 500.0]
    assert missing == 1
