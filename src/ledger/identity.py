"""Deterministic logical identities shared by parsing and persistence."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

INSTRUMENT_KEY_VERSION = "ik1"
STATEMENT_KEY_VERSION = "sk1"
EVIDENCE_KEY_VERSION = "ev1"


def _token(value: object | None, *, compact: bool = False) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value)).strip()
    text = re.sub(r"\s+", "" if compact else " ", text).upper()
    return quote(text, safe="._-")


def _decimal_token(value: float | int | str | None) -> str:
    if value is None or value == "":
        return ""
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return _token(value)
    if not number.is_finite():
        return _token(value)
    if number == 0:
        return "0"
    return format(number.normalize(), "f")


def canonical_instrument_key(
    *,
    asset_type: str,
    symbol: str,
    currency: str,
    option_root: str | None = None,
    option_expiry: str | None = None,
    option_strike: float | None = None,
    option_type: str | None = None,
    option_multiplier: int = 100,
) -> str:
    """Return the canonical security identity used by every ledger table.

    Ordinary instruments are keyed by type, normalized printed/resolved symbol,
    and native currency. Option identity additionally includes the contract
    terms, so nullable SQL comparison semantics never participate in identity.
    """
    kind = _token(asset_type)
    native_currency = _token(currency)
    if kind == "OPTION":
        root = _token(option_root or symbol, compact=True)
        return "|".join(
            (
                INSTRUMENT_KEY_VERSION,
                "OPTION",
                root,
                native_currency,
                _token(option_expiry),
                _decimal_token(option_strike),
                _token(option_type),
                str(option_multiplier),
            )
        )
    return "|".join(
        (
            INSTRUMENT_KEY_VERSION,
            kind,
            _token(symbol, compact=True),
            native_currency,
        )
    )


def _digest_key(version: str, parts: dict[str, object | None]) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{version}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def canonical_statement_key(
    *,
    source_identity: str,
    institution_code: str,
    account_number: str,
    period_start: str,
    period_end: str,
    statement_type: str,
) -> str:
    """Return a rebuild-stable identity for one physical account/period."""
    return _digest_key(
        STATEMENT_KEY_VERSION,
        {
            "source": source_identity.strip(),
            "institution": _token(institution_code),
            "account": _token(account_number),
            "period_start": period_start,
            "period_end": period_end,
            "statement_type": _token(statement_type),
        },
    )


def canonical_evidence_key(
    *,
    source_identity: str,
    row_kind: str,
    occurrence: int,
    raw_text: str | None,
    page_number: int | None = None,
    line_number: int | None = None,
    parser_rule: str | None = None,
) -> str:
    """Return a deterministic, non-content-revealing source-evidence key."""
    return _digest_key(
        EVIDENCE_KEY_VERSION,
        {
            "source": source_identity.strip(),
            "kind": _token(row_kind),
            "occurrence": occurrence,
            "page": page_number,
            "line": line_number,
            "raw": unicodedata.normalize("NFKC", raw_text or ""),
            "rule": parser_rule or "",
        },
    )


def evidence_occurrence(statement_key: str, row_kind: str, row_index: int) -> int:
    """Return a stable positive SQLite INTEGER occurrence for a parsed row."""
    payload = f"{statement_key}\0{row_kind}\0{row_index}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & ((1 << 63) - 1)
