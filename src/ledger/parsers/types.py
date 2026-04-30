"""Common types used by parsers.

A parser's job is to take an extracted PDF (text, by-page) and produce a list
of `ParsedStatement` objects, each with header info, transactions, positions
and cash balances. The ingest pipeline writes these to SQLite.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


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
    "stock_split", "name_change", "spinoff", "merger", "return_of_capital",
]


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


@dataclass
class ParsedCashBalance:
    currency: str
    opening_balance: float | None
    closing_balance: float


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
    quarantine: list[tuple[str, str]] = field(default_factory=list)  # (raw_line, reason)


@dataclass
class ParseResult:
    parser_name: str
    parser_version: str
    statements: list[ParsedStatement] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
