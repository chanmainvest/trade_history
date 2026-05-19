"""HSBC InvestDirect extractor — yields CAD sub-account then USD sub-account.

HSBC PDFs are rendered without spaces between words (e.g. "Statementperiod",
"Jan30", "OpeningBalance"). All patterns must account for this.
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
    convert_pdf_via_docling,
    is_valid_account_id,
    parse_amount,
    parse_date_flexible,
    parse_hsbc_option,
    parse_quantity,
    parse_short_date,
    parse_td_option,
)

_HANDLE_SIG = "HSBC InvestDirect"

# Account IDs: "6Y-6HF9-E" (CAD) and "6Y-6HF9-F" (USD)
# Also supports bare form "6Y-6HF9" in fee files
_ACCOUNT_RE = re.compile(r"\b([A-Z0-9]{2}-[A-Z0-9]{4}(?:-[EF])?)\b")

# Period: "Statementperiod January1,2023toJanuary31,2023"
# Words run together in HSBC PDFs; spaces are optional between parts
_PERIOD_RE = re.compile(
    r"[Ss]tatement\s*[Pp]eriod\s+"
    r"([A-Za-z]+)\s*(\d+),\s*(\d{4})\s*to\s*([A-Za-z]+)\s*(\d+),\s*(\d{4})"
)

# Transaction date — pdfplumber has NO SPACE ("Jan30"), docling adds space ("Jan 30")
_TX_DATE_RE = re.compile(r"^([A-Za-z]{3,})\s*(\d{1,2})\s+(.+)$")

# HSBC activity verbs
_ACTIVITY_MAP = {
    "bought": "bought",
    "buy": "bought",
    "sold": "sold",
    "sell": "sold",
    "dividend": "dividend",
    "income dist": "dividend",
    "interest": "interest",
    "transfer": "transfer_in",
    "internal tfr": "transfer_in",
    "withdrawal": "withdrawal",
    "deposit": "contribution",
    "fee": "fee",
    "exercise": "exercise",
    "assignment": "assignment",
    "assigned": "assignment",
    "expiry": "expired",
    "expired": "expired",
    "expire": "expired",
    "eps": "withdrawal",
    "non-res tax": "withholding_tax",
    "convert$": "fx_conversion",
    "refund": "adjustment",
}

# Skip these lines inside the activity table (also checked on the "rest" after date prefix)
_SKIP_RE = re.compile(
    r"^(OpeningBalance|ClosingBalance|Closing\s*Balance\s*after|"
    r"Opening\s+Balance|Closing\s+Balance|"
    r"Datesettled|Date\s+settled|Transaction|"
    r"Pending\s*[Tt]ransactions|PendingTransactions)",
    re.IGNORECASE,
)

# Balance line pattern — captures amount from "OpeningBalance $X" or "ClosingBalance $X"
_BALANCE_RE = re.compile(
    r"(Opening|Closing)\s*Balance\s*\$?([\d,]+\.?\d*)",
    re.IGNORECASE,
)


@ExtractorRegistry.register
class HSBCInvestDirect(StatementExtractor):
    INSTITUTION = "HSBC"

    @classmethod
    def can_handle(cls, pdf_path: Path, first_page_text: str) -> bool:
        # Fee/performance report files detected by filename
        if "hsbc" in pdf_path.name.lower() and "fee" in pdf_path.name.lower():
            return True
        return _HANDLE_SIG in first_page_text

    def extract(
        self, pdf_path: Path
    ) -> Iterator[tuple[RawStatement, list[RawTransaction], list[RawPosition]]]:
        full_text, self._docling_dict = convert_pdf_via_docling(pdf_path)

        # Find account IDs
        account_ids: list[str] = []
        for m in _ACCOUNT_RE.finditer(full_text):
            aid = m.group(1)
            if is_valid_account_id(aid) and aid not in account_ids:
                account_ids.append(aid)

        # Parse period
        period_start, period_end = _extract_period(full_text)

        is_fee_file = "fee" in pdf_path.name.lower()

        if is_fee_file:
            cad_id = _account_id_from_fee_filename(pdf_path)
            if not cad_id:
                # Try to find from account IDs
                cad_id = next((a for a in account_ids if a.endswith("-E")), None)
            if not cad_id and account_ids:
                cad_id = account_ids[0]
            if not cad_id:
                cad_id = "UNKNOWN-E"
            stmt = RawStatement(
                institution="HSBC",
                account_id=cad_id,
                account_type="margin",
                primary_currency="CAD",
                period_start=period_start,
                period_end=period_end,
                source_file=pdf_path,
            )
            # Performance/fee report files have no transaction data
            yield stmt, [], []
            return

        # Normal statement: split into CAD (-E) and USD (-F) sections
        cad_text, usd_text = _split_currency_sections(full_text)

        for section_text, suffix, currency in [
            (cad_text, "-E", "CAD"),
            (usd_text, "-F", "USD"),
        ]:
            if not section_text.strip():
                continue

            aid = next(
                (a for a in account_ids if a.endswith(suffix)),
                (account_ids[0] if account_ids else f"UNKNOWN{suffix}"),
            )

            txs, ob, cb = self._parse_transactions(section_text, currency, period_end)
            positions = self._parse_positions(section_text, currency)
            stmt = RawStatement(
                institution="HSBC",
                account_id=aid,
                account_type="margin",
                primary_currency=currency,
                period_start=period_start,
                period_end=period_end,
                source_file=pdf_path,
                opening_balance=ob,
                closing_balance=cb,
            )
            yield stmt, txs, positions

    def _parse_transactions(
        self, text: str, currency: str, period_end: date
    ) -> tuple[list[RawTransaction], Decimal | None, Decimal | None]:
        """Parse transactions and return (transactions, opening_balance, closing_balance)."""
        transactions: list[RawTransaction] = []
        period_year = period_end.year
        period_month = period_end.month
        in_activity = False
        in_pending = False
        opening_balance: Decimal | None = None
        closing_balance: Decimal | None = None

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            upper = stripped.upper()

            # Detect activity section
            if "ACCOUNT ACTIVITY" in upper or "ACCOUNTACTIVITY" in upper:
                in_activity = True
                in_pending = False
                continue

            # Detect pending transactions section — skip these (they settle next month)
            if "PENDING" in upper and "TRANSACTION" in upper:
                in_pending = True
                continue

            # Exit at "Details of holdings" or "Total Holdings"
            if "DETAILS OF HOLDINGS" in upper or "TOTALHOLDINGS" in upper:
                in_activity = False

            if not in_activity or in_pending:
                continue

            # Skip table header / balance lines
            if _SKIP_RE.match(stripped):
                # Capture balance values before skipping
                bm = _BALANCE_RE.search(stripped)
                if bm:
                    bal_type, bal_val = bm.groups()
                    bal = parse_amount(bal_val)
                    if "opening" in bal_type.lower():
                        opening_balance = bal
                    else:
                        closing_balance = bal
                continue

            # Match "Jan30 Activity Description..." (no space between month and day)
            m = _TX_DATE_RE.match(stripped)
            if not m:
                continue

            mon_str, day_str, rest = m.groups()

            # After extracting date, check if rest is a balance line
            # (e.g., "Dec31 ClosingBalance $9,907.26" — date parsed, rest = "ClosingBalance $9,907.26")
            if _SKIP_RE.match(rest.strip()):
                bm = _BALANCE_RE.search(rest)
                if bm:
                    bal_type, bal_val = bm.groups()
                    bal = parse_amount(bal_val)
                    if "opening" in bal_type.lower():
                        opening_balance = bal
                    else:
                        closing_balance = bal
                continue

            tx_date = parse_short_date(mon_str, int(day_str), period_year, period_month)
            if not tx_date:
                continue

            tx = _parse_tx_rest(rest, tx_date, currency, stripped)
            if tx:
                transactions.append(tx)

        return transactions, opening_balance, closing_balance

    def _parse_positions(self, text: str, currency: str) -> list[RawPosition]:
        positions: list[RawPosition] = []
        # Look for holdings detail section
        in_holdings = False

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            upper = stripped.upper()

            if "DETAILS OF HOLDINGS" in upper or "DETAILSOFHOLDINGS" in upper:
                in_holdings = True
                continue
            if "ACCOUNT ACTIVITY" in upper or "ACCOUNTACTIVITY" in upper:
                in_holdings = False

            if not in_holdings:
                continue

            # Position lines: description + qty + [SEG] + price + book_cost + market_value + %
            # HSBC format: "SPDRBLM3-12MT-BILLETF 1,000 S 99.570 99,356.88 99,570.00 90.6"
            pos = _try_parse_position_line(stripped, currency)
            if pos:
                positions.append(pos)

        return positions


# ── Module-level helpers ───────────────────────────────────────────────────────

def _extract_period(text: str) -> tuple[date, date]:
    """Extract period from HSBC no-space format."""
    m = _PERIOD_RE.search(text)
    if m:
        s_mon, s_day, s_yr, e_mon, e_day, e_yr = m.groups()
        try:
            ps = parse_date_flexible(f"{s_mon} {s_day}, {s_yr}")
            pe = parse_date_flexible(f"{e_mon} {e_day}, {e_yr}")
            if ps and pe:
                return ps, pe
        except Exception:
            pass
    return date(2000, 1, 1), date(2000, 1, 1)


def _parse_tx_rest(
    rest: str, tx_date: date, currency: str, raw_text: str
) -> RawTransaction | None:
    """Parse the portion of an HSBC transaction line after the date."""
    # Strip trailing reference numbers (e.g. "2023061620003662")
    tokens = rest.split()
    if tokens and re.match(r"^\d{13,}$", tokens[-1]):
        tokens = tokens[:-1]

    numeric_end: list[str] = []
    for tok in reversed(tokens):
        if re.match(r"^\(?-?\$?[\d,]+\.?\d*\)?$", tok):
            numeric_end.insert(0, tok)
        else:
            break

    if not numeric_end:
        return None

    description = " ".join(tokens[: len(tokens) - len(numeric_end)]).strip()
    if not description:
        return None

    # Determine activity from first word or keywords
    activity = "other"
    lower_desc = description.lower()
    for key, val in _ACTIVITY_MAP.items():
        if key in lower_desc:
            activity = val
            break

    # Detect option early to handle quantity-only lines
    opt = parse_td_option(description) or parse_hsbc_option(description)

    amount = Decimal("0")
    quantity: Decimal | None = None
    price: Decimal | None = None

    if opt and len(numeric_end) == 1:
        # Option line with only quantity (e.g. "Expire PUT -100 OTEX'23 JN@42  20")
        quantity = parse_quantity(numeric_end[0])
    elif len(numeric_end) >= 3:
        quantity = parse_quantity(numeric_end[-3])
        price = parse_quantity(numeric_end[-2])
        amount = parse_amount(numeric_end[-1])
    elif len(numeric_end) == 2:
        price = parse_quantity(numeric_end[-2])
        amount = parse_amount(numeric_end[-1])
    else:
        amount = parse_amount(numeric_end[-1])

    # Symbol extraction
    symbol: str | None = None
    if opt:
        symbol = opt.root
        # Only override activity for non-expire/assignment cases
        if activity not in ("expired", "assignment"):
            activity = "bought" if (quantity or Decimal(0)) > 0 else "sold"
    else:
        for word in description.split():
            w = word.strip(".,():-")
            if w.isupper() and 1 < len(w) <= 6 and w.isalpha():
                symbol = w
                break

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


def _try_parse_position_line(line: str, currency: str) -> RawPosition | None:
    tokens = line.split()
    numeric_suffix: list[str] = []
    for tok in reversed(tokens):
        # Skip "S" (segregated marker) and percentage
        if re.match(r"^[\d,.()-]+$", tok) and ("." in tok or tok.replace(",", "").isdigit()):
            numeric_suffix.insert(0, tok)
        else:
            break

    if len(numeric_suffix) < 2:
        return None

    description = " ".join(tokens[: len(tokens) - len(numeric_suffix)]).strip()
    # Remove trailing "S" (segregated) marker
    description = re.sub(r"\s+S$", "", description).strip()
    if not description or len(description) < 2:
        return None

    try:
        market_value = parse_amount(numeric_suffix[-1])
        market_price = parse_amount(numeric_suffix[-2]) if len(numeric_suffix) >= 2 else None
        quantity = parse_quantity(numeric_suffix[-3]) if len(numeric_suffix) >= 3 else None
    except Exception:
        return None

    opt = parse_td_option(description) or parse_hsbc_option(description)
    if opt or any(k in description.upper() for k in ("CALL", "PUT")):
        asset_type = "option"
        symbol: str | None = opt.root if opt else None
    else:
        asset_type = "equity"
        symbol = description.split()[0].strip(".,()") if description else None

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


def _account_id_from_fee_filename(pdf_path: Path) -> str | None:
    """Extract account ID from HSBC fee filename.

    "hsbc_6y6hf9_2023_fees.pdf" -> "6Y-6HF9" (CAD sub-account, no suffix)
    """
    stem = pdf_path.stem.lower()
    # Look for 6-char alphanumeric segment
    m = re.search(r"_([a-z0-9]{6})_", stem)
    if m:
        s = m.group(1).upper()
        candidate = f"{s[:2]}-{s[2:]}"
        if is_valid_account_id(candidate):
            return candidate
    return None


def _split_currency_sections(text: str) -> tuple[str, str]:
    """Split HSBC full text into CAD and USD sections.

    HSBC statements use "Your Canadian Margin Account" and
    "Your USD Margin Account" as section headers. Words may run
    together without spaces due to PDF rendering.
    """
    lines = text.splitlines()
    cad_lines: list[str] = []
    usd_lines: list[str] = []
    current = cad_lines  # default to CAD

    for line in lines:
        upper = line.upper().replace(" ", "")
        # "YourCanadianMarginAccount" or "CANADIANMARGINACCOUNT"
        if "CANADIANMARGINACCOUNT" in upper or "CANADIANDOLLAR" in upper:
            current = cad_lines
        # "YourUSDMarginAccount" or "USDMARGINACCOUNT"
        elif "USDMARGINACCOUNT" in upper or "USDOLLAR" in upper:
            current = usd_lines
        current.append(line)

    return "\n".join(cad_lines), "\n".join(usd_lines)
