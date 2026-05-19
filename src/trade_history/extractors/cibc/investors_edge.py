"""CIBC Investors Edge extractor (Invest Direct + TFSA, same format)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
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
    parse_cibc_option,
    parse_quantity,
    parse_short_date,
)

# ── Signatures ────────────────────────────────────────────────────────────────

_HANDLE_SIGS = [
    "Investor's Edge Investment Account",
    "Investor's Edge Self-Directed",
    "Self-Directed Tax Free Savings Account",
    "Investors Edge",
]

# ── Patterns ──────────────────────────────────────────────────────────────────

# Account ID: "588-93738" or "605-82155"
_ACCOUNT_ID_RE = re.compile(r"\b(\d{3}-\d{5})\b")

# Period: "August 1-August 31, 2021"  or  "January 1 – January 31, 2024"
_PERIOD_RE = re.compile(
    r"([A-Za-z]+)\s+(\d{1,2})\s*[-\u2013]\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})"
)

# Transaction date prefix: "Aug 25 ..."
_TX_DATE_RE = re.compile(r"^([A-Za-z]{3,9})\s+(\d{1,2})\s+(.+)$")

# Activity keywords — first match wins (ordered list of tuples)
_ACTIVITY_MAP: list[tuple[str, str]] = [
    ("bought", "bought"),
    ("purchase", "bought"),
    ("buy ", "bought"),
    ("sold", "sold"),
    ("sell ", "sold"),
    ("dividend", "dividend"),
    ("div ", "dividend"),
    ("interest", "interest"),
    ("electronic funds", "contribution"),
    ("eft ", "contribution"),
    ("contrib", "contribution"),
    ("deposit", "contribution"),
    ("withdrawal", "withdrawal"),
    ("redeem", "withdrawal"),
    ("fee", "fee"),
    ("commission", "fee"),
    ("exercise", "exercise"),
    ("assignment", "assignment"),
    ("assign ", "assignment"),
    ("expir", "expired"),
    ("non-res tax", "withholding_tax"),
    ("withhel", "withholding_tax"),
    ("reinvest", "reinvestment"),
    ("transfer", "transfer_in"),
    ("journal", "transfer_in"),
    ("merger", "exchange"),
    ("cash-lieu", "cash_in_lieu"),
    ("cash in lieu", "cash_in_lieu"),
    ("unsolicited", "corporate_action"),
    ("corp com", "corporate_action"),
    ("equivalent", "fx_equivalent"),
]

# Header-row patterns inside the activity table — skip these
_SKIP_RE = re.compile(
    r"^(Date\b|Activity\b|Description\b|Quantity\b|Price\b|Amount\b|"
    r"Opening\b|Closing\b|Balance\b|Total\b|Subtotal\b|Page\s+\d)",
    re.IGNORECASE,
)

# Balance line: "Opening cash balance $X" or "Closing cash balance $X"
_CIBC_BALANCE_RE = re.compile(
    r"(opening|closing)\s+cash\s+balance.*?\$?([\d,]+\.?\d*)",
    re.IGNORECASE,
)


# ── Extractor ─────────────────────────────────────────────────────────────────

@ExtractorRegistry.register
class CIBCInvestorsEdge(StatementExtractor):
    INSTITUTION = "CIBC"

    @classmethod
    def can_handle(cls, pdf_path: Path, first_page_text: str) -> bool:
        return any(sig in first_page_text for sig in _HANDLE_SIGS)

    def extract(
        self, pdf_path: Path
    ) -> Iterator[tuple[RawStatement, list[RawTransaction], list[RawPosition]]]:
        full_text, self._docling_dict = convert_pdf_via_docling(pdf_path)

        statement = self._parse_header(full_text, pdf_path)
        transactions, opening_balance, closing_balance = self._parse_transactions(full_text, statement)
        statement.opening_balance = opening_balance
        statement.closing_balance = closing_balance
        positions = self._parse_positions(full_text)

        yield statement, transactions, positions

    # ── Header ────────────────────────────────────────────────────────────────

    def _parse_header(self, text: str, pdf_path: Path) -> RawStatement:
        # Account ID
        account_id = "UNKNOWN"
        for m in _ACCOUNT_ID_RE.finditer(text):
            if is_valid_account_id(m.group(1)):
                account_id = m.group(1)
                break

        # Account type
        if "Tax Free" in text or "TFSA" in text:
            account_type = "tfsa"
        elif "RRSP" in text:
            account_type = "rrsp"
        else:
            account_type = "margin"

        # Period: "August 1-August 31, 2021"
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
            account_type=account_type,
            primary_currency="CAD",
            period_start=period_start,
            period_end=period_end,
            source_file=pdf_path,
        )

    # ── Transactions ──────────────────────────────────────────────────────────

    def _parse_transactions(
        self, text: str, stmt: RawStatement
    ) -> tuple[list[RawTransaction], Decimal | None, Decimal | None]:
        """Parse transactions and return (transactions, opening_balance, closing_balance)."""
        transactions: list[RawTransaction] = []
        current_currency = "CAD"
        period_year = stmt.period_end.year
        period_month = stmt.period_end.month
        in_activity = False
        opening_balance: Decimal | None = None
        closing_balance: Decimal | None = None

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            upper = stripped.upper()

            # Section detection — CIBC uses "Account Activity" as section header
            if "ACCOUNT ACTIVITY" in upper:
                in_activity = True
                if "U.S. DOLLAR" in upper or "US DOLLAR" in upper:
                    current_currency = "USD"
                else:
                    current_currency = "CAD"
                continue

            # Future Settlement section also contains real transactions
            if "FUTURE SETTLEMENT" in upper:
                in_activity = True
                if "U.S." in upper or "USD" in upper:
                    current_currency = "USD"
                continue

            # Exit activity section at portfolio/holdings
            if "PORTFOLIO ASSETS" in upper or "PORTFOLIO OVERVIEW" in upper:
                in_activity = False
                continue

            if not in_activity:
                continue

            # Skip table header rows
            if _SKIP_RE.match(stripped):
                continue

            m = _TX_DATE_RE.match(stripped)
            if not m:
                continue

            mon_str, day_str, rest = m.groups()

            # Capture Opening/Closing cash balance values before skipping
            # Must check BEFORE dash/period check — docling format:
            # "Oct 1 - Opening cash balance  - - $500,000.00"
            bm = _CIBC_BALANCE_RE.search(rest)
            if bm:
                bal_type, bal_val = bm.groups()
                bal = parse_amount(bal_val)
                if "opening" in bal_type.lower():
                    opening_balance = bal
                else:
                    closing_balance = bal
                continue

            # Period lines look like "September 1-September 30, 2021" — rest starts with "-"
            rest_stripped = rest.lstrip()
            if rest_stripped.startswith("-") or rest_stripped.startswith("\u2013"):
                continue

            tx_date = parse_short_date(mon_str, int(day_str), period_year, period_month)
            if not tx_date:
                continue

            tx = _parse_tx_rest(rest, tx_date, current_currency, stripped)
            if tx:
                transactions.append(tx)

        return transactions, opening_balance, closing_balance

    # ── Positions ─────────────────────────────────────────────────────────────

    def _parse_positions(self, text: str) -> list[RawPosition]:
        positions: list[RawPosition] = []
        current_currency = "CAD"
        in_portfolio = False

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            upper = stripped.upper()

            if "CANADIAN DOLLAR" in upper or "CDN DOLLAR" in upper:
                current_currency = "CAD"
            elif "US DOLLAR" in upper or "U.S. DOLLAR" in upper:
                current_currency = "USD"

            if re.match(r"^(Portfolio|Holdings|Your Portfolio|Investment Summary)", stripped, re.IGNORECASE):
                in_portfolio = True
                continue
            if re.match(r"^Activity\s*$", stripped, re.IGNORECASE):
                in_portfolio = False
            if not in_portfolio:
                continue

            pos = _try_parse_position_line(stripped, current_currency)
            if pos:
                positions.append(pos)

        return positions


# ── Module-level helpers ───────────────────────────────────────────────────────

def _parse_tx_rest(
    rest: str, tx_date: date, currency: str, raw_text: str
) -> RawTransaction | None:
    """Parse the portion of a CIBC transaction line after the date."""
    tokens = rest.split()
    numeric_end: list[str] = []
    for tok in reversed(tokens):
        if re.match(r"^\(?\-?\$?[\d,]+\.?\d*\)?$", tok):
            numeric_end.insert(0, tok)
        else:
            break

    if not numeric_end:
        return None

    amount = parse_amount(numeric_end[-1])
    quantity: Decimal | None = None
    price: Decimal | None = None
    if len(numeric_end) >= 3:
        quantity = parse_quantity(numeric_end[-3])
        price = parse_quantity(numeric_end[-2])
    elif len(numeric_end) == 2:
        price = parse_quantity(numeric_end[-2])

    description = " ".join(tokens[: len(tokens) - len(numeric_end)]).strip()
    if not description:
        return None

    activity = _infer_activity(description)
    opt = parse_cibc_option(description)
    symbol: str | None = opt.root if opt else _extract_symbol(description)

    return RawTransaction(
        date=tx_date,
        activity=activity,
        description=description,
        amount=amount,
        currency=currency,
        raw_text=raw_text,
        symbol=symbol,
        quantity=quantity,
        price=price,
    )


def _infer_activity(description: str) -> str:
    lower = description.lower()
    for key, val in _ACTIVITY_MAP:
        if key in lower:
            return val
    return "other"


def _extract_symbol(description: str) -> str | None:
    # CIBC descriptions are company names, not tickers.
    # Symbol extraction is only reliable via parse_cibc_option() for options.
    # For equities, return None and let the normalizer use the description.
    return None


def _try_parse_position_line(line: str, currency: str) -> RawPosition | None:
    tokens = line.split()
    numeric_suffix: list[str] = []
    for tok in reversed(tokens):
        if re.match(r"^[\d,.()\$-]+$", tok) and ("." in tok or tok.replace(",", "").isdigit()):
            numeric_suffix.insert(0, tok)
        else:
            break
    if len(numeric_suffix) < 2:
        return None

    description = " ".join(tokens[: len(tokens) - len(numeric_suffix)]).strip()
    if not description or len(description) < 2:
        return None

    try:
        market_value = parse_amount(numeric_suffix[-1])
        market_price = parse_amount(numeric_suffix[-2]) if len(numeric_suffix) >= 2 else None
        quantity = parse_quantity(numeric_suffix[-3]) if len(numeric_suffix) >= 3 else None
    except Exception:
        return None

    opt = parse_cibc_option(description)
    if opt or any(k in description.upper() for k in ("CALL", "PUT")):
        asset_type = "option"
    elif any(k in description.upper() for k in ("FUND", "MUTUAL")):
        asset_type = "mutual_fund"
    else:
        asset_type = "equity"

    symbol = opt.root if opt else _extract_symbol(description)

    return RawPosition(
        description=description,
        symbol=symbol,
        quantity=quantity or Decimal("0"),
        book_cost=None,
        market_price=market_price,
        market_value=market_value,
        currency=currency,
        asset_type=asset_type,
    )
