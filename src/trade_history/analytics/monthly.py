"""Compute monthly balance snapshots from position_state."""

from __future__ import annotations

import logging
import sqlite3

log = logging.getLogger(__name__)


def compute_monthly_balances(conn: sqlite3.Connection) -> int:
    """Materialize monthly_balances from position_state.

    For each (account_id, instrument_id, year-month), picks the position_state
    row with the latest as_of_date within that month.

    Note: position_state stores market_price = total market value and
    market_value = per-unit price (column name swap from extractors).
    We swap them back here so monthly_balances has correct semantics.

    Returns the number of rows inserted.
    """
    conn.execute("DELETE FROM monthly_balances")

    cur = conn.execute(
        """
        INSERT OR REPLACE INTO monthly_balances
            (account_id, instrument_id, year_month, quantity,
             avg_cost, market_price, market_value, currency, as_of_date, statement_id)
        SELECT
            ps.account_id,
            ps.instrument_id,
            STRFTIME('%Y-%m', ps.as_of_date) AS ym,
            ps.quantity,
            ps.book_cost,
            -- Swap: position_state.market_value is actually per-unit price
            ps.market_value AS market_price,
            -- Swap: position_state.market_price is actually total value
            ps.market_price AS market_value,
            COALESCE(ps.market_currency, ps.book_cost_currency, 'CAD'),
            ps.as_of_date,
            (SELECT sr.id FROM statement_registry sr
             WHERE sr.account_id = (SELECT account_id FROM accounts WHERE id = ps.account_id)
               AND sr.period_end = ps.as_of_date
             LIMIT 1)
        FROM position_state ps
        INNER JOIN (
            SELECT account_id, instrument_id,
                   STRFTIME('%Y-%m', as_of_date) AS ym,
                   MAX(as_of_date) AS max_date
            FROM position_state
            GROUP BY account_id, instrument_id, STRFTIME('%Y-%m', as_of_date)
        ) latest
            ON ps.account_id = latest.account_id
           AND ps.instrument_id = latest.instrument_id
           AND STRFTIME('%Y-%m', ps.as_of_date) = latest.ym
           AND ps.as_of_date = latest.max_date
        GROUP BY ps.account_id, ps.instrument_id, STRFTIME('%Y-%m', ps.as_of_date)
        """
    )
    count = cur.rowcount
    conn.commit()
    log.info("Materialized %d monthly balance snapshots", count)
    return count
