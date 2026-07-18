"""Dated ticker-change identity and lineage helpers.

Ticker changes preserve two instrument identities.  They are deliberately not
stored in ``instrument_aliases`` because an old symbol remains historically
correct before the effective date.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from .parsers.types import ParsedInstrument, ParseResult

_SYMBOL = r"[A-Z][A-Z0-9.\-]{0,9}"
_EXPLICIT_CHANGE = re.compile(
    rf"\b(?:NAME|SYMBOL|TICKER)\s+CHANGE(?:D)?\b"
    rf"[^A-Z0-9.\-]*(?:FROM\s+)?\.?({_SYMBOL})\s+(?:TO|INTO)\s+\.?({_SYMBOL})\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TickerSegment:
    instrument_id: int
    instrument_key: str
    symbol: str
    valid_from: str | None
    valid_to: str | None


def explicit_ticker_change_symbols(text: str) -> tuple[str, str] | None:
    """Return only an explicitly printed ``old -> new`` ticker pair."""
    match = _EXPLICIT_CHANGE.search(text)
    if match is None:
        return None
    old, new = (value.upper().lstrip(".") for value in match.groups())
    if old == new or old in {"FROM", "TO"} or new in {"FROM", "TO"}:
        return None
    return old, new


def enrich_ticker_change_transactions(result: ParseResult) -> int:
    """Attach explicit old/new identities to parsed name-change rows.

    The function never infers a symbol from a company name.  If a broker row
    does not print both tickers in an unambiguous phrase, it remains unresolved.
    """
    enriched = 0
    for statement in result.statements:
        for transaction in statement.transactions:
            if transaction.txn_type != "name_change":
                continue
            pair = explicit_ticker_change_symbols(
                " ".join(
                    part
                    for part in (transaction.description, transaction.raw_line)
                    if part
                )
            )
            if pair is None:
                continue
            old, new = pair
            asset_type = (
                transaction.instrument.asset_type
                if transaction.instrument is not None
                else "equity"
            )
            transaction.instrument = ParsedInstrument(
                asset_type=asset_type,
                symbol=old,
                currency=transaction.currency,
                resolution_method="printed_ticker_change",
                resolution_confidence=1.0,
                resolution_evidence=transaction.source_span,
            )
            transaction.related_instrument = ParsedInstrument(
                asset_type=asset_type,
                symbol=new,
                currency=transaction.currency,
                resolution_method="printed_ticker_change",
                resolution_confidence=1.0,
                resolution_evidence=transaction.source_span,
            )
            transaction.corporate_action_ratio = 1.0
            transaction.resolution_method = "printed_ticker_change"
            transaction.resolution_confidence = 1.0
            transaction.resolution_evidence = transaction.source_span
            enriched += 1
    return enriched


def record_ticker_change(
    conn: sqlite3.Connection,
    *,
    from_instrument_id: int,
    to_instrument_id: int,
    effective_date: str,
    conversion_ratio: float,
    transaction_id: int,
    evidence_id: int | None,
    resolution_method: str,
    resolution_confidence: float,
) -> int:
    """Persist one source-backed ticker change after structural validation."""
    instruments = conn.execute(
        """
        SELECT instrument_id, asset_type, currency
          FROM instruments
         WHERE instrument_id IN (?, ?)
        """,
        (from_instrument_id, to_instrument_id),
    ).fetchall()
    if len(instruments) != 2 or from_instrument_id == to_instrument_id:
        raise sqlite3.IntegrityError("ticker change requires two distinct instruments")
    if len({(row["asset_type"], row["currency"]) for row in instruments}) != 1:
        raise sqlite3.IntegrityError(
            "ticker change instruments must have the same asset type and currency"
        )
    if conversion_ratio <= 0:
        raise sqlite3.IntegrityError("ticker change ratio must be positive")

    # One instrument cannot branch to two successors or have two predecessors.
    conflict = conn.execute(
        """
        SELECT 1 FROM instrument_ticker_changes
         WHERE (from_instrument_id = ? AND
                (to_instrument_id <> ? OR effective_date <> ?))
            OR (to_instrument_id = ? AND
                (from_instrument_id <> ? OR effective_date <> ?))
         LIMIT 1
        """,
        (
            from_instrument_id,
            to_instrument_id,
            effective_date,
            to_instrument_id,
            from_instrument_id,
            effective_date,
        ),
    ).fetchone()
    if conflict is not None:
        raise sqlite3.IntegrityError("ticker change would create a branching lineage")

    change_id = int(
        conn.execute(
            """
            INSERT INTO instrument_ticker_changes(
                from_instrument_id, to_instrument_id, effective_date,
                conversion_ratio, status, resolution_method,
                resolution_confidence
            ) VALUES (?, ?, ?, ?, 'extracted', ?, ?)
            ON CONFLICT(from_instrument_id, to_instrument_id, effective_date)
            DO UPDATE SET
                conversion_ratio = excluded.conversion_ratio,
                resolution_confidence = MAX(
                    instrument_ticker_changes.resolution_confidence,
                    excluded.resolution_confidence
                )
            RETURNING ticker_change_id
            """,
            (
                from_instrument_id,
                to_instrument_id,
                effective_date,
                conversion_ratio,
                resolution_method,
                resolution_confidence,
            ),
        ).fetchone()[0]
    )
    # Reject cycles, including a later attempt to add C -> A to A -> B -> C.
    cycle = conn.execute(
        """
        WITH RECURSIVE successors(instrument_id) AS (
            SELECT to_instrument_id FROM instrument_ticker_changes
             WHERE from_instrument_id = ?
            UNION
            SELECT tc.to_instrument_id
              FROM instrument_ticker_changes tc
              JOIN successors s ON tc.from_instrument_id = s.instrument_id
        )
        SELECT 1 FROM successors WHERE instrument_id = ? LIMIT 1
        """,
        (to_instrument_id, from_instrument_id),
    ).fetchone()
    if cycle is not None:
        raise sqlite3.IntegrityError("ticker change would create a lineage cycle")
    conn.execute(
        """
        INSERT INTO instrument_ticker_change_sources(
            ticker_change_id, transaction_id, evidence_id
        ) VALUES (?, ?, ?)
        ON CONFLICT(transaction_id) DO UPDATE SET
            ticker_change_id = excluded.ticker_change_id,
            evidence_id = excluded.evidence_id
        """,
        (change_id, transaction_id, evidence_id),
    )
    return change_id


def ticker_segments(conn: sqlite3.Connection, symbol: str) -> list[TickerSegment]:
    """Return the single non-branching ticker lineage containing ``symbol``."""
    roots = conn.execute(
        """
        SELECT instrument_id FROM instruments
         WHERE UPPER(symbol) = UPPER(?) AND asset_type IN ('equity','etf')
         ORDER BY instrument_id
        """,
        (symbol,),
    ).fetchall()
    if not roots:
        return []
    seed_ids = [int(row[0]) for row in roots]
    placeholders = ",".join("?" * len(seed_ids))
    rows = conn.execute(
        f"""
        WITH RECURSIVE lineage(instrument_id) AS (
            SELECT instrument_id FROM instruments WHERE instrument_id IN ({placeholders})
            UNION
            SELECT tc.from_instrument_id FROM instrument_ticker_changes tc
              JOIN lineage l ON tc.to_instrument_id = l.instrument_id
            UNION
            SELECT tc.to_instrument_id FROM instrument_ticker_changes tc
              JOIN lineage l ON tc.from_instrument_id = l.instrument_id
        )
        SELECT DISTINCT i.instrument_id, i.instrument_key, i.symbol
          FROM lineage l JOIN instruments i ON i.instrument_id = l.instrument_id
        """,
        seed_ids,
    ).fetchall()
    ids = {int(row["instrument_id"]) for row in rows}
    edges = conn.execute(
        """
        SELECT from_instrument_id, to_instrument_id, effective_date
          FROM instrument_ticker_changes ORDER BY effective_date, ticker_change_id
        """
    ).fetchall()
    relevant = [
        edge for edge in edges
        if int(edge["from_instrument_id"]) in ids and int(edge["to_instrument_id"]) in ids
    ]
    by_id = {int(row["instrument_id"]): row for row in rows}
    successor = {int(edge["from_instrument_id"]): edge for edge in relevant}
    predecessor = {int(edge["to_instrument_id"]): edge for edge in relevant}
    root_candidates = sorted(ids - set(predecessor))
    if len(root_candidates) != 1:
        # Multiple same-symbol instruments are ambiguous unless relationships
        # establish one lineage. Do not join unrelated securities by ticker.
        return [
            TickerSegment(int(row["instrument_id"]), row["instrument_key"], row["symbol"], None, None)
            for row in rows if str(row["symbol"]).upper() == symbol.upper()
        ]
    output: list[TickerSegment] = []
    current = root_candidates[0]
    valid_from: str | None = None
    while current in by_id:
        edge = successor.get(current)
        valid_to = str(edge["effective_date"]) if edge is not None else None
        item = by_id[current]
        output.append(
            TickerSegment(current, item["instrument_key"], item["symbol"], valid_from, valid_to)
        )
        if edge is None:
            break
        current = int(edge["to_instrument_id"])
        valid_from = str(edge["effective_date"])
    return output
