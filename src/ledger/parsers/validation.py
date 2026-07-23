"""Runtime validation for parser output.

The parser dataclasses are intentionally lightweight and Python's Literal
annotations are not enforced at runtime. This module provides the validation
boundary used by ingestion and the read-only extraction audit.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Literal, get_args

from ..domains import SUPPORTED_LEDGER_CURRENCIES
from ..identity import canonical_instrument_key
from ..quantity import POSITION_AFFECTING_TYPES
from .types import (
    ParsedInstrument,
    ParsedQuarantine,
    ParsedStatement,
    ParseResult,
    SourceSpan,
    TxnType,
)

Severity = Literal["error", "warning"]

VALID_TXN_TYPES = frozenset(get_args(TxnType))
VALID_ASSET_TYPES = frozenset(
    {"equity", "etf", "option", "bond", "mutual_fund", "cash", "other"}
)
VALID_STATEMENT_TYPES = frozenset({"monthly", "quarterly", "annual", "interim"})


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: Severity = "error"
    statement_index: int | None = None
    row_kind: str | None = None
    row_index: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def statement_key(statement: ParsedStatement) -> tuple[str, str, str, str]:
    """Return the current logical identity emitted by a parser."""
    return (
        statement.account.account_number.strip(),
        statement.period_start,
        statement.period_end,
        statement.statement_type,
    )


def instrument_key(instrument: ParsedInstrument) -> str:
    """Return the canonical logical instrument key used by SQLite."""
    return canonical_instrument_key(
        asset_type=instrument.asset_type,
        symbol=instrument.symbol,
        currency=instrument.currency,
        option_root=instrument.option_root,
        option_expiry=instrument.option_expiry,
        option_strike=instrument.option_strike,
        option_type=instrument.option_type,
        option_multiplier=instrument.option_multiplier,
    )


def _issue(
    report: ValidationReport,
    code: str,
    message: str,
    *,
    severity: Severity = "error",
    statement_index: int | None = None,
    row_kind: str | None = None,
    row_index: int | None = None,
) -> None:
    report.issues.append(
        ValidationIssue(
            code=code,
            message=message,
            severity=severity,
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
    )


def _iso_date(
    report: ValidationReport,
    value: str,
    *,
    code: str,
    statement_index: int,
    row_kind: str | None = None,
    row_index: int | None = None,
) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        _issue(
            report,
            code,
            f"invalid ISO date: {value!r}",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
        return None


def _currency(
    report: ValidationReport,
    value: str,
    *,
    statement_index: int,
    row_kind: str,
    row_index: int,
) -> None:
    if value not in SUPPORTED_LEDGER_CURRENCIES:
        _issue(
            report,
            "invalid_currency",
            f"currency must be one of {sorted(SUPPORTED_LEDGER_CURRENCIES)}, got {value!r}",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )


def _finite(
    report: ValidationReport,
    value: float | int | None,
    *,
    field_name: str,
    statement_index: int,
    row_kind: str,
    row_index: int,
    required: bool = False,
) -> None:
    if value is None:
        if required:
            _issue(
                report,
                "missing_numeric",
                f"{field_name} is required",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _issue(
            report,
            "invalid_numeric",
            f"{field_name} must be a finite number or null, got {value!r}",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )


def _source_span(
    report: ValidationReport,
    span: SourceSpan | None,
    *,
    statement_index: int,
    row_kind: str,
    row_index: int,
) -> None:
    if span is None:
        return
    for field_name in ("page_number", "line_number"):
        value = getattr(span, field_name)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 1
        ):
            _issue(
                report,
                "invalid_source_span",
                f"{field_name} must be a positive integer or null, got {value!r}",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
    if span.bbox is not None and (
        len(span.bbox) != 4
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in span.bbox
        )
    ):
        _issue(
            report,
            "invalid_source_bbox",
            "source bounding box must contain four finite numbers",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )


def _instrument(
    report: ValidationReport,
    instrument: ParsedInstrument,
    *,
    statement_index: int,
    row_kind: str,
    row_index: int,
    row_currency: str,
) -> None:
    if instrument.asset_type not in VALID_ASSET_TYPES:
        _issue(
            report,
            "invalid_asset_type",
            f"unsupported asset type: {instrument.asset_type!r}",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
    if not instrument.symbol.strip():
        _issue(
            report,
            "missing_symbol",
            "instrument symbol is empty",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
    _currency(
        report,
        instrument.currency,
        statement_index=statement_index,
        row_kind=row_kind,
        row_index=row_index,
    )
    if instrument.currency != row_currency:
        _issue(
            report,
            "instrument_currency_mismatch",
            f"instrument currency {instrument.currency!r} != row currency {row_currency!r}",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
    if instrument.asset_type == "option":
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
        if missing:
            _issue(
                report,
                "incomplete_option_identity",
                f"option is missing: {', '.join(missing)}",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        if instrument.option_type not in {"CALL", "PUT"}:
            _issue(
                report,
                "invalid_option_type",
                f"option_type must be CALL or PUT, got {instrument.option_type!r}",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        if instrument.option_expiry:
            _iso_date(
                report,
                instrument.option_expiry,
                code="invalid_option_expiry",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        _finite(
            report,
            instrument.option_strike,
            field_name="option_strike",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
            required=True,
        )
        if (
            not isinstance(instrument.option_multiplier, int)
            or isinstance(instrument.option_multiplier, bool)
            or instrument.option_multiplier <= 0
        ):
            _issue(
                report,
                "invalid_option_multiplier",
                f"option_multiplier must be a positive integer, got {instrument.option_multiplier!r}",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )


def _validate_statement(
    report: ValidationReport,
    statement: ParsedStatement,
    statement_index: int,
    *,
    page_count: int | None = None,
    require_pages: bool = False,
) -> None:
    pages = statement.page_numbers
    if require_pages and not pages:
        _issue(
            report,
            "missing_statement_pages",
            "statement has no explicit physical page membership",
            statement_index=statement_index,
        )
    if pages:
        invalid_pages = [
            page for page in pages
            if not isinstance(page, int) or isinstance(page, bool) or page < 1
        ]
        if invalid_pages:
            _issue(
                report,
                "invalid_statement_pages",
                f"statement pages must be positive integers, got {invalid_pages!r}",
                statement_index=statement_index,
            )
        if tuple(sorted(set(pages))) != tuple(pages):
            _issue(
                report,
                "invalid_statement_pages",
                "statement pages must be unique and ascending",
                statement_index=statement_index,
            )
        if page_count is not None and any(page > page_count for page in pages):
            _issue(
                report,
                "statement_page_out_of_range",
                f"statement page exceeds source page count {page_count}",
                statement_index=statement_index,
            )
        if statement.page_assignment_method not in {
            "parser_explicit",
            "single_statement_source",
        }:
            _issue(
                report,
                "missing_statement_page_method",
                "statement page membership has no supported assignment method",
                statement_index=statement_index,
            )
    if not statement.account.account_number.strip():
        _issue(
            report,
            "missing_account_number",
            "statement account number is empty",
            statement_index=statement_index,
        )
    if statement.statement_type not in VALID_STATEMENT_TYPES:
        _issue(
            report,
            "invalid_statement_type",
            f"unsupported statement type: {statement.statement_type!r}",
            statement_index=statement_index,
        )
    period_start = _iso_date(
        report,
        statement.period_start,
        code="invalid_period_start",
        statement_index=statement_index,
    )
    period_end = _iso_date(
        report,
        statement.period_end,
        code="invalid_period_end",
        statement_index=statement_index,
    )
    if period_start and period_end and period_start > period_end:
        _issue(
            report,
            "reversed_period",
            "statement period_start is after period_end",
            statement_index=statement_index,
        )

    for row_index, transaction in enumerate(statement.transactions):
        row_kind = "transaction"
        trade_date = _iso_date(
            report,
            transaction.trade_date,
            code="invalid_transaction_date",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
        if (
            trade_date
            and period_start
            and period_end
            and not (period_start <= trade_date <= period_end)
        ):
            _issue(
                report,
                "transaction_date_outside_period",
                (
                    f"transaction date {transaction.trade_date} is outside "
                    f"{statement.period_start}..{statement.period_end}"
                ),
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        if transaction.settle_date:
            _iso_date(
                report,
                transaction.settle_date,
                code="invalid_settlement_date",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        if transaction.cash_effective_date:
            _iso_date(
                report,
                transaction.cash_effective_date,
                code="invalid_cash_effective_date",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        if transaction.txn_type not in VALID_TXN_TYPES:
            _issue(
                report,
                "invalid_transaction_type",
                f"unsupported transaction type: {transaction.txn_type!r}",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        _currency(
            report,
            transaction.currency,
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
        if (
            transaction.txn_type in POSITION_AFFECTING_TYPES
            and transaction.quantity is not None
            and transaction.instrument is None
            and transaction.resolution_method != "unresolved_printed_identity"
        ):
            _issue(
                report,
                "position_movement_without_instrument",
                "position-affecting transaction has quantity but no instrument",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        if transaction.instrument is not None:
            _instrument(
                report,
                transaction.instrument,
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
                row_currency=transaction.currency,
            )
        if transaction.related_instrument is not None:
            _instrument(
                report,
                transaction.related_instrument,
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
                row_currency=transaction.currency,
            )
            if transaction.txn_type != "name_change":
                _issue(
                    report,
                    "unexpected_related_instrument",
                    "related_instrument is supported only for a ticker/name change",
                    statement_index=statement_index,
                    row_kind=row_kind,
                    row_index=row_index,
                )
            if transaction.instrument is None:
                _issue(
                    report,
                    "ticker_change_without_old_instrument",
                    "ticker change has a new instrument but no old instrument",
                    statement_index=statement_index,
                    row_kind=row_kind,
                    row_index=row_index,
                )
            elif (
                transaction.instrument.asset_type
                != transaction.related_instrument.asset_type
                or transaction.instrument.currency
                != transaction.related_instrument.currency
                or transaction.instrument.symbol
                == transaction.related_instrument.symbol
            ):
                _issue(
                    report,
                    "invalid_ticker_change_pair",
                    "ticker change requires different symbols with the same asset type and currency",
                    statement_index=statement_index,
                    row_kind=row_kind,
                    row_index=row_index,
                )
        if transaction.txn_type == "name_change" and transaction.related_instrument is not None:
            ratio = transaction.corporate_action_ratio
            if ratio is None or not isinstance(ratio, (int, float)) or ratio <= 0:
                _issue(
                    report,
                    "invalid_ticker_change_ratio",
                    "ticker change requires a positive corporate_action_ratio",
                    statement_index=statement_index,
                    row_kind=row_kind,
                    row_index=row_index,
                )
        for field_name in (
            "quantity", "price", "gross_amount", "commission", "other_fees",
            "net_amount", "tax_rate", "parser_confidence", "position_delta",
            "cash_delta", "resolution_confidence", "corporate_action_ratio",
        ):
            _finite(
                report,
                getattr(transaction, field_name),
                field_name=field_name,
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        if (
            isinstance(transaction.resolution_confidence, (int, float))
            and not isinstance(transaction.resolution_confidence, bool)
            and math.isfinite(transaction.resolution_confidence)
            and not 0 <= transaction.resolution_confidence <= 1
        ):
            _issue(
                report,
                "invalid_resolution_confidence",
                "resolution_confidence must be between zero and one",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        if not transaction.raw_line.strip():
            _issue(
                report,
                "missing_raw_line",
                "transaction has no source raw line",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        _source_span(
            report,
            transaction.source_span,
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
        _source_span(
            report,
            transaction.resolution_evidence,
            statement_index=statement_index,
            row_kind="transaction_resolution",
            row_index=row_index,
        )

    for row_index, position in enumerate(statement.positions):
        row_kind = "position"
        _currency(
            report,
            position.currency,
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
        _instrument(
            report,
            position.instrument,
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
            row_currency=position.currency,
        )
        for field_name in (
            "quantity", "avg_cost", "book_value", "market_price",
            "market_value", "unrealized_pnl",
        ):
            _finite(
                report,
                getattr(position, field_name),
                field_name=field_name,
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
                required=field_name == "quantity",
            )
        if not (position.raw_line or "").strip():
            _issue(
                report,
                "missing_position_raw_line",
                "position has no source raw line",
                severity="warning",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        _source_span(
            report,
            position.source_span,
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )

    cash_currencies: set[str] = set()
    for row_index, cash in enumerate(statement.cash_balances):
        row_kind = "cash"
        _currency(
            report,
            cash.currency,
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
        if cash.currency in cash_currencies:
            _issue(
                report,
                "duplicate_cash_currency",
                f"statement emits more than one {cash.currency} cash balance",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        cash_currencies.add(cash.currency)
        _finite(
            report,
            cash.opening_balance,
            field_name="opening_balance",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
        _finite(
            report,
            cash.closing_balance,
            field_name="closing_balance",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
            required=True,
        )
        if not (cash.raw_line or "").strip() and not (
            cash.source_span and (cash.source_span.raw_text or "").strip()
        ):
            _issue(
                report,
                "cash_source_evidence_unavailable",
                "cash balance has no source raw text",
                severity="warning",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        _source_span(
            report,
            cash.source_span,
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )

    declared_scopes: set[tuple[str, str, str]] = set()
    for row_index, snapshot_set in enumerate(statement.snapshot_sets):
        row_kind = "snapshot_set"
        _currency(
            report,
            snapshot_set.currency,
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
        key = (
            snapshot_set.currency,
            snapshot_set.section_type,
            snapshot_set.scope_key,
        )
        if key in declared_scopes:
            _issue(
                report,
                "duplicate_snapshot_scope",
                f"duplicate snapshot scope {key!r}",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        declared_scopes.add(key)
        if snapshot_set.section_type not in {"positions", "cash", "summary"}:
            _issue(
                report,
                "invalid_snapshot_section_type",
                f"unsupported snapshot section type: {snapshot_set.section_type!r}",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        if not isinstance(snapshot_set.scope_key, str) or not snapshot_set.scope_key.strip():
            _issue(
                report,
                "invalid_snapshot_scope_key",
                "snapshot scope_key must be non-empty text",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        if snapshot_set.completeness not in {"complete", "partial", "absent", "unknown"}:
            _issue(
                report,
                "invalid_snapshot_completeness",
                f"unsupported snapshot completeness: {snapshot_set.completeness!r}",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        if snapshot_set.validation_status not in {
            "unvalidated",
            "valid",
            "warning",
            "invalid",
        }:
            _issue(
                report,
                "invalid_snapshot_validation_status",
                f"unsupported snapshot validation status: {snapshot_set.validation_status!r}",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        _finite(
            report,
            snapshot_set.opening_total,
            field_name="opening_total",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
        _finite(
            report,
            snapshot_set.reported_change,
            field_name="reported_change",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
        _finite(
            report,
            snapshot_set.reported_total,
            field_name="reported_total",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
        _source_span(
            report,
            snapshot_set.source_span,
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )
        blocking_issues = [
            issue for issue in snapshot_set.issues if issue.blocks_completeness
        ]
        if snapshot_set.completeness == "complete" and blocking_issues:
            _issue(
                report,
                "complete_scope_has_blocking_issue",
                "complete snapshot scope cannot have a blocking extraction issue",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        if snapshot_set.completeness in {"partial", "unknown"} and not blocking_issues:
            _issue(
                report,
                "incomplete_scope_without_issue",
                "partial/unknown snapshot scope requires a blocking extraction issue",
                statement_index=statement_index,
                row_kind=row_kind,
                row_index=row_index,
            )
        for issue_index, issue in enumerate(snapshot_set.issues):
            if not issue.issue_code.strip():
                _issue(
                    report,
                    "empty_scope_issue_code",
                    "snapshot scope issue code is empty",
                    statement_index=statement_index,
                    row_kind="scope_issue",
                    row_index=issue_index,
                )
            if issue.severity not in {"info", "warning", "error"}:
                _issue(
                    report,
                    "invalid_scope_issue_severity",
                    f"unsupported scope issue severity: {issue.severity!r}",
                    statement_index=statement_index,
                    row_kind="scope_issue",
                    row_index=issue_index,
                )
            if not isinstance(issue.detail, dict):
                _issue(
                    report,
                    "invalid_scope_issue_detail",
                    "snapshot scope issue detail must be an object",
                    statement_index=statement_index,
                    row_kind="scope_issue",
                    row_index=issue_index,
                )
            _source_span(
                report,
                issue.source_span,
                statement_index=statement_index,
                row_kind="scope_issue",
                row_index=issue_index,
            )
            if issue.quarantine is not None and not any(
                item is issue.quarantine for item in statement.quarantine
            ):
                _issue(
                    report,
                    "scope_issue_quarantine_mismatch",
                    "snapshot scope issue references quarantine outside its statement",
                    statement_index=statement_index,
                    row_kind="scope_issue",
                    row_index=issue_index,
                )

    actual_scopes = {
        (position.currency, "positions", position.scope_key)
        for position in statement.positions
    } | {
        (cash.currency, "cash", cash.scope_key)
        for cash in statement.cash_balances
    }
    for row_kind, rows in (
        ("position", statement.positions),
        ("cash", statement.cash_balances),
    ):
        for row_index, row in enumerate(rows):
            if not isinstance(row.scope_key, str) or not row.scope_key.strip():
                _issue(
                    report,
                    "invalid_snapshot_scope_key",
                    f"{row_kind} scope_key must be non-empty text",
                    statement_index=statement_index,
                    row_kind=row_kind,
                    row_index=row_index,
                )
    for scope in sorted(actual_scopes, key=repr):
        if scope not in declared_scopes:
            _issue(
                report,
                "snapshot_scope_undeclared",
                f"rows exist for undeclared snapshot scope {scope!r}",
                severity="warning",
                statement_index=statement_index,
            )
    for scope in declared_scopes:
        declaration = next(
            item
            for item in statement.snapshot_sets
            if (item.currency, item.section_type, item.scope_key) == scope
        )
        if declaration.completeness == "absent" and scope in actual_scopes:
            _issue(
                report,
                "absent_snapshot_scope_has_rows",
                f"scope {scope!r} is declared absent but emits rows",
                statement_index=statement_index,
            )

    for row_index, item in enumerate(statement.quarantine):
        if isinstance(item, ParsedQuarantine):
            raw_line, reason, span = item.raw_line, item.reason, item.source_span
        else:
            raw_line, reason = item
            span = None
        if not raw_line.strip():
            _issue(
                report,
                "empty_quarantine_raw_line",
                "quarantine item has no source text",
                statement_index=statement_index,
                row_kind="quarantine",
                row_index=row_index,
            )
        if not reason.strip():
            _issue(
                report,
                "empty_quarantine_reason",
                "quarantine item has no reason",
                statement_index=statement_index,
                row_kind="quarantine",
                row_index=row_index,
            )
        _source_span(
            report,
            span,
            statement_index=statement_index,
            row_kind="quarantine",
            row_index=row_index,
        )

    if pages:
        page_set = set(pages)
        spans = [
            transaction.source_span for transaction in statement.transactions
        ] + [
            position.source_span for position in statement.positions
        ] + [
            cash.source_span for cash in statement.cash_balances
        ] + [
            snapshot.source_span for snapshot in statement.snapshot_sets
        ] + [
            issue.source_span
            for snapshot in statement.snapshot_sets
            for issue in snapshot.issues
        ] + [
            item.source_span
            for item in statement.quarantine
            if isinstance(item, ParsedQuarantine)
        ]
        outside = sorted({
            span.page_number
            for span in spans
            if span is not None
            and span.page_number is not None
            and span.page_number not in page_set
        })
        if outside:
            _issue(
                report,
                "source_span_outside_statement_pages",
                f"source evidence pages are outside statement membership: {outside}",
                statement_index=statement_index,
            )


def validate_parse_result(
    result: ParseResult,
    *,
    page_count: int | None = None,
) -> ValidationReport:
    """Validate one complete parser result before persistence."""
    report = ValidationReport()
    if not result.parser_name.strip():
        _issue(report, "missing_parser_name", "ParseResult.parser_name is empty")
    if not result.parser_version.strip():
        _issue(report, "missing_parser_version", "ParseResult.parser_version is empty")
    for error in result.errors:
        _issue(report, "parser_reported_error", error)

    seen: dict[tuple[str, str, str, str], int] = {}
    for statement_index, statement in enumerate(result.statements):
        key = statement_key(statement)
        if key in seen:
            _issue(
                report,
                "duplicate_statement_key",
                f"statement key {key!r} duplicates statement {seen[key]}",
                statement_index=statement_index,
            )
        else:
            seen[key] = statement_index
        _validate_statement(
            report,
            statement,
            statement_index,
            page_count=page_count,
            require_pages=page_count is not None,
        )
    return report
