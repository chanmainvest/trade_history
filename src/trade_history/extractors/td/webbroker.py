"""TD WebBroker extractor — handles quarterly (2016-2022) and monthly (2023+) formats."""

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
    convert_pdf_via_docling,
    is_valid_account_id,
    parse_amount,
    parse_date_flexible,
    parse_quantity,
    parse_short_date,
    parse_td_option,
)

_HANDLE_SIG = "TD Direct Investing"
# Year-end resource packages (T5, T3 summary PDFs) are NOT trading statements
_SKIP_DOC_SIGS = ["Year-end Resource Package", "Resource Package", "Tax Package"]

# TD account IDs — checked in filename order before text search
_KNOWN_ACCOUNTS = ["77FF49", "58MRB0"]  # list preserves priority order
_ACCOUNT_RE = re.compile(r"\b([A-Z0-9]{6})\b")

# Period patterns (multiple TD formats over the years):
#   New monthly:  "For the period ending January 31, 2023"
#   Old 2016:     "Statement for January 1 to January 31, 2016"
#   Old 2017-18:  "September 1, 2017 to September 30, 2017"  (bare range, no prefix)
_PERIOD_ENDING_RE = re.compile(
    r"[Ff]or the period ending\s+([A-Za-z]+\.?\s+\d+,?\s+\d{4})"
)
# Matches "Statement for MON D to MON D, YYYY" OR bare "MON D, YYYY to MON D, YYYY"
_PERIOD_RANGE_RE = re.compile(
    r"(?:[Ss]tatement\s+for\s+)?"
    r"([A-Za-z]+\.?\s+\d{1,2},?\s*\d{0,4})"
    r"\s+to\s+"
    r"([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})"
)

# Transaction date: "Jan 30 ..." (short, no year)
_TX_DATE_RE = re.compile(r"^([A-Za-z]{3,9})\s+(\d{1,2})\s+(.+)$")

# Skip table header rows and balance lines
_SKIP_RE = re.compile(
    r"^(Date\b|Settlement\b|Description\b|Quantity\b|Price\b|Amount\b|"
    r"Balance\b|Total\b|Page\s+\d|Opening\b|Closing\b|Beginning\b|Cash\b|"
    r"Ending\s+cash\s+balance|Opening\s+cash\s+balance|Beginning\s+cash\s+balance)",
    re.IGNORECASE,
)

# Balance line: "Ending cash balance $142,000.00" or "Opening cash balance $50,000.00"
_TD_BALANCE_RE = re.compile(
    r"(Opening|Beginning|Ending|Closing)\s+cash\s+balance\s+\$?([\d,]+\.?\d*)",
    re.IGNORECASE,
)

# TD activity keywords — order matters: more specific substrings before general ones
_ACTIVITY_MAP = {
    "buy": "bought",
    "bought": "bought",
    "sell": "sold",
    "sold": "sold",
    "disposition": "sold",
    "acquisition": "bought",
    "dividend": "dividend",
    "distribution": "dividend",
    "interest": "interest",
    "transfer out": "transfer_out",   # must precede "transfer" to avoid mis-mapping
    "transfer_out": "transfer_out",
    "transfer": "transfer_in",
    "tsf fr": "transfer_in",
    "tsf to": "transfer_out",
    "journal entry": "journalled",
    "reinvest": "reinvestment",
    "contribution": "contribution",
    "deposit": "contribution",
    "web banking deposit": "contribution",
    "withdrawal": "withdrawal",
    "cheque issued": "withdrawal",
    "fee": "fee",
    "exercise": "exercise",
    "assign": "assignment",
    "expir": "expired",
    "withholding": "withholding_tax",
    "journalled": "journalled",
    "exchange": "exchange",
    "stock split": "stock_split",
    "reverse split": "stock_split",
    "stock exchange": "exchange",
    "adjustment": "adjustment",
    "goodwill": "adjustment",
    "return of capital": "return_of_capital",
    "defunct": "adjustment",
    "conversion": "fx_conversion",
    "cil": "cash_in_lieu",
    "cash in lieu": "cash_in_lieu",
    "name chg": "name_change",
    "security position": "adjustment",
}

_QUARTERLY_THRESHOLD = 45  # days


