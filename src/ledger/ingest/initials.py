"""Infer ``initial_positions`` and ``initial_cash`` from snapshots + transactions.

For each (account, instrument), the earliest position_snapshot quantity minus
the sum of transaction quantities up to and including that snapshot date is
the implied carried-in quantity before our records start.

If that value is non-zero, we record it as an ``initial_positions`` row dated
one day before the earliest snapshot. Same logic for cash.

Idempotent: re-running deletes inferred rows (notes LIKE 'inferred:%') and
recomputes from scratch. User-curated rows (with a different ``notes``
prefix) are preserved.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from ..db import sqlite as sqlite_db
from ..quantity import quantity_delta

log = logging.getLogger(__name__)


def _day_before(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    return (date(y, m, d) - timedelta(days=1)).isoformat()


def infer_initials(path: Path | str | None = None) -> dict:
    """Populate initial_positions and initial_cash.

    Returns a small summary dict for logging.
    """
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    n_positions = 0
    n_cash = 0
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        # Wipe previously-inferred rows so we recompute idempotently.
        conn.execute("DELETE FROM initial_positions WHERE notes LIKE 'inferred:%'")
        conn.execute("DELETE FROM initial_cash WHERE notes LIKE 'inferred:%'")
        # Positions ------------------------------------------------------
        rows = conn.execute(
            "SELECT account_id, instrument_id, MIN(as_of_date) AS first_date "
            "  FROM position_snapshots "
            " GROUP BY account_id, instrument_id"
        ).fetchall()

        for r in rows:
            acct = r["account_id"]
            inst = r["instrument_id"]
            first = r["first_date"]

            snap = conn.execute(
                "SELECT quantity, currency "
                "  FROM position_snapshots "
                " WHERE account_id = ? AND instrument_id = ? AND as_of_date = ? "
                " LIMIT 1",
                (acct, inst, first),
            ).fetchone()
            if not snap:
                continue
            snap_qty = float(snap["quantity"] or 0.0)
            ccy = snap["currency"]

            txns = conn.execute(
                "SELECT txn_type, COALESCE(quantity, 0) AS quantity "
                "  FROM transactions "
                " WHERE account_id = ? AND instrument_id = ? AND trade_date <= ?",
                (acct, inst, first),
            ).fetchall()
            txn_qty = 0.0
            for t in txns:
                txn_qty += quantity_delta(t["txn_type"], t["quantity"])

            implied_initial = snap_qty - txn_qty
            if abs(implied_initial) < 1e-9:
                continue

            cur = conn.execute(
                "INSERT INTO initial_positions "
                "  (account_id, as_of_date, instrument_id, quantity, "
                "   avg_cost, currency, notes) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?) "
                "ON CONFLICT(account_id, as_of_date, instrument_id) DO NOTHING",
                (
                    acct,
                    _day_before(first),
                    inst,
                    implied_initial,
                    ccy,
                    f"inferred: snapshot {snap_qty} - txns {txn_qty}",
                ),
            )
            if cur.rowcount > 0:
                n_positions += 1

        # Cash -----------------------------------------------------------
        # Walk each (account, currency) cash series.
        cash_rows = conn.execute(
            "SELECT account_id, currency, MIN(as_of_date) AS first_date "
            "  FROM cash_balances "
            " GROUP BY account_id, currency"
        ).fetchall()
        for r in cash_rows:
            acct = r["account_id"]
            ccy = r["currency"]
            first = r["first_date"]
            first_bal = conn.execute(
                "SELECT closing_balance AS balance FROM cash_balances "
                " WHERE account_id = ? AND currency = ? AND as_of_date = ? "
                " LIMIT 1",
                (acct, ccy, first),
            ).fetchone()
            if not first_bal:
                continue
            bal = float(first_bal["balance"] or 0.0)
            net_rows = conn.execute(
                "SELECT COALESCE(SUM(net_amount), 0) AS s "
                "  FROM transactions "
                " WHERE account_id = ? AND currency = ? AND trade_date <= ?",
                (acct, ccy, first),
            ).fetchone()
            net = float(net_rows["s"] or 0.0) if net_rows else 0.0
            implied = bal - net
            if abs(implied) < 1e-6:
                continue
            cur = conn.execute(
                "INSERT INTO initial_cash "
                "  (account_id, as_of_date, currency, balance, notes) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(account_id, as_of_date, currency) DO NOTHING",
                (
                    acct,
                    _day_before(first),
                    ccy,
                    implied,
                    f"inferred: closing {bal} - net txns {net}",
                ),
            )
            if cur.rowcount > 0:
                n_cash += 1

        conn.commit()
    log.info("infer_initials: positions=%d cash=%d", n_positions, n_cash)
    return {"positions": n_positions, "cash": n_cash}
