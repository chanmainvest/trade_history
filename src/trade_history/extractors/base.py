"""Base dataclasses and ABC for statement extractors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import ClassVar


@dataclass
class RawStatement:
    institution: str
    account_id: str
    account_type: str       # 'margin' | 'tfsa' | 'rrsp' | 'managed' | ...
    primary_currency: str   # 'CAD' | 'USD'
    period_start: date
    period_end: date
    source_file: Path
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None


@dataclass
class RawTransaction:
    date: date
    activity: str           # institution-specific raw verb; normalised in pipeline
    description: str
    amount: Decimal         # positive = credit, negative = debit
    currency: str
    raw_text: str
    settle_date: date | None = None
    symbol: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    commission: Decimal = field(default_factory=lambda: Decimal("0"))


@dataclass
class RawPosition:
    description: str
    quantity: Decimal
    currency: str
    asset_type: str         # 'equity'|'option'|'mutual_fund'|'etf'|'cash'
    symbol: str | None = None
    book_cost: Decimal | None = None
    market_price: Decimal | None = None
    market_value: Decimal | None = None


class StatementExtractor(ABC):
    INSTITUTION: ClassVar[str]

    @classmethod
    @abstractmethod
    def can_handle(cls, pdf_path: Path, first_page_text: str) -> bool:
        """Return True if this extractor handles the given PDF."""
        ...

    @abstractmethod
    def extract(
        self, pdf_path: Path
    ) -> Iterator[tuple[RawStatement, list[RawTransaction], list[RawPosition]]]:
        """
        Yield one tuple per sub-account.
        HSBC yields two (CAD, then USD); all others yield one.
        """
        ...


class UnknownStatementError(Exception):
    """Raised when no extractor can handle a PDF."""