@ExtractorRegistry.register
class TDWebbroker(StatementExtractor):
    INSTITUTION = "TD"

    @classmethod
    def can_handle(cls, pdf_path: Path, first_page_text: str) -> bool:
        if _HANDLE_SIG not in first_page_text:
            return False
        # Reject year-end tax/resource packages — not trading statements
        if any(sig in first_page_text for sig in _SKIP_DOC_SIGS):
            return False
        return True

    def extract(
        self, pdf_path: Path
    ) -> Iterator[tuple[RawStatement, list[RawTransaction], list[RawPosition]]]:
        full_text, self._docling_dict = convert_pdf_via_docling(pdf_path)

        # Account ID: filename takes priority over text (avoids cross-account confusion)
        account_id = _extract_account_id(full_text, pdf_path)
        period_start, period_end = _extract_period(full_text)
        is_quarterly = (period_end - period_start).days > _QUARTERLY_THRESHOLD

        cad_text, usd_text = _split_currency_sections(full_text)

        all_txs: list[RawTransaction] = []
        all_positions: list[RawPosition] = []
        cad_txs, cad_ob, cad_cb = self._parse_transactions(cad_text, "CAD", period_end, is_quarterly)
        usd_txs, usd_ob, usd_cb = self._parse_transactions(usd_text, "USD", period_end, is_quarterly)
        all_txs.extend(cad_txs)
        all_txs.extend(usd_txs)
        all_positions.extend(self._parse_positions(cad_text, "CAD"))
        all_positions.extend(self._parse_positions(usd_text, "USD"))

        # Use CAD balance as primary; USD stored separately
        opening_balance = cad_ob
        closing_balance = cad_cb

        stmt = RawStatement(
            institution="TD",
            account_id=account_id,
            account_type=_detect_account_type(full_text),
            primary_currency="CAD",
            period_start=period_start,
            period_end=period_end,
            source_file=pdf_path,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
        )
        yield stmt, all_txs, all_positions

    def _parse_transactions(
        self, text: str, currency: str, period_end: date, is_quarterly: bool
    ) -> tuple[list[RawTransaction], Decimal | None, Decimal | None]:
        """Parse transactions and return (transactions, opening_balance, closing_balance)."""
        transactions: list[RawTransaction] = []
        period_year = period_end.year
        period_month = period_end.month
        opening_balance: Decimal | None = None
        closing_balance: Decimal | None = None

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or _SKIP_RE.match(stripped):
                # Capture balance from skip lines
                bm = _TD_BALANCE_RE.search(stripped)
                if bm:
                    bal_type, bal_val = bm.groups()
                    bal = parse_amount(bal_val)
                    if bal_type.lower() in ("opening", "beginning"):
                        opening_balance = bal
                    else:
                        closing_balance = bal
                continue

            m = _TX_DATE_RE.match(stripped)
            if not m:
                continue

            mon_str, day_str, rest = m.groups()

            # Check if rest is a balance line (e.g., "Jan 31 Ending cash balance $142,000")
            bm = _TD_BALANCE_RE.search(rest)
            if bm:
                bal_type, bal_val = bm.groups()
                bal = parse_amount(bal_val)
                if bal_type.lower() in ("opening", "beginning"):
                    opening_balance = bal
                else:
                    closing_balance = bal
                continue

            tx_date = parse_short_date(mon_str, int(day_str), period_year, period_month)
            if not tx_date:
                continue

            tokens = rest.split()

            # TD monthly statements have 7 columns — last column is running cash balance.
            # We skip the running balance (rightmost numeric when there are ≥4 numerics).
            numeric_end: list[str] = []
            for tok in reversed(tokens):
                if re.match(r"^\(?\-?\$?[\d,]+\.?\d*\)?$", tok):
                    numeric_end.insert(0, tok)
                else:
                    break

            if not numeric_end:
                continue

            # In 7-column layout: qty, price, commission, amount, balance
            # We want amount = numeric_end[-2] and skip balance = numeric_end[-1]
            # But we only do this when we have ≥4 trailing numerics (indicates balance col)
            effective_numerics = numeric_end
            if len(numeric_end) >= 4 and not is_quarterly:
                # Drop the rightmost (running balance)
                effective_numerics = numeric_end[:-1]

            amount = parse_amount(effective_numerics[-1])
            quantity: Decimal | None = None
            price: Decimal | None = None
            if len(effective_numerics) >= 3:
                quantity = parse_quantity(effective_numerics[-3])
                price = parse_quantity(effective_numerics[-2])

            description = " ".join(tokens[: len(tokens) - len(numeric_end)]).strip()
            if not description:
                continue

            activity = _infer_activity(description)
            opt = parse_td_option(description)
            symbol: str | None = opt.root if opt else _extract_symbol(description)

            transactions.append(
                RawTransaction(
                    date=tx_date,
                    activity=activity,
                    description=description,
                    amount=amount,
                    currency=currency,
                    raw_text=stripped,
                    symbol=symbol,
                    quantity=quantity,
                    price=price,
                )
            )
        return transactions, opening_balance, closing_balance

    def _parse_positions(self, text: str, currency: str) -> list[RawPosition]:
        positions: list[RawPosition] = []
        pos_re = re.compile(
            r"^([A-Z]{1,5}(?:-[A-Z0-9]+)?)\s+(.+?)\s+([\d,]+\.?\d*)\s+\$?([\d,]+\.?\d*)\s+\$?([\d,]+\.?\d*)\s*$"
        )
        for line in text.splitlines():
            m = pos_re.match(line.strip())
            if not m:
                continue
            symbol, desc, qty_str, price_str, mv_str = m.groups()
            qty = parse_quantity(qty_str)
            if not qty:
                continue

            opt = parse_td_option(f"{symbol} {desc}")
            is_option = bool(opt) or any(k in desc.upper() for k in ("CALL", "PUT"))
            asset_type = "option" if is_option else "equity"

            positions.append(
                RawPosition(
                    description=desc.strip(),
                    symbol=symbol,
                    quantity=qty,
                    book_cost=None,
                    market_price=parse_amount(price_str),
                    market_value=parse_amount(mv_str),
                    currency=currency,
                    asset_type=asset_type,
                )
            )
        return positions


