"""Reviewed (no printed transaction) ticker-change records.

Mirrors the fund-lookup review workflow: a human or agent researches the
printed evidence, records the dated identity change in a committed JSON
file, and ``ingest apply-ticker-changes`` validates and persists it as
curated ``reviewed`` state that the position rollforward consumes.
"""
from __future__ import annotations

import json
import re
import sqlite3

from ..ticker_changes import record_reviewed_ticker_change

# Adjusted option symbols may carry the OCC "+$" marker ("98TRI+$").
REVIEWED_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.+$-]{1,11}$")


def load_ticker_change_entries(path: str) -> list[dict]:
    """Load reviewed entries from ``{"ticker_changes": [...]}`` JSON."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    entries = data.get("ticker_changes") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError(
            "ticker-change JSON must be an object with a 'ticker_changes' list"
        )
    return entries


def _resolve_instrument(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    asset_type: str,
    currency: str,
    role: str,
    option: dict[str, object] | None = None,
) -> int:
    if asset_type == "option":
        # Brokers reuse adjusted option symbols across contract generations;
        # only expiry + strike + type identify one contract.
        if not option:
            raise ValueError(
                f"{role} {symbol}: option entries must carry option_expiry, "
                "option_strike, and option_type"
            )
        rows = conn.execute(
            """
            SELECT instrument_id FROM instruments
             WHERE UPPER(symbol) = ? AND asset_type = ? AND UPPER(currency) = ?
               AND option_expiry = ? AND option_strike = ?
               AND UPPER(COALESCE(option_type, '')) = UPPER(?)
            """,
            (
                symbol,
                asset_type,
                currency,
                option["option_expiry"],
                option["option_strike"],
                str(option["option_type"]),
            ),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT instrument_id FROM instruments
             WHERE UPPER(symbol) = ? AND asset_type = ? AND UPPER(currency) = ?
            """,
            (symbol, asset_type, currency),
        ).fetchall()
    if len(rows) != 1:
        raise ValueError(
            f"{role} {symbol}: expected exactly one {asset_type}/{currency} "
            f"instrument, found {len(rows)}"
        )
    return int(rows[0]["instrument_id"])


def apply_reviewed_ticker_changes(
    conn: sqlite3.Connection,
    entries: list[dict],
) -> dict:
    """Record reviewed ticker changes from a JSON-derived entry list.

    This is the manual review path shared by humans and agents: both printed
    symbols, the effective date, and the ratio come from the reviewed
    record's own evidence (never inferred here).  Every entry is validated
    and resolved to existing instruments before anything is written.

    Raises ``ValueError`` on a malformed entry without writing any of it:
    a reviewed record that cannot be trusted must not half-apply.
    """
    prepared: list[tuple[int, int, str, float, str, float, str | None]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("each ticker-change entry must be an object")
        from_symbol = str(entry.get("from_symbol") or "").strip().upper()
        to_symbol = str(entry.get("to_symbol") or "").strip().upper()
        for role, symbol in (("from_symbol", from_symbol), ("to_symbol", to_symbol)):
            if not REVIEWED_SYMBOL_RE.fullmatch(symbol) or "_" in symbol:
                raise ValueError(
                    f"{role} must be a short uppercase symbol without "
                    f"underscores, got {entry.get(role)!r}"
                )
        if from_symbol == to_symbol:
            raise ValueError("from_symbol and to_symbol must differ")
        asset_type = str(entry.get("asset_type") or "").strip().lower()
        if not asset_type:
            raise ValueError(f"{from_symbol}: asset_type is required")
        currency = str(entry.get("currency") or "").strip().upper()
        if not currency:
            raise ValueError(f"{from_symbol}: currency is required")
        effective_date = str(entry.get("effective_date") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective_date):
            raise ValueError(
                f"{from_symbol}: effective_date must be an ISO date (YYYY-MM-DD), "
                f"got {entry.get('effective_date')!r}"
            )
        raw_ratio = entry.get("conversion_ratio")
        ratio = 1.0 if raw_ratio is None else float(raw_ratio)
        method = str(
            entry.get("resolution_method") or "reviewed_statement_reprint"
        )
        raw_confidence = entry.get("resolution_confidence")
        confidence = 0.95 if raw_confidence is None else float(raw_confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(
                f"{from_symbol}: resolution_confidence must be within [0, 1]"
            )

        option: dict[str, object] | None = None
        if asset_type == "option":
            expiry = str(entry.get("option_expiry") or "").strip()
            strike = entry.get("option_strike")
            kind = str(entry.get("option_type") or "").strip().upper()
            if (
                not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiry)
                or not isinstance(strike, (int, float))
                or kind not in {"CALL", "PUT"}
            ):
                raise ValueError(
                    f"{from_symbol}: option entries must carry option_expiry "
                    "(YYYY-MM-DD), option_strike (number), and option_type "
                    "(CALL|PUT)"
                )
            option = {
                "option_expiry": expiry,
                "option_strike": float(strike),
                "option_type": kind,
            }

        from_id = _resolve_instrument(
            conn, symbol=from_symbol, asset_type=asset_type, currency=currency,
            role="from_symbol", option=option,
        )
        to_id = _resolve_instrument(
            conn, symbol=to_symbol, asset_type=asset_type, currency=currency,
            role="to_symbol", option=option,
        )
        notes_parts = [entry["notes"]] if entry.get("notes") else []
        evidence = entry.get("evidence") or []
        if isinstance(evidence, list) and evidence:
            notes_parts.append("evidence: " + "; ".join(str(item) for item in evidence))
        notes = " | ".join(notes_parts) if notes_parts else None
        prepared.append(
            (from_id, to_id, effective_date, ratio, method, confidence, notes)
        )

    stats = {"entries": len(entries), "inserted": 0, "updated": 0}
    for from_id, to_id, effective_date, ratio, method, confidence, notes in prepared:
        _change_id, updated = record_reviewed_ticker_change(
            conn,
            from_instrument_id=from_id,
            to_instrument_id=to_id,
            effective_date=effective_date,
            conversion_ratio=ratio,
            resolution_method=method,
            resolution_confidence=confidence,
            notes=notes,
        )
        if updated:
            stats["updated"] += 1
        else:
            stats["inserted"] += 1
    return stats
