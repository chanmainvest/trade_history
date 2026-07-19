"""CLI-only repair/status pass for listing and market-symbol identities."""
from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

from ..db import sqlite as sqlite_db
from ..instrument_catalog import ListingIdentity, listing_for_symbol, listing_for_text


def _target_instrument(conn: sqlite3.Connection, listing: ListingIdentity) -> int:
    return sqlite_db.upsert_instrument(
        conn,
        asset_type=listing.asset_type,
        symbol=listing.symbol,
        currency=listing.currency,
        exchange=listing.exchange,
        name=listing.security_name,
        resolution_method="catalog_name",
        resolution_confidence=1.0,
        issuer_key=listing.issuer_key,
        issuer_name=listing.issuer_name,
        security_key=listing.security_key,
        security_name=listing.security_name,
        journalable=listing.journalable,
        market_symbol=listing.yahoo_symbol,
    )


def _listing_for_row(row: sqlite3.Row) -> ListingIdentity | None:
    direct = listing_for_symbol(str(row["symbol"]), str(row["currency"]))
    if direct is not None:
        return direct
    for value in (row["symbol"], row["name"], row["description"]):
        listing = listing_for_text(
            value,
            str(row["currency"]),
            institution_code=str(row["institution_code"]),
        )
        if listing is not None:
            return listing
    return None


def sync_catalog_identities(path: Path | str | None = None) -> dict[str, int]:
    """Repair derived references only when one catalog listing is exact.

    Conflicting checkpoint/initial rows are left for a clean re-ingest rather
    than merged here. Reported numeric fields and evidence never change.
    """
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    sqlite_db.init_db(db_path)
    metrics: Counter[str] = Counter()
    with sqlite_db.session(db_path) as conn:
        transactions = conn.execute(
            """
            SELECT t.transaction_id, t.instrument_id, t.description,
                   t.currency, i.symbol, i.name, institution.code AS institution_code
              FROM transactions t
              JOIN instruments i ON i.instrument_id = t.instrument_id
              JOIN accounts account ON account.account_id = t.account_id
              JOIN institutions institution
                ON institution.institution_id = account.institution_id
            """
        ).fetchall()
        for row in transactions:
            listing = _listing_for_row(row)
            if listing is None:
                continue
            target_id = _target_instrument(conn, listing)
            if target_id != int(row["instrument_id"]):
                conn.execute(
                    """
                    UPDATE transactions
                       SET instrument_id = ?, resolution_method = 'catalog_name',
                           resolution_confidence = 1.0
                     WHERE transaction_id = ?
                    """,
                    (target_id, row["transaction_id"]),
                )
                metrics["transactions_repointed"] += 1
            else:
                metrics["listings_enriched"] += 1

        for table, id_column in (
            ("position_snapshots", "snapshot_id"),
            ("initial_positions", "initial_id"),
        ):
            rows = conn.execute(
                f"""
                SELECT row.{id_column}, row.instrument_id, row.currency,
                       i.symbol, i.name, NULL AS description,
                       institution.code AS institution_code
                  FROM {table} row
                  JOIN instruments i ON i.instrument_id = row.instrument_id
                  JOIN accounts account ON account.account_id = row.account_id
                  JOIN institutions institution
                    ON institution.institution_id = account.institution_id
                """
            ).fetchall()
            for row in rows:
                listing = _listing_for_row(row)
                if listing is None:
                    continue
                target_id = _target_instrument(conn, listing)
                if target_id == int(row["instrument_id"]):
                    metrics["listings_enriched"] += 1
                    continue
                try:
                    conn.execute(
                        f"UPDATE {table} SET instrument_id = ? WHERE {id_column} = ?",
                        (target_id, row[id_column]),
                    )
                except sqlite3.IntegrityError:
                    metrics["checkpoint_conflicts"] += 1
                    continue
                metrics[f"{table}_repointed"] += 1

        pending = conn.execute(
            """
            SELECT candidate.candidate_id, candidate.display_text,
                   candidate.asset_type, candidate.currency,
                   institution.code AS institution_code
              FROM instrument_resolution_candidates candidate
              JOIN institutions institution
                ON institution.institution_id = candidate.institution_id
             WHERE candidate.status = 'pending'
            """
        ).fetchall()
        for row in pending:
            listing = listing_for_text(
                row["display_text"],
                row["currency"],
                institution_code=row["institution_code"],
            )
            if listing is None:
                continue
            target_id = _target_instrument(conn, listing)
            conn.execute(
                """
                UPDATE instrument_resolution_candidates
                   SET status = 'resolved', resolved_instrument_id = ?,
                       resolution_method = 'catalog_name',
                       resolution_confidence = 1.0,
                       last_seen_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
                 WHERE candidate_id = ?
                """,
                (target_id, row["candidate_id"]),
            )
            metrics["candidates_resolved"] += 1

        for row in conn.execute(
            """
            SELECT status, COUNT(*) AS count
              FROM instrument_resolution_candidates GROUP BY status
            """
        ).fetchall():
            metrics[f"candidate_{row['status']}"] = int(row["count"])
        for row in conn.execute(
            """
            SELECT status, COUNT(*) AS count
              FROM instrument_market_symbols
             WHERE provider = 'yahoo' GROUP BY status
            """
        ).fetchall():
            metrics[f"yahoo_{row['status']}"] = int(row["count"])
    return dict(sorted(metrics.items()))
