"""Load Gemini-generated override corrections and apply them to raw transactions."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from trade_history.extractors.base import RawTransaction
from trade_history.extractors.utils import parse_date_flexible, parse_quantity

log = logging.getLogger(__name__)

_OVERRIDES_DIR = Path("data/gemini_overrides")


def load_overrides(institution: str, statement_stem: str) -> list[dict]:
    """Load override JSON for a given institution/statement, if it exists."""
    override_file = _OVERRIDES_DIR / institution / f"{statement_stem}.json"
    if not override_file.exists():
        return []
    try:
        data = json.loads(override_file.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as exc:
        log.warning("Failed to load override %s: %s", override_file, exc)
        return []


def apply_overrides(
    transactions: list[RawTransaction],
    overrides: list[dict],
) -> list[RawTransaction]:
    """
    Replace transactions whose raw_text matches an override's original_raw_text.
    Non-matched overrides are appended (new rows Gemini recovered).
    """
    if not overrides:
        return transactions

    raw_text_index = {tx.raw_text: i for i, tx in enumerate(transactions)}
    result = list(transactions)
    appended = 0

    for override in overrides:
        original = override.get("original_raw_text", "")
        corrected = override.get("corrected", {})
        if not corrected:
            continue

        tx_date = parse_date_flexible(corrected.get("date", ""))
        if not tx_date:
            continue

        new_tx = RawTransaction(
            date=tx_date,
            activity=corrected.get("activity", "other"),
            description=corrected.get("notes", original),
            amount=corrected.get("amount", 0),
            currency=corrected.get("currency", "CAD"),
            raw_text=original,
            symbol=corrected.get("symbol"),
            quantity=parse_quantity(str(corrected["quantity"])) if corrected.get("quantity") else None,
            price=parse_quantity(str(corrected["price"])) if corrected.get("price") else None,
        )

        if original in raw_text_index:
            result[raw_text_index[original]] = new_tx
        else:
            result.append(new_tx)
            appended += 1

    if appended:
        log.info("Applied %d Gemini override corrections", appended)
    return result
