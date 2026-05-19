"""Match inter-account transfer pairs to avoid phantom P/L."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

_MATCH_WINDOW_DAYS = 5  # transfer_out and transfer_in within 5 days = same transfer


def match_transfer_pairs(conn: sqlite3.Connection) -> int:
    """
    Find unmatched transfer_in / transfer_out transactions and create transfer_pair records.
    Returns the number of pairs created.
    """
    # Find all transfer_out transactions not yet in transfer_pairs
    outs = conn.execute(
        """
        SELECT t.id, t.account_id, t.instrument_id, t.quantity, t.trade_date
        FROM transactions t
        WHERE t.activity = 'transfer_out'
          AND t.instrument_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM transfer_pairs p WHERE p.from_transaction_id = t.id
          )
        ORDER BY t.trade_date
        """
    ).fetchall()

    pairs_created = 0
    for out_row in outs:
        out_id, out_account, out_instrument, out_qty, out_date_str = (
            out_row["id"],
            out_row["account_id"],
            out_row["instrument_id"],
            out_row["quantity"],
            out_row["trade_date"],
        )
        out_date = date.fromisoformat(out_date_str)
        window_start = (out_date - timedelta(days=_MATCH_WINDOW_DAYS)).isoformat()
        window_end = (out_date + timedelta(days=_MATCH_WINDOW_DAYS)).isoformat()

        # Find matching transfer_in with same instrument, similar qty, different account
        match = conn.execute(
            """
            SELECT t.id, t.trade_date
            FROM transactions t
            WHERE t.activity = 'transfer_in'
              AND t.instrument_id = ?
              AND t.account_id != ?
              AND ABS(t.quantity - ?) < 0.001
              AND t.trade_date BETWEEN ? AND ?
              AND NOT EXISTS (
                  SELECT 1 FROM transfer_pairs p WHERE p.to_transaction_id = t.id
              )
            ORDER BY ABS(julianday(t.trade_date) - julianday(?))
            LIMIT 1
            """,
            (out_instrument, out_account, out_qty, window_start, window_end, out_date_str),
        ).fetchone()

        if match:
            conn.execute(
                """
                INSERT INTO transfer_pairs
                    (from_transaction_id, to_transaction_id, instrument_id, quantity, transfer_date)
                VALUES (?, ?, ?, ?, ?)
                """,
                (out_id, match["id"], out_instrument, out_qty, out_date_str),
            )
            pairs_created += 1

    conn.commit()
    return pairs_created
