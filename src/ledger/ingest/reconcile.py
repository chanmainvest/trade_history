"""Transfer pairing and position-to-transaction reconciliation."""
from __future__ import annotations

import re
import sqlite3
from datetime import date
from pathlib import Path

from ..db import sqlite as sqlite_db
from ..quantity import quantity_delta

TRANSFER_TYPES = {"transfer_in", "transfer_out", "journal"}
AUTO_TRANSFER_NOTE_RE = re.compile(r"auto: matched transfer transactions (\d+) <-> (\d+)")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _clear_auto_transfer_links(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT notes FROM account_links WHERE notes LIKE 'auto: matched transfer transactions %'"
    ).fetchall()
    transaction_ids: set[int] = set()
    for row in rows:
        match = AUTO_TRANSFER_NOTE_RE.search(row["notes"] or "")
        if match:
            transaction_ids.update({int(match.group(1)), int(match.group(2))})

    if transaction_ids:
        placeholders = ",".join("?" * len(transaction_ids))
        conn.execute(
            f"""
            UPDATE transactions
               SET counterpart_account_id = NULL,
                   counterpart_txn_id = NULL
             WHERE transaction_id IN ({placeholders})
                OR counterpart_txn_id IN ({placeholders})
            """,
            [*transaction_ids, *transaction_ids],
        )
    deleted = conn.execute(
        "DELETE FROM account_links WHERE notes LIKE 'auto: matched transfer transactions %'"
    ).rowcount
    return int(deleted or 0)


def _security_delta(row: sqlite3.Row) -> float:
    if row["instrument_id"] is None:
        return 0.0
    if "position_delta" in row.keys() and row["position_delta"] is not None:
        return float(row["position_delta"])
    if row["quantity"] is None:
        return 0.0
    return quantity_delta(row["txn_type"], row["quantity"])


def _cash_delta(row: sqlite3.Row) -> float:
    if row["instrument_id"] is not None:
        return 0.0
    amount_value = (
        row["cash_delta"]
        if "cash_delta" in row.keys() and row["cash_delta"] is not None
        else row["net_amount"]
    )
    if amount_value is None:
        return 0.0
    amount = float(amount_value)
    if row["txn_type"] == "transfer_in":
        return abs(amount)
    if row["txn_type"] == "transfer_out":
        return -abs(amount)
    return amount


def _transfer_key(row: sqlite3.Row) -> tuple[str, str, str, float] | None:
    security_delta = _security_delta(row)
    if abs(security_delta) > 1e-9 and row["instrument_key"] is not None:
        return (
            "instrument",
            str(row["instrument_key"]),
            row["currency"] or "",
            round(abs(security_delta), 8),
        )
    cash_delta = _cash_delta(row)
    if abs(cash_delta) > 0.005:
        return ("cash", row["currency"] or "", row["currency"] or "", round(abs(cash_delta), 2))
    return None


def _transfer_delta(row: sqlite3.Row) -> float:
    security_delta = _security_delta(row)
    if abs(security_delta) > 1e-9:
        return security_delta
    return _cash_delta(row)


