"""Shared runtime domains for values persisted in the private ledger."""
from __future__ import annotations

from datetime import UTC, date, datetime

SUPPORTED_LEDGER_CURRENCIES = frozenset({"CAD", "USD"})


def validate_ledger_currency(value: str) -> str:
    """Return a supported ISO currency or raise a stable domain error."""
    if value not in SUPPORTED_LEDGER_CURRENCIES:
        supported = ", ".join(sorted(SUPPORTED_LEDGER_CURRENCIES))
        raise ValueError(f"unsupported ledger currency {value!r}; expected one of {supported}")
    return value


def validate_iso_date(value: str) -> str:
    """Return one canonical calendar date string or raise ``ValueError``."""
    parsed = date.fromisoformat(value)
    canonical = parsed.isoformat()
    if canonical != value:
        raise ValueError(f"date is not canonical ISO YYYY-MM-DD: {value!r}")
    return canonical


def utc_now_text() -> str:
    """Return the sole timestamp representation used by SQLite writes."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
