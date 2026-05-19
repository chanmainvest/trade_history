"""Tests for open-position computation."""

import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from trade_history.analytics.positions import get_open_positions
from trade_history.db.sqlite import init_db


@pytest.fixture
def conn():
    with tempfile.TemporaryDirectory() as d:
        c = init_db(Path(d) / "test.db")
        c.execute(
            "INSERT INTO accounts (institution, account_id, account_type) VALUES ('TEST','ACC1','margin')"
        )
        c.execute(
            "INSERT INTO instruments (symbol, asset_type) VALUES ('BCE','equity')"
        )
        c.commit()
        yield c
        c.close()


_SRC = "test.pdf"


def test_open_position_after_buy(conn):
    conn.execute(
        """INSERT INTO transactions
            (account_id, instrument_id, trade_date, activity, quantity, price, amount, currency, source_file)
           VALUES (1, 1, '2024-01-01', 'bought', 200, 50.0, -10000.0, 'CAD', ?)""",
        (_SRC,),
    )
    conn.commit()

    positions = get_open_positions(conn)
    assert len(positions) == 1
    assert positions[0].symbol == "BCE"
    assert positions[0].quantity == Decimal("200")


def test_position_zero_after_full_sell(conn):
    conn.execute(
        """INSERT INTO transactions
            (account_id, instrument_id, trade_date, activity, quantity, price, amount, currency, source_file)
           VALUES (1, 1, '2024-01-01', 'bought', 100, 50.0, -5000.0, 'CAD', ?)""",
        (_SRC,),
    )
    conn.execute(
        """INSERT INTO transactions
            (account_id, instrument_id, trade_date, activity, quantity, price, amount, currency, source_file)
           VALUES (1, 1, '2024-06-01', 'sold', 100, 55.0, 5500.0, 'CAD', ?)""",
        (_SRC,),
    )
    conn.commit()

    positions = get_open_positions(conn)
    assert len(positions) == 0


def test_long_direction(conn):
    """A buy creates a long position."""
    conn.execute(
        """INSERT INTO transactions
            (account_id, instrument_id, trade_date, activity, quantity, price, amount, currency, source_file)
           VALUES (1, 1, '2024-01-01', 'bought', 100, 50.0, -5000.0, 'CAD', ?)""",
        (_SRC,),
    )
    conn.commit()

    positions = get_open_positions(conn)
    assert len(positions) == 1
    assert positions[0].direction == "long"
    assert positions[0].quantity == Decimal("100")


def test_short_position_visible(conn):
    """A sell without prior buy creates a short position."""
    conn.execute(
        """INSERT INTO transactions
            (account_id, instrument_id, trade_date, activity, quantity, price, amount, currency, source_file)
           VALUES (1, 1, '2024-01-01', 'sold', 100, 55.0, 5500.0, 'CAD', ?)""",
        (_SRC,),
    )
    conn.commit()

    positions = get_open_positions(conn)
    assert len(positions) == 1
    assert positions[0].direction == "short"
    assert positions[0].quantity == Decimal("100")  # absolute value
    assert positions[0].symbol == "BCE"
