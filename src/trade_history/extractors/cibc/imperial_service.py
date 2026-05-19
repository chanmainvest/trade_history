"""CIBC Imperial Investor Service extractor (managed account, mutual funds only)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

from trade_history.extractors.base import (
    RawPosition,
    RawStatement,
    RawTransaction,
    StatementExtractor,
)
from trade_history.extractors.registry import ExtractorRegistry
from trade_history.extractors.utils import (
    convert_pdf_via_docling,
    is_valid_account_id,
    parse_amount,
    parse_quantity,
    parse_short_date,
)

_HANDLE_SIG = "Imperial Investor Service"
_ACCOUNT_ID_RE = re.compile(r"\b(\d{3}-\d{5})\b")

# Period: "August 1-August 31, 2021"
_PERIOD_RE = re.compile(
    r"([A-Za-z]+)\s+(\d{1,2})\s*[-\u2013]\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})"
)

# Transaction date prefix: "Aug 25 ..."
_TX_DATE_RE = re.compile(r"^([A-Za-z]{3,9})\s+(\d{1,2})\s+(.+)$")

# Reinvestment / distribution keywords
_REINVEST_KEYWORDS = ("reinvestment", "distribution", "dividend", "reinvest")

# Fee keywords
_FEE_KEYWORDS = ("fee", "management", "advisory", "admin")


@ExtractorRegistry.register
class CIBCImperialService(StatementExtractor):
    INSTITUTION = "CIBC"

    @classmethod
    def can_handle(cls, pdf_path: Path, first_page_text: str) -> bool:
        return _HANDLE_SIG in first_page_text

    def extract(
        self, pdf_path: Path
    ) -> Iterator[tuple[RawStatement, list[RawTransaction], list[RawPosition]]]:
        full_text, self._docling_dict = convert_pdf_via_docling(pdf_path)

        statement = self._parse_header(full_text, pdf_path)
        transactions = self._parse_transactions(full_text, statement)
        positions = self._parse_positions(full_text)

        yield statement, transactions, positions

    def _parse_header(self, text: str, pdf_path: Path) -> RawStatement:
        account_id = "UNKNOWN"
        for m in _ACCOUNT_ID_RE.finditer(text):
            if is_valid_account_id(m.group(1)):
                account_id = m.group(1)
                break

        period_start = date(2000, 1, 1)
        period_end = date(2000, 1, 1)
        pm = _PERIOD_RE.search(text)
        if pm:
            s_mon, s_day, e_mon, e_day, yr = pm.groups()
            year = int(yr)
            for fmt in ("%B", "%b"):
                try:
                    period_end = datetime.strptime(
                        f"{e_mon} {e_day} {year}", f"{fmt} %d %Y"
                    ).date()
                    period_start = datetime.strptime(
                        f"{s_mon} {s_day} {year}", f"{fmt} %d %Y"
                    ).date()
                    break
                except ValueError:
                    continue

        return RawStatement(
            institution="CIBC",
            account_id=account_id,
            account_type="managed",
            primary_currency="CAD",
            period_start=period_start,
            period_end=period_end,
            source_file=pdf_path,
        )

    def _parse_transactions(self, text: str, stmt: RawStatement) -> list[RawTransaction]:
        transactions: list[RawTransaction] = []
        period_year = stmt.period_end.year
        period_month = stmt.period_end.month

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            m = _TX_DATE_RE.match(stripped)
            if not m:
                continue

            mon_str, day_str, rest = m.groups()
            tx_date = parse_short_date(mon_str, int(day_str), period_year, period_month)
            if not tx_date:
                continue

            lower_rest = rest.lower()
            if any(kw in lower_rest for kw in _REINVEST_KEYWORDS):
                activity = "reinvestment"
            elif any(kw in lower_rest for kw in _FEE_KEYWORDS):
                activity = "fee"
            else:
                continue  # Imperial only captures reinvestments and fees

            # Extract trailing numerics: [qty, amount] or just [amount]
            tokens = rest.split()
            numeric_end: list[str] = []
            for tok in reversed(tokens):
                if re.match(r"^\(?\-?\$?[\d,]+\.?\d*\)?$", tok):
                    numeric_end.insert(0, tok)
                else:
                    break

            if not numeric_end:
                continue

            amount = parse_amount(numeric_end[-1])
            if activity == "fee":
                amount = -abs(amount)

            qty: object = None
            if len(numeric_end) >= 2:
                qty = parse_quantity(numeric_end[-2])

            description = " ".join(tokens[: len(tokens) - len(numeric_end)]).strip()

            transactions.append(
                RawTransaction(
                    date=tx_date,
                    activity=activity,
                    description=description,
                    amount=amount,
                    currency="CAD",
                    raw_text=stripped,
                    quantity=qty,
                )
            )

        return transactions

    def _parse_positions(self, text: str) -> list[RawPosition]:
        positions: list[RawPosition] = []
        # Mutual fund positions: fund name + units + price + market value
        mf_re = re.compile(
            r"([A-Z][A-Za-z &]+(?:Fund|Portfolio)[A-Za-z ]*)\s+([\d,]+\.?\d*)\s+\$?([\d,]+\.?\d*)\s+\$?([\d,]+\.?\d*)",
            re.IGNORECASE,
        )
        for m in mf_re.finditer(text):
            desc, qty_str, price_str, mv_str = m.groups()
            qty = parse_quantity(qty_str)
            if not qty:
                continue
            positions.append(
                RawPosition(
                    description=desc.strip(),
                    symbol=None,
                    quantity=qty,
                    book_cost=None,
                    market_price=parse_amount(price_str),
                    market_value=parse_amount(mv_str),
                    currency="CAD",
                    asset_type="mutual_fund",
                )
            )
        return positions
