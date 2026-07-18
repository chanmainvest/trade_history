"""Infer ``initial_positions`` and ``initial_cash`` from snapshots + transactions.

For each (account, instrument), the earliest position_snapshot quantity minus
the sum of transaction quantities up to and including that snapshot date is
the implied carried-in quantity before our records start.

If that value is non-zero, we record it as an ``initial_positions`` row dated
one day before the earliest snapshot. Same logic for cash.

Idempotent: re-running deletes inferred rows (notes LIKE 'inferred:%') and
recomputes from scratch. User-curated rows (with a different ``notes``
prefix) are preserved. Legacy ``initial_cash`` rows created before the notes
column existed are treated as inferred output and replaced with tagged rows.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from ..db import sqlite as sqlite_db
from ..quantity import NON_CASH_TXN_TYPES, quantity_delta
from ..statement_selection import canonical_statement_clause

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
        canonical_snapshot = canonical_statement_clause("ps.statement_id")
        canonical_cash = canonical_statement_clause("cb.statement_id")
        canonical_transaction = canonical_statement_clause("t.statement_id")
        # Wipe previously-inferred rows so we recompute idempotently.
        conn.execute("DELETE FROM initial_positions WHERE notes LIKE 'inferred:%'")
        conn.execute("DELETE FROM initial_cash WHERE notes LIKE 'inferred:%' OR notes IS NULL")
        # Positions ------------------------------------------------------
        rows = conn.execute(
            f"""
            SELECT ps.account_id, i.instrument_key, MIN(ps.as_of_date) AS first_date
              FROM position_snapshots ps
              JOIN snapshot_sets ss ON ss.snapshot_set_id = ps.snapshot_set_id
              JOIN instruments i ON i.instrument_id = ps.instrument_id
             WHERE ss.section_type = 'positions' AND ss.can_clear_omitted = 1
               AND {canonical_snapshot}
             GROUP BY ps.account_id, i.instrument_key
            """
        ).fetchall()

        for r in rows:
            acct = r["account_id"]
            instrument_key = r["instrument_key"]
            first = r["first_date"]

            snap = conn.execute(
                "SELECT ps.instrument_id, ps.quantity, ps.currency "
                "  FROM position_snapshots ps "
                "  JOIN snapshot_sets ss ON ss.snapshot_set_id = ps.snapshot_set_id "
                "  JOIN instruments i ON i.instrument_id = ps.instrument_id "
                " WHERE ps.account_id = ? AND i.instrument_key = ? AND ps.as_of_date = ? "
                "   AND ss.section_type = 'positions' AND ss.can_clear_omitted = 1 "
                f"   AND {canonical_snapshot} "
                " LIMIT 1",
                (acct, instrument_key, first),
            ).fetchone()
            if not snap:
                continue
            snap_qty = float(snap["quantity"] or 0.0)
            ccy = snap["currency"]
            inst = snap["instrument_id"]

            txns = conn.execute(
                f"""
                SELECT t.txn_type, t.quantity, t.position_delta
                  FROM transactions t
                  JOIN instruments i ON i.instrument_id = t.instrument_id
                 WHERE t.account_id = ?
                   AND i.instrument_key = ?
                   AND t.trade_date <= ?
                   AND {canonical_transaction}
                """,
                (acct, instrument_key, first),
            ).fetchall()
            txn_qty = 0.0
            for t in txns:
                txn_qty += (
                    float(t["position_delta"])
                    if t["position_delta"] is not None
                    else quantity_delta(t["txn_type"], t["quantity"])
                )

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
        # Walk each (account, currency) first monthly cash snapshot. Annual and
        # interim statements can be recorded without a real holdings checkpoint.
        cash_rows = conn.execute(
            "SELECT cb.account_id, cb.currency, MIN(cb.as_of_date) AS first_date "
            "  FROM cash_balances cb "
            "  JOIN statements s ON s.statement_id = cb.statement_id "
            "  JOIN snapshot_sets ss ON ss.snapshot_set_id = cb.snapshot_set_id "
            " WHERE s.statement_type = 'monthly' "
            "   AND ss.section_type = 'cash' AND ss.can_clear_omitted = 1 "
            f"   AND {canonical_cash} "
            " GROUP BY cb.account_id, cb.currency"
        ).fetchall()
        for r in cash_rows:
            acct = r["account_id"]
            ccy = r["currency"]
            first = r["first_date"]
            first_bal = conn.execute(
                "SELECT cb.closing_balance AS balance "
            "  FROM cash_balances cb "
            "  JOIN statements s ON s.statement_id = cb.statement_id "
            "  JOIN snapshot_sets ss ON ss.snapshot_set_id = cb.snapshot_set_id "
            " WHERE cb.account_id = ? AND cb.currency = ? AND cb.as_of_date = ? "
            "   AND s.statement_type = 'monthly' "
            "   AND ss.section_type = 'cash' AND ss.can_clear_omitted = 1 "
                f"   AND {canonical_cash} "
                " LIMIT 1",
                (acct, ccy, first),
            ).fetchone()
            if not first_bal:
                continue
            bal = float(first_bal["balance"] or 0.0)
            net_rows = conn.execute(
                "SELECT COALESCE(SUM(COALESCE(cash_delta, net_amount)), 0) AS s "
                "  FROM transactions t "
                " WHERE t.account_id = ? AND t.currency = ? "
                "   AND COALESCE(t.cash_effective_date, t.trade_date) <= ? "
                "   AND COALESCE(t.cash_delta, t.net_amount) IS NOT NULL "
                f"   AND t.txn_type NOT IN ({','.join('?' * len(NON_CASH_TXN_TYPES))}) "
                f"   AND {canonical_transaction}",
                (acct, ccy, first, *sorted(NON_CASH_TXN_TYPES)),
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
