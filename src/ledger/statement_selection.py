"""Canonical statement selection for derived ledger calculations.

Every physical PDF remains available for verification. When more than one
active source describes the same account, period, and statement type, derived
views use the most recently persisted statement as the canonical revision so
movements are never counted twice.
"""
from __future__ import annotations

import re

_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$")


def canonical_statement_clause(column: str) -> str:
    """Return a safe SQL predicate for a nullable statement-id column."""
    if not _COLUMN.fullmatch(column):
        raise ValueError(f"unsafe SQL column: {column!r}")
    return f"""
        ({column} IS NULL OR {column} IN (
            SELECT MAX(canonical.statement_id)
              FROM statements canonical
             GROUP BY canonical.account_id, canonical.period_start,
                      canonical.period_end, canonical.statement_type
        ))
    """
