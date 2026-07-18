"""Lookup/cache helpers for statement fund names that lack fund codes."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from ..db import sqlite as sqlite_db
from ..parsers.name_resolver import strip_leading_verbs

CREATE_LOOKUP_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS instrument_identifier_lookups (
    lookup_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    identifier_type    TEXT NOT NULL DEFAULT 'fund_code',
    asset_type         TEXT NOT NULL DEFAULT 'mutual_fund',
    institution_code   TEXT NOT NULL DEFAULT '',
    normalized_name    TEXT NOT NULL,
    display_name       TEXT NOT NULL,
    currency           TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','resolved','not_found','ambiguous','ignored')),
    resolved_symbol    TEXT,
    resolved_exchange  TEXT,
    resolved_name      TEXT,
    evidence_url       TEXT,
    sample_description TEXT,
    first_seen_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    last_seen_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    notes              TEXT,
    UNIQUE(identifier_type, asset_type, institution_code, normalized_name, currency)
);
"""

CREATE_LOOKUP_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_identifier_lookups_status
    ON instrument_identifier_lookups(status, identifier_type, asset_type);
"""


@dataclass(frozen=True)
class FundCodeMatch:
    lookup_id: int
    symbol: str
    asset_type: str
    exchange: str | None
    name: str | None


def ensure_lookup_table(conn: sqlite3.Connection) -> None:
    """Create the lookup cache table for existing databases."""
    conn.execute(CREATE_LOOKUP_TABLE_SQL)
    conn.execute(CREATE_LOOKUP_INDEX_SQL)


def normalize_fund_name(text: str | None) -> str | None:
    """Return a stable fund-name key while preserving the class when printed."""
    if not text:
        return None
    cleaned = strip_leading_verbs(text).upper()
    cleaned = cleaned.replace("&", " AND ")
    cleaned = re.sub(r"\bCL\s+([A-Z])\b", r"CLASS \1", cleaned)
    cleaned = re.sub(r"[^A-Z0-9 ]+", " ", cleaned)
    cleaned = re.sub(r"\b\d[\d,]*(?:\.\d+)?\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    tokens = cleaned.split()
    name_tokens = None
    class_code = None
    for start_idx, token in enumerate(tokens):
        if token != "CIBC":
            continue
        for fund_idx in range(start_idx + 1, min(len(tokens), start_idx + 7)):
            if tokens[fund_idx] != "FUND":
                continue
            candidate = tokens[start_idx:fund_idx + 1]
            if any(noise in candidate for noise in {"ACCOUNT", "INVESTOR", "PAGE", "REINVESTED"}):
                continue
            name_tokens = candidate
            tail = tokens[fund_idx + 1:fund_idx + 5]
            for idx, tail_token in enumerate(tail):
                if tail_token == "CLASS" and idx + 1 < len(tail) and re.fullmatch(r"[A-Z]", tail[idx + 1]):
                    class_code = tail[idx + 1]
                    break
            break
        if name_tokens is not None:
            break
    if name_tokens is None:
        return None
    if name_tokens == ["RBB", "FUND"]:
        return None
    if class_code:
        name_tokens.extend(["CLASS", class_code])
    return " ".join(name_tokens)


def lookup_fund_code(
    conn: sqlite3.Connection,
    *,
    fund_name: str,
    currency: str,
    institution_code: str | None = None,
    sample_description: str | None = None,
) -> FundCodeMatch | None:
    """Look up a reviewed fund code, or queue the name for initial lookup.

    The function never invents a code. If no resolved row exists, it records a
    pending lookup request and returns ``None``.
    """
    ensure_lookup_table(conn)
    normalized = normalize_fund_name(sample_description or fund_name)
    if not normalized:
        return None

    institution = institution_code or ""
    currency = currency or "CAD"
    row = conn.execute(
        "SELECT lookup_id, asset_type, resolved_symbol, resolved_exchange, resolved_name, status "
        "  FROM instrument_identifier_lookups "
        " WHERE identifier_type = 'fund_code' "
        "   AND asset_type = 'mutual_fund' "
        "   AND normalized_name = ? "
        "   AND currency = ? "
        "   AND institution_code IN (?, '') "
        " ORDER BY CASE WHEN institution_code = ? THEN 0 ELSE 1 END "
        " LIMIT 1",
        (normalized, currency, institution, institution),
    ).fetchone()
    if row and row["status"] == "resolved" and row["resolved_symbol"]:
        return FundCodeMatch(
            lookup_id=int(row["lookup_id"]),
            symbol=str(row["resolved_symbol"]).upper(),
            asset_type=row["asset_type"] or "mutual_fund",
            exchange=row["resolved_exchange"],
            name=row["resolved_name"] or normalized.title(),
        )

    conn.execute(
        "INSERT INTO instrument_identifier_lookups "
        "  (identifier_type, asset_type, institution_code, normalized_name, display_name, "
        "   currency, status, sample_description) "
        "VALUES ('fund_code', 'mutual_fund', ?, ?, ?, ?, 'pending', ?) "
        "ON CONFLICT(identifier_type, asset_type, institution_code, normalized_name, currency) "
        "DO UPDATE SET "
        "  last_seen_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'), "
        "  sample_description = COALESCE(excluded.sample_description, sample_description)",
        (institution, normalized, normalized.title(), currency, sample_description or fund_name),
    )
    return None


def lookup_fund_instrument_id(
    conn: sqlite3.Connection,
    *,
    fund_name: str,
    currency: str,
    institution_code: str | None = None,
    sample_description: str | None = None,
) -> int | None:
    """Return an instrument id for a resolved fund-code lookup, if available."""
    match = lookup_fund_code(
        conn,
        fund_name=fund_name,
        currency=currency,
        institution_code=institution_code,
        sample_description=sample_description,
    )
    if match is None:
        return None
    return sqlite_db.upsert_instrument(
        conn,
        asset_type=match.asset_type,
        symbol=match.symbol,
        currency=currency,
        exchange=match.exchange,
        name=match.name or fund_name[:120],
    )


def lookup_status_summary(conn: sqlite3.Connection) -> dict[str, int]:
    ensure_lookup_table(conn)
    rows = conn.execute(
        "SELECT status, COUNT(*) AS count "
        "  FROM instrument_identifier_lookups "
        " WHERE identifier_type = 'fund_code' "
        " GROUP BY status "
        " ORDER BY status"
    ).fetchall()
    return {str(row["status"]): int(row["count"]) for row in rows}
