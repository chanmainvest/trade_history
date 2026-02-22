from __future__ import annotations

import sqlite3

from trade_history.core.positions import rebuild_positions
from trade_history.db.sqlite import SQLITE_SCHEMA


def setup_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SQLITE_SCHEMA)
    conn.execute(
        """
        INSERT INTO accounts(account_id, institution, account_name)
        VALUES ('A1', 'TestBroker', 'A1'), ('A2', 'TestBroker', 'A2')
        """
    )
    conn.execute(
        """
        INSERT INTO instruments(symbol_raw, symbol_norm, asset_type)
        VALUES ('AAPL', 'AAPL', 'equity')
        """
    )
    conn.execute(
        """
        INSERT INTO statement_files(institution, file_path, format_version, parse_status, checksum)
        VALUES ('TestBroker', 'dummy.pdf', 'test', 'success', 'abc')
        """
    )
    return conn


def test_average_cost_long_position_close() -> None:
    conn = setup_conn()
    try:
        # Buy 10 @ 100 then sell 4 @ 120.
        conn.execute(
            """
            INSERT INTO events(
              account_id, trade_date, event_type, instrument_id, side, quantity, price,
              gross_amount, commission, fees, currency, source_file_id
            ) VALUES
              ('A1', '2024-01-02', 'trade', 1, 'BUY', 10, 100, -1000, 0, 0, 'USD', 1),
              ('A1', '2024-01-10', 'trade', 1, 'SELL', 4, 120, 480, 0, 0, 'USD', 1)
            """
        )
        result = rebuild_positions(conn)
        assert result["closed_lot_rows"] == 1

        row = conn.execute("SELECT realized_pl_native FROM lot_closures").fetchone()
        assert row is not None
        assert round(float(row["realized_pl_native"]), 2) == 80.0

        pos = conn.execute("SELECT quantity, avg_cost_native FROM position_state").fetchone()
        assert pos is not None
        assert round(float(pos["quantity"]), 6) == 6.0
        assert round(float(pos["avg_cost_native"]), 2) == 100.0
    finally:
        conn.close()


def test_transfer_continuity_preserves_cost_basis() -> None:
    conn = setup_conn()
    try:
        conn.execute(
            """
            INSERT INTO events(
              account_id, trade_date, event_type, instrument_id, side, quantity, price,
              gross_amount, commission, fees, currency, source_file_id
            ) VALUES
              ('A1', '2024-01-02', 'trade', 1, 'BUY', 10, 100, -1000, 0, 0, 'USD', 1),
              ('A1', '2024-01-12', 'transfer', 1, 'TRANSFER_OUT', 5, NULL, NULL, 0, 0, 'USD', 1),
              ('A2', '2024-01-13', 'transfer', 1, 'TRANSFER_IN', 5, NULL, NULL, 0, 0, 'USD', 1),
              ('A2', '2024-01-20', 'trade', 1, 'SELL', 5, 120, 600, 0, 0, 'USD', 1)
            """
        )
        result = rebuild_positions(conn)
        assert result["transfers_linked"] == 1

        # A2 closes transferred lot at carried cost 100.
        row = conn.execute(
            """
            SELECT realized_pl_native FROM lot_closures
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        assert row is not None
        assert round(float(row["realized_pl_native"]), 2) == 100.0

        # A1 should keep remaining 5 shares at same average cost.
        pos_a1 = conn.execute(
            """
            SELECT quantity, avg_cost_native FROM position_state WHERE account_id = 'A1'
            """
        ).fetchone()
        assert pos_a1 is not None
        assert round(float(pos_a1["quantity"]), 6) == 5.0
        assert round(float(pos_a1["avg_cost_native"]), 2) == 100.0
    finally:
        conn.close()


def test_transfer_in_before_out_same_day_keeps_carry_cost() -> None:
    conn = setup_conn()
    try:
        conn.execute(
            """
            INSERT INTO events(
              account_id, trade_date, event_type, instrument_id, side, quantity, price,
              gross_amount, commission, fees, currency, source_file_id
            ) VALUES
              ('A1', '2024-01-02', 'trade', 1, 'BUY', 10, 100, -1000, 0, 0, 'USD', 1),
              ('A2', '2024-01-10', 'transfer', 1, 'TRANSFER_IN', 5, NULL, -500, 0, 0, 'USD', 1),
              ('A1', '2024-01-10', 'transfer', 1, 'TRANSFER_OUT', 5, NULL, 500, 0, 0, 'USD', 1),
              ('A2', '2024-01-11', 'trade', 1, 'SELL', 5, 120, 600, 0, 0, 'USD', 1)
            """
        )
        result = rebuild_positions(conn)
        assert result["transfers_linked"] == 1

        row = conn.execute(
            """
            SELECT realized_pl_native FROM lot_closures
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        assert row is not None
        assert round(float(row["realized_pl_native"]), 2) == 100.0
    finally:
        conn.close()


def test_non_trade_events_are_ignored_for_lot_closures() -> None:
    conn = setup_conn()
    try:
        conn.execute(
            """
            INSERT INTO events(
              account_id, trade_date, event_type, instrument_id, side, quantity, price,
              gross_amount, commission, fees, currency, source_file_id
            ) VALUES
              ('A1', '2024-01-02', 'trade', 1, 'BUY', 10, 100, -1000, 0, 0, 'USD', 1),
              ('A1', '2024-01-05', 'dividend', 1, 'DIVIDEND', 1, 1000, 10, 0, 0, 'USD', 1),
              ('A1', '2024-01-10', 'trade', 1, 'SELL', 10, 110, 1100, 0, 0, 'USD', 1)
            """
        )
        result = rebuild_positions(conn)
        assert result["closed_lot_rows"] == 1

        row = conn.execute("SELECT realized_pl_native FROM lot_closures").fetchone()
        assert row is not None
        assert round(float(row["realized_pl_native"]), 2) == 100.0
    finally:
        conn.close()
