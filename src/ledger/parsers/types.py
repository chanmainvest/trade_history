"""Common types used by parsers.

A parser's job is to take an extracted PDF (text, by-page) and produce a list
of `ParsedStatement` objects, each with header info, transactions, positions
and cash balances. The ingest pipeline writes these to SQLite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PARSER_CONTRACT_VERSION = "6"

# Canonical transaction-type vocabulary. See schema.sql for definitions.
TxnType = Literal[
    "buy", "sell", "short_sell", "buy_to_cover",
    "option_buy_to_open", "option_sell_to_open",
    "option_buy_to_close", "option_sell_to_close",
    "option_assignment", "option_exercise", "option_expiration",
    "dividend", "distribution", "interest_income",
    "interest_expense", "margin_interest",
    "transfer_in", "transfer_out", "journal",
    "deposit", "withdrawal",
    "tax_withholding",
    "fee", "commission", "adjustment", "fx_conversion",
    "reinvest_dividend", "stock_split", "stock_split_credit", "stock_split_debit",
    "name_change", "spinoff", "merger", "return_of_capital",
]

SnapshotCompleteness = Literal["complete", "partial", "absent", "unknown"]
SnapshotSectionType = Literal["positions", "cash", "summary"]


@dataclass
class SourceSpan:
    """Location and parser rule supporting one emitted value or row."""

    raw_text: str | None = None
    page_number: int | None = None
    line_number: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    words: list[dict[str, object]] | None = None
    parser_rule: str | None = None


@dataclass
class ParsedInstrument:
    asset_type: str               # equity, etf, option, mutual_fund, bond, cash, other
    symbol: str
    currency: str
    exchange: str | None = None
    name: str | None = None
    option_root: str | None = None
    option_expiry: str | None = None    # YYYY-MM-DD
    option_strike: float | None = None
    option_type: str | None = None      # CALL/PUT
    option_multiplier: int = 100
    # The parser preserves the printed identity.  The staged ingest resolver
    # records whether that identity was explicit, matched a reviewed alias, or
    # intentionally left unresolved before persistence.
    resolution_method: str | None = None
    resolution_confidence: float | None = None
    resolution_evidence: SourceSpan | None = None
    # Listing metadata is assigned only by the staged identity resolver. The
    # parser itself remains text-only and provider-independent.
    issuer_key: str | None = None
    issuer_name: str | None = None
    security_key: str | None = None
    security_name: str | None = None
    journalable: bool = False
    market_symbol: str | None = None


@dataclass
class ParsedTxn:
    trade_date: str               # YYYY-MM-DD
    settle_date: str | None
    txn_type: TxnType
    instrument: ParsedInstrument | None
    quantity: float | None
    price: float | None
    gross_amount: float | None
    commission: float | None
    other_fees: float | None
    net_amount: float | None
    currency: str
    description: str | None
    raw_line: str
    tax_country: str | None = None
    tax_rate: float | None = None
    parser_confidence: float = 1.0
    position_delta: float | None = None
    cash_delta: float | None = None
    cash_effective_date: str | None = None
    resolution_method: str | None = None
    resolution_confidence: float | None = None
    resolution_evidence: SourceSpan | None = None
    source_span: SourceSpan | None = None
    # Corporate actions may replace the printed instrument with another
    # instrument. Keep both identities instead of collapsing the old ticker
    # into an undated alias.
    related_instrument: ParsedInstrument | None = None
    corporate_action_ratio: float | None = None


@dataclass
class ParsedPosition:
    instrument: ParsedInstrument
    quantity: float
    avg_cost: float | None
    book_value: float | None
    market_price: float | None
    market_value: float | None
    unrealized_pnl: float | None
    currency: str
    raw_line: str | None = None
    source_span: SourceSpan | None = None
    scope_key: str = "default"


@dataclass
class ParsedCashBalance:
    currency: str
    opening_balance: float | None
    closing_balance: float
    raw_line: str | None = None
    source_span: SourceSpan | None = None
    scope_key: str = "default"


@dataclass
class ParsedQuarantine:
    raw_line: str
    reason: str
    source_span: SourceSpan | None = None

    def __iter__(self):
        """Retain tuple-unpacking compatibility during parser migration."""
        yield self.raw_line
        yield self.reason


@dataclass
class ParsedScopeIssue:
    """One structured reason a snapshot scope cannot be trusted as complete."""

    issue_code: str
    severity: Literal["info", "warning", "error"] = "warning"
    detail: dict[str, object] = field(default_factory=dict)
    blocks_completeness: bool = True
    source_span: SourceSpan | None = None
    quarantine: ParsedQuarantine | None = None


@dataclass
class ParsedSnapshotSet:
    """Declared completeness of one statement currency/section scope."""

    currency: str
    section_type: SnapshotSectionType
    completeness: SnapshotCompleteness
    scope_key: str = "default"
    reported_total: float | None = None
    validation_status: Literal["unvalidated", "valid", "warning", "invalid"] = (
        "unvalidated"
    )
    source_span: SourceSpan | None = None
    issues: list[ParsedScopeIssue] = field(default_factory=list)


@dataclass
class ParsedAnnualPerformance:
    currency: str
    period_start: str
    period_end: str
    since_date: str | None
    beginning_market_value: float | None
    deposits_transfers_in: float | None
    withdrawals_transfers_out: float | None
    net_investment_return: float | None
    ending_market_value: float | None
    money_weighted_1y: float | None
    money_weighted_3y: float | None
    money_weighted_5y: float | None
    money_weighted_10y: float | None
    money_weighted_since: float | None


@dataclass
class ParsedAccount:
    account_number: str
    account_type: str | None       # Cash/Margin/RRSP/TFSA...
    base_currency: str = "CAD"


@dataclass
class ParsedStatement:
    account: ParsedAccount
    period_start: str              # YYYY-MM-DD
    period_end: str                # YYYY-MM-DD
    statement_type: str = "monthly"
    transactions: list[ParsedTxn] = field(default_factory=list)
    positions: list[ParsedPosition] = field(default_factory=list)
    cash_balances: list[ParsedCashBalance] = field(default_factory=list)
    annual_performance: list[ParsedAnnualPerformance] = field(default_factory=list)
    quarantine: list[tuple[str, str] | ParsedQuarantine] = field(default_factory=list)
    snapshot_sets: list[ParsedSnapshotSet] = field(default_factory=list)
    page_numbers: tuple[int, ...] = ()
    page_assignment_method: Literal[
        "parser_explicit", "single_statement_source"
    ] | None = "parser_explicit"


@dataclass
class ParseResult:
    parser_name: str
    parser_version: str
    statements: list[ParsedStatement] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    status: Literal["parsed", "skipped"] = "parsed"
    skip_reason: str | None = None
