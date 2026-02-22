from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol


@dataclass(slots=True)
class ParsedAccount:
    account_id: str
    institution: str
    account_name: str | None = None
    account_type: str | None = None
    base_currency: str | None = None
    masked_number: str | None = None


@dataclass(slots=True)
class ParsedInstrument:
    symbol_raw: str
    symbol_norm: str
    asset_type: str = "equity"
    option_root: str | None = None
    strike: float | None = None
    expiry: date | None = None
    put_call: str | None = None
    multiplier: int = 1
    exchange: str | None = None
    sector: str | None = None


@dataclass(slots=True)
class ParsedEvent:
    account_id: str
    trade_date: date
    settle_date: date | None
    event_type: str
    side: str | None
    quantity: float | None
    price: float | None
    gross_amount: float | None
    commission: float
    fees: float
    currency: str | None
    instrument: ParsedInstrument | None
    source_line_ref: str | None = None
    notes: str | None = None


@dataclass(slots=True)
class ParsedSnapshot:
    account_id: str
    metric_code: str
    value_native: float
    currency: str | None = None
    snapshot_date: date | None = None
    source_line_ref: str | None = None
    raw_line: str | None = None


@dataclass(slots=True)
class ParseIssue:
    page_number: int | None
    raw_line: str
    reason: str


@dataclass(slots=True)
class ParsedStatement:
    institution: str
    file_path: Path
    format_version: str
    accounts: list[ParsedAccount] = field(default_factory=list)
    events: list[ParsedEvent] = field(default_factory=list)
    snapshots: list[ParsedSnapshot] = field(default_factory=list)
    issues: list[ParseIssue] = field(default_factory=list)
    parse_message: str | None = None
    period_start: date | None = None
    period_end: date | None = None


class StatementParser(Protocol):
    institution: str

    def detect_format(self, file_path: Path) -> str:
        ...

    def parse(self, file_path: Path) -> ParsedStatement:
        ...
