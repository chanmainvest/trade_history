"""Reviewed printed-symbol → canonical-symbol normalization rules.

Sibling of the reviewed ticker-change workflow. When a broker prints one
instrument under a different adjusted symbol (TD WebBroker prints OCC
adjusted option roots such as ``5SOXS`` or ``98TRI+$``), the reviewed rule
makes extraction resolve the printed symbol to the canonical instrument
before the instrument row is written (see
``sqlite_db.upsert_instrument``), so every statement period shares one
instrument. The printed symbol is preserved on record here and in the
statement raw lines. A normalization is recorded only from reviewed
evidence, never a name join.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass

from ..db import sqlite as sqlite_db
from .ticker_change_lookup import REVIEWED_SYMBOL_RE


def load_symbol_normalizations(path: str) -> list[dict]:
    """Load reviewed rules from a JSON ``symbol_normalizations`` list."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    entries = data.get("symbol_normalizations") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError(
            "normalization JSON must be an object with a "
            "'symbol_normalizations' list"
        )
    return entries


@dataclass(frozen=True)
class NormalizationRule:
    printed_symbol: str
    canonical_symbol: str
    asset_type: str
    currency: str
    option_expiry: str | None
    option_strike: float | None
    option_type: str | None
    effective_date: str | None
    resolution_method: str
    resolution_confidence: float
    notes: str | None
    evidence: str | None


def _prepare(entry: object, index: int) -> NormalizationRule:
    if not isinstance(entry, dict):
        raise ValueError(f"normalization #{index}: each entry must be an object")
    label = f"normalization #{index}"
    printed = str(entry.get("printed_symbol") or "").strip().upper()
    canonical = str(entry.get("canonical_symbol") or "").strip().upper()
    for role, symbol in (("printed_symbol", printed), ("canonical_symbol", canonical)):
        if not REVIEWED_SYMBOL_RE.fullmatch(symbol) or "_" in symbol:
            raise ValueError(
                f"{label}: {role} must be a short uppercase symbol without "
                f"underscores, got {entry.get(role)!r}"
            )
    if printed == canonical:
        raise ValueError(f"{label}: printed_symbol and canonical_symbol must differ")
    asset_type = str(entry.get("asset_type") or "").strip().lower()
    if not asset_type:
        raise ValueError(f"{label}: asset_type is required")
    currency = str(entry.get("currency") or "").strip().upper()
    if not currency:
        raise ValueError(f"{label}: currency is required")

    option_expiry: str | None = None
    option_strike: float | None = None
    option_type: str | None = None
    if asset_type == "option":
        # Brokers reuse adjusted option symbols across contract
        # generations; only expiry + strike + type identify one contract.
        expiry = str(entry.get("option_expiry") or "").strip()
        strike = entry.get("option_strike")
        kind = str(entry.get("option_type") or "").strip().upper()
        if (
            not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry)
            or not isinstance(strike, (int, float))
            or kind not in {"CALL", "PUT"}
        ):
            raise ValueError(
                f"{label}: option entries must carry option_expiry "
                "(YYYY-MM-DD), option_strike (number), and option_type "
                "(CALL|PUT)"
            )
        option_expiry, option_strike, option_type = expiry, float(strike), kind

    effective_date = str(entry.get("effective_date") or "").strip() or None
    if effective_date is not None and not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", effective_date
    ):
        raise ValueError(
            f"{label}: effective_date must be an ISO date (YYYY-MM-DD), "
            f"got {entry.get('effective_date')!r}"
        )
    method = str(entry.get("resolution_method") or "reviewed_statement_reprint")
    raw_confidence = entry.get("resolution_confidence")
    confidence = 0.95 if raw_confidence is None else float(raw_confidence)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(
            f"{label}: resolution_confidence must be within [0, 1]"
        )
    notes = str(entry["notes"]) if entry.get("notes") else None
    evidence = entry.get("evidence") or []
    evidence_text = (
        json.dumps([str(item) for item in evidence], ensure_ascii=False)
        if isinstance(evidence, list) and evidence
        else None
    )
    return NormalizationRule(
        printed_symbol=printed,
        canonical_symbol=canonical,
        asset_type=asset_type,
        currency=currency,
        option_expiry=option_expiry,
        option_strike=option_strike,
        option_type=option_type,
        effective_date=effective_date,
        resolution_method=method,
        resolution_confidence=confidence,
        notes=notes,
        evidence=evidence_text,
    )