def link_transfers(
    path: Path | str | None = None,
    *,
    date_window_days: int = 7,
) -> dict:
    """Pair unambiguous transfer rows and populate counterpart fields."""
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        cleared = _clear_auto_transfer_links(conn)
        rows = conn.execute(
            """
            SELECT t.transaction_id, t.account_id, t.trade_date, t.txn_type,
                   t.instrument_id, i.instrument_key, t.quantity, t.position_delta,
                   t.net_amount, t.cash_delta, t.currency, t.description
              FROM transactions t
              LEFT JOIN instruments i ON i.instrument_id = t.instrument_id
             WHERE txn_type IN ('transfer_in', 'transfer_out', 'journal')
               AND counterpart_txn_id IS NULL
             ORDER BY trade_date, transaction_id
            """
        ).fetchall()

        incoming: dict[tuple[str, str, str, float], list[sqlite3.Row]] = {}
        outgoing: list[sqlite3.Row] = []
        skipped_missing_key = 0
        for row in rows:
            key = _transfer_key(row)
            delta = _transfer_delta(row)
            if key is None or abs(delta) <= 1e-9:
                skipped_missing_key += 1
                continue
            if delta > 0:
                incoming.setdefault(key, []).append(row)
            else:
                outgoing.append(row)

        matched_incoming_ids: set[int] = set()
        matched = 0
        ambiguous = 0
        for out_row in outgoing:
            out_date = _parse_date(out_row["trade_date"])
            key = _transfer_key(out_row)
            if out_date is None or key is None:
                skipped_missing_key += 1
                continue
            candidates: list[tuple[int, sqlite3.Row]] = []
            for in_row in incoming.get(key, []):
                in_id = int(in_row["transaction_id"])
                if in_id in matched_incoming_ids or in_row["account_id"] == out_row["account_id"]:
                    continue
                in_date = _parse_date(in_row["trade_date"])
                if in_date is None:
                    continue
                days_apart = abs((in_date - out_date).days)
                if days_apart <= date_window_days:
                    candidates.append((days_apart, in_row))
            if not candidates:
                continue
            candidates.sort(key=lambda item: (item[0], item[1]["trade_date"], item[1]["transaction_id"]))
            best_distance = candidates[0][0]
            best = [candidate for distance, candidate in candidates if distance == best_distance]
            if len(best) != 1:
                ambiguous += 1
                continue
            in_row = best[0]
            out_id = int(out_row["transaction_id"])
            in_id = int(in_row["transaction_id"])
            conn.execute(
                """
                UPDATE transactions
                   SET counterpart_account_id = ?, counterpart_txn_id = ?
                 WHERE transaction_id = ?
                """,
                (in_row["account_id"], in_id, out_id),
            )
            conn.execute(
                """
                UPDATE transactions
                   SET counterpart_account_id = ?, counterpart_txn_id = ?
                 WHERE transaction_id = ?
                """,
                (out_row["account_id"], out_id, in_id),
            )
            conn.execute(
                """
                INSERT INTO account_links(from_account_id, to_account_id, transfer_date, notes)
                VALUES (?, ?, ?, ?)
                """,
                (
                    out_row["account_id"],
                    in_row["account_id"],
                    min(out_row["trade_date"], in_row["trade_date"]),
                    f"auto: matched transfer transactions {out_id} <-> {in_id}",
                ),
            )
            matched_incoming_ids.add(in_id)
            matched += 1

    return {
        "matched": matched,
        "cleared": cleared,
        "ambiguous": ambiguous,
        "skipped_missing_key": skipped_missing_key,
    }


def rebuild_position_transaction_links(path: Path | str | None = None) -> dict:
    """Rebuild monthly snapshot movement attribution from transaction rows."""
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        conn.execute("DELETE FROM position_transaction_links")
        snapshots = conn.execute(
            """
            SELECT ps.snapshot_id, ps.account_id, ps.instrument_id, ps.as_of_date,
                   ps.statement_id, ps.currency, i.instrument_key
              FROM position_snapshots ps
              JOIN instruments i ON i.instrument_id = ps.instrument_id
             ORDER BY ps.account_id, i.instrument_key, ps.currency,
                      ps.as_of_date, ps.statement_id, ps.snapshot_id
            """
        ).fetchall()
        previous_snapshot_date: dict[tuple[int, str, str], str] = {}
        linked = 0
        for snapshot in snapshots:
            key = (
                int(snapshot["account_id"]),
                str(snapshot["instrument_key"]),
                str(snapshot["currency"]),
            )
            params: list = [snapshot["account_id"], snapshot["instrument_key"], snapshot["as_of_date"]]
            previous_date = previous_snapshot_date.get(key)
            previous_clause = ""
            if previous_date is not None:
                previous_clause = "AND trade_date > ?"
                params.append(previous_date)
            transactions = conn.execute(
                f"""
                SELECT t.transaction_id, t.instrument_id, t.txn_type, t.quantity,
                       t.position_delta
                  FROM transactions t
                  JOIN instruments i ON i.instrument_id = t.instrument_id
                 WHERE t.account_id = ?
                   AND i.instrument_key = ?
                   AND t.trade_date <= ?
                   {previous_clause}
                 ORDER BY trade_date, transaction_id
                """,
                params,
            ).fetchall()
            for transaction in transactions:
                attributed = _security_delta(transaction)
                if abs(attributed) <= 1e-9:
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO position_transaction_links(
                        snapshot_id, transaction_id, quantity_attributed
                    ) VALUES (?, ?, ?)
                    """,
                    (snapshot["snapshot_id"], transaction["transaction_id"], attributed),
                )
                linked += 1
            previous_snapshot_date[key] = snapshot["as_of_date"]
    return {"links": linked, "snapshots": len(snapshots)}


def reconcile_after_ingest(path: Path | str | None = None) -> dict:
    """Run all automatic reconciliation passes."""
    return {
        "transfers": link_transfers(path),
        "positions": rebuild_position_transaction_links(path),
    }
