"""RBC Direct Investing extractor — handles old and new statement formats.

RBC monthly statements use a two-line period header:
  "Order Execution Only AUG. 31"
  "Cdn. Dollar Statement 2021"
Transaction dates are "AUG.10" (month+period+day, no space, ALL CAPS, no year).
DEBIT/CREDIT are separate columns — sign is inferred from activity.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
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
    _MONTH_MAP,
    convert_pdf_via_docling,
    is_valid_account_id,
    parse_amount,
    parse_date_flexible,
    parse_quantity,
    parse_rbc_option,
    parse_short_date,
)

_HANDLE_SIG = "RBC Direct Investing"

_OLD_ACCOUNT_RE = re.compile(r"\b(668-44715-2-\d)\b")
_NEW_ACCOUNT_RE = re.compile(r"\b(670-27469-2-\d)\b")
_GENERIC_ACCOUNT_RE = re.compile(r"\b(\d{3}-\d{5}-\d-\d)\b")

# Two-line period header for RBC monthly statements:
# pdfplumber layout:  Line 1: "Order Execution Only AUG. 31"
#                     Line 2: "Cdn. Dollar Statement 2021"
# docling layout:     Line 1: "Order Execution Only Cdn. Dollar Statement"
#                     Line 2: "AUG. 31 2021"
_RBC_HEADER_MONTH_RE = re.compile(
    r"Order\s+Execution\s+Only\s+([A-Z]{3,4})\.?\s+(\d{1,2})"
)
_RBC_HEADER_YEAR_RE = re.compile(r"Cdn\.\s+Dollar\s+Statement\s+(\d{4})")
# Docling single-line date: "AUG. 31 2021", "SEPT 30 2022", "JULY 29 2022"
_RBC_DATE_LINE_RE = re.compile(r"^([A-Z]{3,9})\.?\s+(\d{1,2})\s+(\d{4})$")

# "Date of Last Statement: JULY 30, 2021" — optional, gives period start
_RBC_LAST_STMT_RE = re.compile(
    r"Date\s+of\s+Last\s+Statement[:\s]+([A-Z]+ \d+, \d{4})"
)

# Transaction date: "AUG.10" or "APR. 02" (ALL CAPS, no space before day)
_RBC_TX_DATE_RE = re.compile(r"^([A-Z]{3,4})\.?\s*(\d{1,2})\s+(.+)$")

# Activities that mean money flows OUT (negative amount)
_DEBIT_ACTIVITIES = {
    "bought", "buy", "purchase", "fee", "commission",
    "exercise", "withholding_tax", "withdrawal",
}

_ACTIVITY_KEYWORDS = {
    "buy": "bought",
    "bought": "bought",
    "purchase": "bought",
    "sell": "sold",
    "sold": "sold",
    "dividend": "dividend",
    "div ": "dividend",
    "dist on": "dividend",
    "distrib": "dividend",
    "interest": "interest",
    "transfer": "transfer_in",
    "trfin": "transfer_in",
    "wire tfr": "transfer_in",
    "deposit": "contribution",
    "contribut": "contribution",
    "withdrawal": "withdrawal",
    "fee": "fee",
    "commission": "fee",
    "exercise": "exercise",
    "assign": "assignment",
    "expir": "expired",
    "withhold": "withholding_tax",
    "nonres tx": "withholding_tax",
    "nonres": "withholding_tax",
    "mark to market": "mark_to_market",
    "market": "mark_to_market",
    "rtc": "return_of_capital",
    "return of capital": "return_of_capital",
    "exchange": "exchange",
    "name chg": "name_change",
    "adjust": "adjustment",
    "reinv": "reinvestment",
    "open contract": "adjustment",
    "stk split": "stock_split",
}

# Skip table header and page-break lines
_SKIP_RE = re.compile(
    r"^(DATE\b|PRICE\b|DEBIT\b|CREDIT\b|"
    r"Opening\s*Balance|Closing\s*Balance|"
    r"OpeningBalance|ClosingBalance|"
    r"-CONTINUED|Page\s+\d|\d+\s+of\s+\d+)",
    re.IGNORECASE,
)

# Balance line: "Opening Balance $X" or "Closing Balance (AUG. 31, 2021) $1,642.40"
_RBC_BALANCE_RE = re.compile(
    r"(Opening|Closing)\s*Balance\b.*?\$?([\d,]+\.?\d{2})",
    re.IGNORECASE,
)


@ExtractorRegistry.register
class RBCDirectInvesting(StatementExtractor):
    INSTITUTION = "RBC"

    @classmethod
    def can_handle(cls, pdf_path: Path, first_page_text: str) -> bool:
        return _HANDLE_SIG in first_page_text

    def extract(
        self, pdf_path: Path
    ) -> Iterator[tuple[RawStatement, list[RawTransaction], list[RawPosition]]]:
        full_text, self._docling_dict = convert_pdf_via_docling(pdf_path)

        account_id = _extract_account_id(full_text, pdf_path)
        period_start, period_end = _extract_period(full_text)

        cad_text, usd_text = _split_currency_sections(full_text)

        all_txs: list[RawTransaction] = []
        all_positions: list[RawPosition] = []
        cad_txs, cad_ob, cad_cb = self._parse_transactions(cad_text, "CAD", period_end)
        usd_txs, usd_ob, usd_cb = self._parse_transactions(usd_text, "USD", period_end)
        all_txs.extend(cad_txs)
        all_txs.extend(usd_txs)
        all_positions.extend(self._parse_positions(cad_text, "CAD"))
        all_positions.extend(self._parse_positions(usd_text, "USD"))

        stmt = RawStatement(
            institution="RBC",
            account_id=account_id,
            account_type=_detect_account_type(full_text),
            primary_currency="CAD",
            period_start=period_start,
            period_end=period_end,
            source_file=pdf_path,
            opening_balance=cad_ob,
            closing_balance=cad_cb,
        )

        yield stmt, all_txs, all_positions

    def _parse_transactions(
        self, text: str, currency: str, period_end: date
    ) -> tuple[list[RawTransaction], Decimal | None, Decimal | None]:
        """Parse transactions and return (transactions, opening_balance, closing_balance)."""
        transactions: list[RawTransaction] = []
        period_year = period_end.year
        period_month = period_end.month
        in_activity = False
        opening_balance: Decimal | None = None
        closing_balance: Decimal | None = None

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            upper = stripped.upper()

            # Section entry/exit
            if "ACCOUNT ACTIVITY" in upper:
                in_activity = True
                continue
            if "ASSET SUMMARY" in upper or "INCOME SUMMARY" in upper or "ASSET REVIEW" in upper:
                in_activity = False

            if not in_activity:
                continue

            if _SKIP_RE.match(stripped):
                # Capture balance values
                bm = _RBC_BALANCE_RE.search(stripped)
                if bm:
                    bal_type, bal_val = bm.groups()
                    bal = parse_amount(bal_val)
                    if "opening" in bal_type.lower():
                        opening_balance = bal
                    else:
                        closing_balance = bal
                continue

            # Match "AUG.10 ACTIVITY REST..." date prefix
            m = _RBC_TX_DATE_RE.match(stripped)
            if not m:
                continue

            mon_str, day_str, rest = m.groups()
            tx_date = parse_short_date(mon_str[:3], int(day_str), period_year, period_month)
            if not tx_date:
                continue

            tx = _parse_tx_rest(rest, tx_date, currency, stripped)
            if tx:
                transactions.append(tx)

        return transactions, opening_balance, closing_balance

    def _parse_positions(self, text: str, currency: str) -> list[RawPosition]:
        positions: list[RawPosition] = []
        # Look for the Asset Review / holdings section
        in_holdings = False

        for line in text.splitlines():
            stripped = line.strip()
            upper = stripped.upper()

            if "ASSET REVIEW" in upper or "SECURITIES HELD" in upper:
                in_holdings = True
                continue
            if "ACCOUNT ACTIVITY" in upper or "INCOME SUMMARY" in upper:
                in_holdings = False

            if not in_holdings:
                continue

            pos = _try_parse_position_line(stripped, currency)
            if pos:
                positions.append(pos)

        return positions


# ── Module-level helpers ───────────────────────────────────────────────────────

def _parse_tx_rest(
    rest: str, tx_date: date, currency: str, raw_text: str
) -> RawTransaction | None:
    """Parse the RBC transaction portion after the date."""
    tokens = rest.split()
    numeric_end: list[str] = []
    for tok in reversed(tokens):
        if re.match(r"^\(?\$?[\d,]+\.?\d*\)?$", tok):
            numeric_end.insert(0, tok)
        else:
            break

    if not numeric_end:
        return None

    # RBC columns: [Activity] [Description] [Qty] [Rate] [DEBIT | CREDIT]
    # Only one of DEBIT or CREDIT is non-empty per row.
    # Amount sign is inferred from activity.
    raw_amount = parse_amount(numeric_end[-1])
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

    # Apply sign: debit activities = negative amount
    if activity in _DEBIT_ACTIVITIES:
        amount = -abs(raw_amount)
    else:
        amount = abs(raw_amount)

    opt = parse_rbc_option(description)
    symbol = opt.root if opt else _extract_symbol(description)

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


def _extract_account_id(text: str, pdf_path: Path) -> str:
    for pattern in (_OLD_ACCOUNT_RE, _NEW_ACCOUNT_RE, _GENERIC_ACCOUNT_RE):
        m = pattern.search(text)
        if m:
            candidate = next(g for g in m.groups() if g)
            if is_valid_account_id(candidate):
                return candidate
    # Fallback from filename
    m2 = re.search(r"(\d{3}-\d{5}-\d-\d)", pdf_path.stem)
    if m2:
        return m2.group(1)
    return "UNKNOWN"


def _extract_period(text: str) -> tuple[date, date]:
    """Extract period from RBC header — supports both pdfplumber and docling layouts."""
    lines = text.splitlines()

    period_end: date | None = None
    period_start: date | None = None

    for i, line in enumerate(lines):
        # Strategy 1: pdfplumber two-line format
        # "Order Execution Only AUG. 31" + "Cdn. Dollar Statement 2021"
        mh = _RBC_HEADER_MONTH_RE.search(line)
        if mh:
            mon_str, day_str = mh.groups()
            for j in range(i + 1, min(i + 4, len(lines))):
                my = _RBC_HEADER_YEAR_RE.search(lines[j])
                if my:
                    year = int(my.group(1))
                    month = _MONTH_MAP.get(mon_str[:3].lower())
                    if month:
                        try:
                            period_end = date(year, month, int(day_str))
                        except ValueError:
                            pass
                    break
            if period_end:
                break

        # Strategy 2: docling single-line format
        # "AUG. 31 2021", "SEPT 30 2022", "JULY 29 2022"
        md = _RBC_DATE_LINE_RE.match(line.strip())
        if md:
            mon_str, day_str, year_str = md.groups()
            month = _MONTH_MAP.get(mon_str[:3].lower())
            if month:
                try:
                    period_end = date(int(year_str), month, int(day_str))
                except ValueError:
                    pass
            if period_end:
                break

    # Fallback: extract date from filename like "66844715-2021Aug31-2021Aug31.pdf"
    # or "Statement-7469 2025-07-31.pdf"
    if not period_end:
        period_end = _date_from_filename(text, lines)

    # Try "Date of Last Statement: JULY 30, 2021" for period start
    ms = _RBC_LAST_STMT_RE.search(text)
    if ms:
        ps = parse_date_flexible(ms.group(1).title())
        if ps:
            period_start = ps

    if period_end:
        if not period_start:
            period_start = date(period_end.year, period_end.month, 1)
        return period_start, period_end

    return date(2000, 1, 1), date(2000, 1, 1)


def _date_from_filename(text: str, lines: list[str]) -> date | None:
    """Last-resort: extract period end from 'AT MONTH DD' or 'Total on MONTH DD' lines."""
    # "AT JULY 30 %" or "Total on JULY 30"
    at_re = re.compile(r"(?:AT|Total\s+on)\s+([A-Z]{3,9})\s+(\d{1,2})\b")
    year_re = re.compile(r"\b(20\d{2})\b")

    # Find any year in the text
    all_years = year_re.findall(text[:3000])
    fallback_year = int(all_years[0]) if all_years else None

    for line in lines:
        m = at_re.search(line)
        if m:
            mon_str, day_str = m.groups()
            month = _MONTH_MAP.get(mon_str[:3].lower())
            if month and fallback_year:
                try:
                    return date(fallback_year, month, int(day_str))
                except ValueError:
                    pass
    return None


_TFSA_RE = re.compile(r"\bTFSA\b|TAX.FREE", re.IGNORECASE)
_RRSP_RE = re.compile(r"\bRRSP\b", re.IGNORECASE)
_RESP_RE = re.compile(r"\bRESP\b", re.IGNORECASE)


def _detect_account_type(text: str) -> str:
    if _TFSA_RE.search(text):
        return "tfsa"
    if _RRSP_RE.search(text):
        return "rrsp"
    if _RESP_RE.search(text):
        return "resp"
    return "margin"


def _split_currency_sections(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    cad_lines: list[str] = []
    usd_lines: list[str] = []
    current = cad_lines

    for line in lines:
        upper = line.upper()
        if "CDN. DOLLAR" in upper or "CANADIAN DOLLAR" in upper:
            current = cad_lines
        elif "U.S. DOLLAR" in upper or "US DOLLAR" in upper:
            current = usd_lines
        current.append(line)

    return "\n".join(cad_lines), "\n".join(usd_lines)


def _infer_activity(description: str) -> str:
    lower = description.lower()
    for key, val in _ACTIVITY_KEYWORDS.items():
        if key in lower:
            return val
    return "other"


def _extract_symbol(description: str) -> str | None:
    skip = {"BOUGHT", "SOLD", "BUY", "SELL", "CALL", "PUT", "FROM", "INTO"}
    for word in description.split():
        w = word.strip(".,():").lstrip(".")
        if w.isupper() and 1 < len(w) <= 5 and w.isalpha() and w not in skip:
            return w
    return None


def _try_parse_position_line(line: str, currency: str) -> RawPosition | None:
    tokens = line.split()
    numeric_suffix: list[str] = []
    for tok in reversed(tokens):
        if re.match(r"^\(?\$?[\d,]+\.?\d*%?\)?$", tok) and "." in tok:
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

    opt = parse_rbc_option(description)
    is_option = bool(opt) or any(k in description.upper() for k in ("CALL", "PUT"))
    symbol = opt.root if opt else (description.split()[0].lstrip(".") if description else None)

    return RawPosition(
        description=description,
        symbol=symbol,
        quantity=quantity or Decimal("0"),
        book_cost=None,
        market_price=market_price,
        market_value=market_value,
        currency=currency,
        asset_type="option" if is_option else "equity",
    )