def _find_instrument(
    conn: sqlite3.Connection, rule: NormalizationRule, symbol: str
) -> int | None:
    """Instrument matching the rule's identity under ``symbol``, if any.

    Tolerates zero matches: a fresh database may hold neither the printed
    nor the canonical instrument yet — the recorded rule still applies at
    extraction time.
    """
    row = conn.execute(
        """
        SELECT instrument_id FROM instruments
         WHERE UPPER(symbol) = ? AND asset_type = ? AND UPPER(currency) = ?
           AND COALESCE(option_expiry, '') = COALESCE(?, '')
           AND option_strike IS ?
           AND UPPER(COALESCE(option_type, '')) = UPPER(COALESCE(?, ''))
         LIMIT 1
        """,
        (
            symbol.upper(),
            rule.asset_type,
            rule.currency,
            rule.option_expiry,
            rule.option_strike,
            rule.option_type,
        ),
    ).fetchone()
    return int(row[0]) if row else None


def _record_rule(
    conn: sqlite3.Connection, rule: NormalizationRule
) -> tuple[int, bool]:
    """Insert or refresh the reviewed rule; returns (id, updated)."""
    existing = conn.execute(
        """
        SELECT normalization_id FROM instrument_symbol_normalizations
         WHERE printed_symbol = ? AND asset_type = ? AND currency = ?
           AND COALESCE(option_expiry, '') = COALESCE(?, '')
           AND option_strike IS ?
           AND COALESCE(option_type, '') = COALESCE(?, '')
        """,
        (
            rule.printed_symbol,
            rule.asset_type,
            rule.currency,
            rule.option_expiry,
            rule.option_strike,
            rule.option_type,
        ),
    ).fetchone()
    if existing is not None:
        conn.execute(
            """
            UPDATE instrument_symbol_normalizations
               SET canonical_symbol = ?,
                   effective_date = COALESCE(?, effective_date),
                   resolution_method = ?,
                   resolution_confidence = ?,
                   notes = ?,
                   evidence = ?
             WHERE normalization_id = ?
            """,
            (
                rule.canonical_symbol,
                rule.effective_date,
                rule.resolution_method,
                rule.resolution_confidence,
                rule.notes,
                rule.evidence,
                existing[0],
            ),
        )
        return int(existing[0]), True
    cursor = conn.execute(
        """
        INSERT INTO instrument_symbol_normalizations
            (printed_symbol, canonical_symbol, asset_type, currency,
             option_expiry, option_strike, option_type, effective_date,
             resolution_method, resolution_confidence, notes, evidence)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            rule.printed_symbol,
            rule.canonical_symbol,
            rule.asset_type,
            rule.currency,
            rule.option_expiry,
            rule.option_strike,
            rule.option_type,
            rule.effective_date,
            rule.resolution_method,
            rule.resolution_confidence,
            rule.notes,
            rule.evidence,
        ),
    )
    return int(cursor.lastrowid), False


def _set_rule_canonical(
    conn: sqlite3.Connection, rule_id: int, canonical_instrument_id: int
) -> None:
    conn.execute(
        "UPDATE instrument_symbol_normalizations SET canonical_instrument_id = ?"
        " WHERE normalization_id = ?",
        (canonical_instrument_id, rule_id),
    )


def _clone_canonical_instrument(
    conn: sqlite3.Connection, printed_id: int, rule: NormalizationRule
) -> int:
    """Create the canonical instrument from the printed instrument's row.

    Used when a reviewed rule lands before the canonical symbol has ever
    been printed. Ledger attributes (name, exchange, security identity)
    carry over unchanged; only the symbol and its option root change.
    """
    row = conn.execute(
        """
        SELECT i.asset_type, i.symbol, i.currency, i.exchange, i.name,
               i.option_expiry, i.option_strike, i.option_type,
               i.option_multiplier, i.security_id,
               s.security_key, s.canonical_name AS security_name,
               si.issuer_key, si.canonical_name AS issuer_name,
               s.journalable
          FROM instruments i
          LEFT JOIN securities s ON s.security_id = i.security_id
          LEFT JOIN security_issuers si ON si.issuer_id = s.issuer_id
         WHERE i.instrument_id = ?
        """,
        (printed_id,),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"{rule.printed_symbol}: printed instrument {printed_id} vanished"
        )
    return sqlite_db.upsert_instrument(
        conn,
        asset_type=row["asset_type"],
        symbol=rule.canonical_symbol,
        currency=row["currency"],
        exchange=row["exchange"],
        name=row["name"],
        option_root=rule.canonical_symbol if row["option_type"] else None,
        option_expiry=row["option_expiry"],
        option_strike=row["option_strike"],
        option_type=row["option_type"],
        option_multiplier=row["option_multiplier"] or 100,
        resolution_method="reviewed_symbol_normalization",
        resolution_confidence=rule.resolution_confidence,
        issuer_key=row["issuer_key"],
        issuer_name=row["issuer_name"],
        security_key=row["security_key"],
        security_name=row["security_name"],
        journalable=bool(row["journalable"]),
    )


def _remap_instrument(
    conn: sqlite3.Connection, printed_id: int, canonical_id: int
) -> dict[str, int]:
    """Point every ledger reference at the canonical instrument.

    Catalog rows that exist only to describe the printed form (market
    symbols, aliases, lineage) stay untouched: the instrument keeps
    serving as the printed-symbol lineage node.
    """
    counts: dict[str, int] = {}
    cursor = conn.execute(
        "UPDATE transactions SET instrument_id = ? WHERE instrument_id = ?",
        (canonical_id, printed_id),
    )
    counts["transactions"] = max(cursor.rowcount, 0)
    cursor = conn.execute(
        "UPDATE position_snapshots SET instrument_id = ? WHERE instrument_id = ?",
        (canonical_id, printed_id),
    )
    counts["position_snapshots"] = max(cursor.rowcount, 0)
    cursor = conn.execute(
        "UPDATE initial_positions SET instrument_id = ? WHERE instrument_id = ?",
        (canonical_id, printed_id),
    )
    counts["initial_positions"] = max(cursor.rowcount, 0)
    cursor = conn.execute(
        "UPDATE reconciliation_results SET instrument_id = ?"
        " WHERE instrument_id = ?",
        (canonical_id, printed_id),
    )
    counts["reconciliation_results"] = max(cursor.rowcount, 0)
    cursor = conn.execute(
        "UPDATE instrument_resolution_candidates SET resolved_instrument_id = ?"
        " WHERE resolved_instrument_id = ?",
        (canonical_id, printed_id),
    )
    counts["instrument_resolution_candidates"] = max(cursor.rowcount, 0)

    # Journal pairs across the merged pair are same-instrument no-ops and
    # are dropped; other partners follow the remap when the merged pair
    # does not already exist.
    cursor = conn.execute(
        "DELETE FROM instrument_journal_pairs"
        " WHERE (from_instrument_id = ? AND to_instrument_id = ?)"
        "    OR (from_instrument_id = ? AND to_instrument_id = ?)",
        (printed_id, canonical_id, canonical_id, printed_id),
    )
    counts["instrument_journal_pairs_deleted"] = max(cursor.rowcount, 0)
    cursor = conn.execute(
        """
        UPDATE instrument_journal_pairs
           SET from_instrument_id = ?
         WHERE from_instrument_id = ?
           AND to_instrument_id != ?
           AND NOT EXISTS (
               SELECT 1 FROM instrument_journal_pairs other
                WHERE other.from_instrument_id = ?
                  AND other.to_instrument_id = instrument_journal_pairs.to_instrument_id
                  AND COALESCE(other.effective_from, '') =
                      COALESCE(instrument_journal_pairs.effective_from, '')
           )
        """,
        (canonical_id, printed_id, canonical_id, canonical_id),
    )
    counts["instrument_journal_pairs_from"] = max(cursor.rowcount, 0)
    cursor = conn.execute(
        """
        UPDATE instrument_journal_pairs
           SET to_instrument_id = ?
         WHERE to_instrument_id = ?
           AND from_instrument_id != ?
           AND NOT EXISTS (
               SELECT 1 FROM instrument_journal_pairs other
                WHERE other.to_instrument_id = ?
                  AND other.from_instrument_id = instrument_journal_pairs.from_instrument_id
                  AND COALESCE(other.effective_from, '') =
                      COALESCE(instrument_journal_pairs.effective_from, '')
           )
        """,
        (canonical_id, printed_id, canonical_id, canonical_id),
    )
    counts["instrument_journal_pairs_to"] = max(cursor.rowcount, 0)
    return counts


def _rule_target(
    conn: sqlite3.Connection, rule_id: int
) -> int | None:
    row = conn.execute(
        "SELECT canonical_instrument_id FROM instrument_symbol_normalizations"
        " WHERE normalization_id = ?",
        (rule_id,),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def apply_symbol_normalizations(
    conn: sqlite3.Connection, entries: list[dict]
) -> dict:
    """Record reviewed normalization rules and merge existing ledger rows.

    Every entry is validated and resolved before anything is written; a
    rule that cannot be trusted must not half-apply. Applying is
    idempotent: the rule row is upserted, already-remapped ledger rows
    match zero times, and a re-apply whose merge finds nothing left to
    move counts as ``already_merged``.
    """
    rules = [_prepare(entry, index) for index, entry in enumerate(entries)]
    stats: dict[str, object] = {
        "rules": len(rules),
        "rules_inserted": 0,
        "rules_updated": 0,
        "instruments_merged": 0,
        "instruments_created": 0,
        "already_merged": 0,
        "pending_extraction": 0,
        "remapped": {},
    }
    remapped_total: dict[str, int] = {}
    for rule in rules:
        rule_id, updated = _record_rule(conn, rule)
        if updated:
            stats["rules_updated"] = int(stats["rules_updated"]) + 1
        else:
            stats["rules_inserted"] = int(stats["rules_inserted"]) + 1

        printed_id = _find_instrument(conn, rule, rule.printed_symbol)
        canonical_id = _find_instrument(conn, rule, rule.canonical_symbol)
        if printed_id is not None and canonical_id is None:
            canonical_id = _clone_canonical_instrument(conn, printed_id, rule)
            stats["instruments_created"] = int(stats["instruments_created"]) + 1
        if printed_id is None:
            # Nothing extracted under the printed symbol (any more); the
            # recorded rule keeps extraction from recreating it.
            if canonical_id is not None:
                _set_rule_canonical(conn, rule_id, canonical_id)
            stats["pending_extraction"] = int(stats["pending_extraction"]) + 1
            continue
        if canonical_id is None:
            raise ValueError(
                f"{rule.printed_symbol}: canonical instrument {rule.canonical_symbol} "
                f"({rule.asset_type}/{rule.currency}) not found and could not be "
                "created"
            )
        if printed_id == canonical_id:
            _set_rule_canonical(conn, rule_id, canonical_id)
            stats["already_merged"] = int(stats["already_merged"]) + 1
            continue
        prior_target = _rule_target(conn, rule_id)
        counts = _remap_instrument(conn, printed_id, canonical_id)
        _set_rule_canonical(conn, rule_id, canonical_id)
        moved = sum(counts.values())
        if prior_target == canonical_id and moved == 0:
            # The printed instrument row survives as a lineage node with no
            # ledger references left; nothing moved on this re-apply.
            stats["already_merged"] = int(stats["already_merged"]) + 1
        else:
            stats["instruments_merged"] = int(stats["instruments_merged"]) + 1
        for table, count in counts.items():
            remapped_total[table] = remapped_total.get(table, 0) + count
    stats["remapped"] = remapped_total
    return stats
