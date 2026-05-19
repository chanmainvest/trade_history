"""Tests for FIFO P/L calculation."""

import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from trade_history.analytics.pnl import compute_pnl
from trade_history.db.sqlite import init_db


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = init_db(Path(d) / "test.db")
        c.execute(
            "INSERT INTO accounts (institution, account_id, account_type) VALUES ('TEST','ACC1','margin')"
        )
        c.execute(
            "INSERT INTO instruments (symbol, asset_type) VALUES ('AAPL','equity')"
        )
        c.commit()
        yield c
        c.close()


_SRC = "test.pdf"


def _insert_tx(conn, date, activity, qty, price, amount):
    conn.execute(
        """INSERT INTO transactions
            (account_id, instrument_id, trade_date, activity, quantity, price, amount, currency, source_file)
           VALUES (1, 1, ?, ?, ?, ?, ?, 'CAD', ?)""",
        (date, activity, qty, price, amount, _SRC),
    )


# ── Long position tests ─────────────────────────────────────────────────────


def test_simple_buy_sell(conn):
    # Buy 100 @ 10, sell 100 @ 15 → P/L = 500
    _insert_tx(conn, "2024-01-01", "bought", 100, 10.0, -1000.0)
    _insert_tx(conn, "2024-06-01", "sold", 100, 15.0, 1500.0)
    conn.commit()

    trades = compute_pnl(conn)
    assert len(trades) == 1
    assert trades[0].realized_pnl == Decimal("500")
    assert trades[0].direction == "long"


def test_partial_sell(conn):
    # Buy 200 @ 10, sell 100 @ 12 → P/L = 200 on 100 shares
    _insert_tx(conn, "2024-01-01", "bought", 200, 10.0, -2000.0)
    _insert_tx(conn, "2024-06-01", "sold", 100, 12.0, 1200.0)
    conn.commit()

    trades = compute_pnl(conn)
    assert len(trades) == 1
    assert trades[0].realized_pnl == Decimal("200")
    assert trades[0].quantity == Decimal("100")
    assert trades[0].direction == "long"


def test_no_closed_positions(conn):
    # Only buy, no sell → no closed trades
    _insert_tx(conn, "2024-01-01", "bought", 100, 10.0, -1000.0)
    conn.commit()

    trades = compute_pnl(conn)
    assert len(trades) == 0


# ── Short position tests ────────────────────────────────────────────────────


def test_short_sell_then_cover(conn):
    """Sell 100 @ 15 (short), buy 100 @ 10 (cover) → P/L = 500"""
    _insert_tx(conn, "2024-01-01", "sold", 100, 15.0, 1500.0)
    _insert_tx(conn, "2024-06-01", "bought", 100, 10.0, -1000.0)
    conn.commit()

    trades = compute_pnl(conn)
    assert len(trades) == 1
    assert trades[0].direction == "short"
    assert trades[0].realized_pnl == Decimal("500")
    assert trades[0].quantity == Decimal("100")


def test_short_partial_cover(conn):
    """Sell 200 @ 15 (short), buy 100 @ 10 (partial cover) → 1 closed short"""
    _insert_tx(conn, "2024-01-01", "sold", 200, 15.0, 3000.0)
    _insert_tx(conn, "2024-06-01", "bought", 100, 10.0, -1000.0)
    conn.commit()

    trades = compute_pnl(conn)
    assert len(trades) == 1
    assert trades[0].direction == "short"
    assert trades[0].quantity == Decimal("100")
    # Proceeds = 3000 * (100/200) = 1500, cost = 10*100 = 1000 → P/L = 500
    assert trades[0].realized_pnl == Decimal("500")


def test_short_loss(conn):
    """Sell 100 @ 10 (short), buy 100 @ 15 (cover at loss) → P/L = -500"""
    _insert_tx(conn, "2024-01-01", "sold", 100, 10.0, 1000.0)
    _insert_tx(conn, "2024-06-01", "bought", 100, 15.0, -1500.0)
    conn.commit()

    trades = compute_pnl(conn)
    assert len(trades) == 1
    assert trades[0].direction == "short"
    assert trades[0].realized_pnl == Decimal("-500")


def test_sell_exceeds_long_creates_short(conn):
    """Buy 100 @ 10, sell 150 @ 15 → 1 long close (100) + 1 short open (50)"""
    _insert_tx(conn, "2024-01-01", "bought", 100, 10.0, -1000.0)
    _insert_tx(conn, "2024-06-01", "sold", 150, 15.0, 2250.0)
    conn.commit()

    trades = compute_pnl(conn)
    # Only the long portion is closed; short portion is still open
    assert len(trades) == 1
    assert trades[0].direction == "long"
    assert trades[0].quantity == Decimal("100")
    # Proceeds = 2250 * (100/150) = 1500; cost = 10*100 = 1000 → P/L = 500
    assert trades[0].realized_pnl == Decimal("500")


def test_buy_exceeds_short_creates_long(conn):
    """Sell 50 @ 20 (short), buy 150 @ 10 → 1 short close (50) + 100 long open"""
    _insert_tx(conn, "2024-01-01", "sold", 50, 20.0, 1000.0)
    _insert_tx(conn, "2024-06-01", "bought", 150, 10.0, -1500.0)
    conn.commit()

    trades = compute_pnl(conn)
    # Short close for 50 shares
    assert len(trades) == 1
    assert trades[0].direction == "short"
    assert trades[0].quantity == Decimal("50")
    # Proceeds = 1000, cost = 10*50 = 500 → P/L = 500
    assert trades[0].realized_pnl == Decimal("500")


def test_no_sell_only_short(conn):
    """Only sell without prior buy → no closed trades (open short)"""
    _insert_tx(conn, "2024-01-01", "sold", 100, 15.0, 1500.0)
    conn.commit()

    trades = compute_pnl(conn)
    assert len(trades) == 0
