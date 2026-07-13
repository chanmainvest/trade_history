"""Runtime validation for parser output.

The parser dataclasses are intentionally lightweight and Python's Literal
annotations are not enforced at runtime. This module provides the validation
boundary used by ingestion and the read-only extraction audit.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Literal, get_args

from .types import ParsedInstrument, ParsedStatement, ParseResult, TxnType

Severity = Literal["error", "warning"]

VALID_TXN_TYPES = frozenset(get_args(TxnType))
VALID_ASSET_TYPES = frozenset(
    {"equity", "etf", "option", "bond", "mutual_fund", "cash", "other"}
)
POSITION_AFFECTING_TYPES = frozenset(
    {
        "buy", "sell", "short_sell", "buy_to_cover",
        "option_buy_to_open", "option_sell_to_open",
        "option_buy_to_close", "option_sell_to_close",
        "option_assignment", "option_exercise", "option_expiration",
        "transfer_in", "transfer_out", "journal", "reinvest_dividend",
        "stock_split", "stock_split_credit", "stock_split_debit",
        "name_change", "spinoff", "merger",
    }
)
VALID_STATEMENT_TYPES = frozenset({"monthly", "quarterly", "annual", "interim"})
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


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
    """Return a deterministic audit-only logical instrument key.

    This does not replace the Phase 2 database instrument key. It lets the
    read-only audit compare parser output without broken SQLite IDs.
    """
    symbol = re.sub(r"\s+", "", (instrument.option_root or instrument.symbol).upper())
    if instrument.asset_type == "option":
        strike = (
            format(float(instrument.option_strike), ".12g")
            if instrument.option_strike is not None
            else ""
        )
        return "|".join(
            [
                "option", symbol, instrument.currency.upper(),
                instrument.option_expiry or "", strike,
                instrument.option_type or "", str(instrument.option_multiplier),
            ]
        )
    return "|".join([instrument.asset_type, symbol, instrument.currency.upper()])


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
    if not isinstance(value, str) or not _CURRENCY_RE.fullmatch(value):
        _issue(
            report,
            "invalid_currency",
            f"currency must be an uppercase three-letter code, got {value!r}",
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
) -> None:
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
        for field_name in (
            "quantity", "price", "gross_amount", "commission", "other_fees",
            "net_amount", "tax_rate", "parser_confidence",
        ):
            _finite(
                report,
                getattr(transaction, field_name),
                field_name=field_name,
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
        _issue(
            report,
            "cash_source_evidence_unavailable",
            "ParsedCashBalance has no raw source field in the current contract",
            severity="warning",
            statement_index=statement_index,
            row_kind=row_kind,
            row_index=row_index,
        )

    for row_index, (raw_line, reason) in enumerate(statement.quarantine):
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

    if statement.positions or statement.cash_balances:
        _issue(
            report,
            "snapshot_completeness_unavailable",
            "the current parser contract cannot declare section scope/completeness",
            severity="warning",
            statement_index=statement_index,
        )


def validate_parse_result(result: ParseResult) -> ValidationReport:
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
        _validate_statement(report, statement, statement_index)
    return report
