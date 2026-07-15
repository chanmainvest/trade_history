"""Shared layout/provenance helpers for stateful statement parsers.

The parsers still receive a plain-text view for their broker-specific state
machines, but PDF extraction now retains a coordinate-bearing line model. This
module is the single bridge from a claimed text row to a ``SourceSpan`` so
parser code never invents page/line/box information.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from ..pdf_text import PdfLine, PdfText
from .types import (
    ParsedQuarantine,
    ParsedSnapshotSet,
    ParsedStatement,
    ParseResult,
    SourceSpan,
)

_SPACE = re.compile(r"\s+")


def normalize_layout_text(value: str) -> str:
    """Normalize extraction artifacts for matching, never for stored evidence."""
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = (
        normalized.replace("\u00a0", " ")
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
    )
    return _SPACE.sub(" ", normalized).strip()


class SourceLocator:
    """Resolve parser raw text to one monotonic page/line occurrence."""

    def __init__(self, pdf: PdfText):
        self._lines = pdf.layout_lines
        self._cursor: dict[str, int] = {}

    def _matching_line(self, raw_text: str) -> PdfLine | None:
        candidates = [
            normalize_layout_text(part)
            for part in raw_text.splitlines()
            if normalize_layout_text(part)
        ]
        if not candidates:
            return None
        key = candidates[0]
        start = self._cursor.get(key, 0)
        for index in range(start, len(self._lines)):
            if normalize_layout_text(self._lines[index].text) == key:
                self._cursor[key] = index + 1
                return self._lines[index]
        # Parser rows sometimes join a harmless continuation. Preserve the
        # first defensible line rather than assigning a fuzzy coordinate.
        for index in range(start, len(self._lines)):
            line_text = normalize_layout_text(self._lines[index].text)
            if line_text and (line_text in key or key in line_text):
                self._cursor[key] = index + 1
                return self._lines[index]
        return None

    def span_for(self, raw_text: str | None, *, parser_rule: str) -> SourceSpan | None:
        if not raw_text:
            return None
        line = self._matching_line(raw_text)
        if line is None:
            return SourceSpan(raw_text=raw_text, parser_rule=parser_rule)
        return SourceSpan(
            raw_text=raw_text,
            page_number=line.page_number,
            line_number=line.line_number,
            bbox=line.bbox,
            words=line.word_dicts,
            parser_rule=parser_rule,
        )


def attach_source_spans(pdf: PdfText, result: ParseResult, *, parser_name: str) -> None:
    """Attach non-fabricated source spans to parsed and quarantined rows."""
    locator = SourceLocator(pdf)
    for statement in result.statements:
        for transaction in statement.transactions:
            if transaction.source_span is None:
                transaction.source_span = locator.span_for(
                    transaction.raw_line,
                    parser_rule=f"{parser_name}:transaction",
                )
        for position in statement.positions:
            if position.source_span is None:
                position.source_span = locator.span_for(
                    position.raw_line,
                    parser_rule=f"{parser_name}:position",
                )
        for cash in statement.cash_balances:
            if cash.source_span is None:
                cash.source_span = locator.span_for(
                    cash.raw_line,
                    parser_rule=f"{parser_name}:cash",
                )

        enriched_quarantine: list[tuple[str, str] | ParsedQuarantine] = []
        for item in statement.quarantine:
            if isinstance(item, ParsedQuarantine):
                if item.source_span is None:
                    item.source_span = locator.span_for(
                        item.raw_line,
                        parser_rule=f"{parser_name}:quarantine",
                    )
                enriched_quarantine.append(item)
            else:
                raw_line, reason = item
                enriched_quarantine.append(
                    ParsedQuarantine(
                        raw_line=raw_line,
                        reason=reason,
                        source_span=locator.span_for(
                            raw_line,
                            parser_rule=f"{parser_name}:quarantine",
                        ),
                    )
                )
        statement.quarantine = enriched_quarantine


def _incomplete_option_reason(instrument) -> str | None:
    if instrument is None or instrument.asset_type != "option":
        return None
    missing = [
        name
        for name, value in (
            ("option_root", instrument.option_root),
            ("option_expiry", instrument.option_expiry),
            ("option_strike", instrument.option_strike),
            ("option_type", instrument.option_type),
            ("option_multiplier", instrument.option_multiplier),
        )
        if value is None or value == ""
    ]
    if not missing:
        return None
    return f"option identity is incomplete: {', '.join(missing)}"


def _downgrade_complete_scope(
    statement: ParsedStatement,
    *,
    currency: str,
    section_type: str,
) -> None:
    for scope in statement.snapshot_sets:
        if (
            scope.currency == currency
            and scope.section_type == section_type
            and scope.completeness == "complete"
        ):
            scope.completeness = "unknown"
            scope.validation_status = "warning"


def quarantine_unsupported_rows(result: ParseResult) -> None:
    """Quarantine rows the current model cannot represent without guessing.

    Statement activity can include pending rows dated outside its declared
    period, and an extraction can identify an option while losing part of its
    contract. Until those variants have an explicit representation, retaining
    them as ordinary rows would make the parse invalid or fabricate identity.
    Move only rows with their printed raw evidence into quarantine instead.
    """
    for statement in result.statements:
        try:
            period_start = date.fromisoformat(statement.period_start)
            period_end = date.fromisoformat(statement.period_end)
        except ValueError:
            # The validator remains responsible for malformed statement dates.
            continue

        transactions = []
        for transaction in statement.transactions:
            reasons: list[str] = []
            try:
                trade_date = date.fromisoformat(transaction.trade_date)
            except ValueError:
                trade_date = None
            if trade_date and not period_start <= trade_date <= period_end:
                reasons.append(
                    "transaction date is outside the statement period; "
                    "pending-row model unavailable"
                )
            option_reason = _incomplete_option_reason(transaction.instrument)
            if option_reason:
                reasons.append(option_reason)
            if reasons and transaction.raw_line.strip():
                statement.quarantine.append(ParsedQuarantine(
                    raw_line=transaction.raw_line,
                    reason="; ".join(reasons),
                ))
                continue
            transactions.append(transaction)
        statement.transactions = transactions

        positions = []
        for position in statement.positions:
            option_reason = _incomplete_option_reason(position.instrument)
            if option_reason and (position.raw_line or "").strip():
                statement.quarantine.append(ParsedQuarantine(
                    raw_line=position.raw_line or "",
                    reason=option_reason,
                ))
                _downgrade_complete_scope(
                    statement,
                    currency=position.currency,
                    section_type="positions",
                )
                continue
            positions.append(position)
        statement.positions = positions


def declare_snapshot_scopes(
    statement: ParsedStatement,
    *,
    position_scopes: dict[str, str] | None = None,
    cash_scopes: dict[str, str] | None = None,
) -> None:
    """Declare each observed/known snapshot scope exactly once.

    Callers pass ``complete`` only when their state machine has observed the
    relevant full broker section. Otherwise this helper conservatively emits an
    ``unknown`` scope, which cannot clear an omitted holding downstream.
    """
    position_scopes = position_scopes or {}
    cash_scopes = cash_scopes or {}
    for position in statement.positions:
        position_scopes.setdefault(position.currency, "unknown")
    for cash in statement.cash_balances:
        cash_scopes.setdefault(cash.currency, "unknown")

    existing = {
        (scope.currency, scope.section_type, scope.scope_key)
        for scope in statement.snapshot_sets
    }
    for currency, completeness in sorted(position_scopes.items()):
        key = (currency, "positions", "default")
        if key not in existing:
            statement.snapshot_sets.append(
                ParsedSnapshotSet(
                    currency=currency,
                    section_type="positions",
                    completeness=completeness,  # type: ignore[arg-type]
                    validation_status="valid" if completeness == "complete" else "warning",
                )
            )
            existing.add(key)
    for currency, completeness in sorted(cash_scopes.items()):
        key = (currency, "cash", "default")
        if key not in existing:
            statement.snapshot_sets.append(
                ParsedSnapshotSet(
                    currency=currency,
                    section_type="cash",
                    completeness=completeness,  # type: ignore[arg-type]
                    validation_status="valid" if completeness == "complete" else "warning",
                )
            )
            existing.add(key)