# ── Module-level helpers ───────────────────────────────────────────────────────

def _extract_account_id(text: str, pdf_path: Path) -> str:
    """Filename takes priority to avoid cross-account reference confusion."""
    stem = pdf_path.stem.upper()
    # Check filename first (deterministic order via list)
    for known in _KNOWN_ACCOUNTS:
        if known in stem:
            return known
    # Then check document text
    for known in _KNOWN_ACCOUNTS:
        if known in text:
            return known
    # Generic 6-char alphanumeric fallback
    for m in _ACCOUNT_RE.finditer(text):
        candidate = m.group(1)
        if is_valid_account_id(candidate) and not candidate.isdigit():
            return candidate
    return "UNKNOWN"


def _extract_period(text: str) -> tuple[date, date]:
    # New monthly format: "For the period ending January 31, 2023"
    m_end = _PERIOD_ENDING_RE.search(text)
    if m_end:
        pe = parse_date_flexible(m_end.group(1).replace(".", ""))
        if pe:
            ps = date(pe.year, pe.month, 1)
            return ps, pe

    # Old formats: "Statement for January 1 to January 31, 2016"
    #          or  "September 1, 2017 to September 30, 2017"
    m_range = _PERIOD_RANGE_RE.search(text)
    if m_range:
        start_raw = m_range.group(1).strip().replace(".", "")
        end_raw = m_range.group(2).strip().replace(".", "")
        pe = parse_date_flexible(end_raw)
        if pe:
            ps = parse_date_flexible(start_raw)
            if not ps and pe:
                # Start has no year (e.g. "January 1") — borrow year from end date
                ps = parse_date_flexible(f"{start_raw} {pe.year}")
            if ps:
                return ps, pe

    return date(2000, 1, 1), date(2000, 1, 1)


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
        if "DIRECT TRADING - CDN" in upper or "CDN ACCOUNT" in upper or "CANADIAN DOLLAR" in upper:
            current = cad_lines
        elif "DIRECT TRADING - US" in upper or "US ACCOUNT" in upper or "U.S. DOLLAR" in upper:
            current = usd_lines
        current.append(line)

    return "\n".join(cad_lines), "\n".join(usd_lines)


def _infer_activity(description: str) -> str:
    lower = description.lower()
    for key, val in _ACTIVITY_MAP.items():
        if key in lower:
            return val
    return "other"


def _extract_symbol(description: str) -> str | None:
    skip = {"CALL", "PUT", "THE", "AND", "FOR", "CDN", "USD"}
    for word in description.split():
        w = word.strip(".,():-")
        if w.isupper() and 1 < len(w) <= 5 and w.isalpha() and w not in skip:
            return w
    return None
