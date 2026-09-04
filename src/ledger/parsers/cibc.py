"""CIBC parser.

Covers three folder variants that share the base "CIBC e-Statement" layout:

- "CIBC Imperial Service"  → "Imperial Investor Service"
- "CIBC Invest Direct"     → "Investor's Edge Investment Account"
- "CIBC TSFA"              → "Investor's Edge Self-Directed Tax Free Savings Account"

Common structure:
    <Header / Account # NNN-NNNNN / Period "Month D-Month D, YYYY">
    Account Activity — Canadian Dollars
        <date> <activity> <description...> <qty> <price> <amount>
    Account Activity — U.S. Dollars
        ...
    Portfolio Assets — Canadian Dollars
        Cash & Cash Equivalents / Equities / Mutual Funds / Other (= options) / Fixed Income
    Portfolio Assets — U.S. Dollars
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..pdf_text import PdfText
from ..quantity import POSITION_AFFECTING_TYPES
from .helpers import (
    _option_mon,
    parse_money,
)
from .layout import (
    PageTextIndex,
    attach_source_spans,
    declare_snapshot_scopes,
    quarantine_unsupported_rows,
)
from .name_resolver import resolve_ticker, synthetic_symbol
from .registry import register
from .types import (
    ParsedAccount,
    ParsedCashBalance,
    ParsedInstrument,
    ParsedPosition,
    ParsedQuarantine,
    ParsedStatement,
    ParsedTxn,
    ParseResult,
    TxnType,
)

# ------------------------------------------------------------------- Regexes
RE_ACCOUNT_NUM   = re.compile(r"[Aa]ccount\s*#\s*(\d{3}[-–]\d{5})")
RE_PERIOD        = re.compile(
    r"\b([A-Z][a-z]+)\s+(\d{1,2})\s*[-–to]+\s*([A-Z][a-z]+)?\s*(\d{1,2})\s*,\s*(\d{4})"
)
RE_ACTIVITY_HDR  = re.compile(
    r"Account\s+Activity\s*[—–\-]\s*(Canadian|U\.S\.|US)\s*Dollars", re.IGNORECASE,
)
RE_PORTFOLIO_HDR = re.compile(
    r"Portfolio\s+Assets\s*[—–\-]\s*(Canadian|U\.S\.|US)\s*Dollars", re.IGNORECASE,
)
RE_DATE_PREFIX   = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2})\b")
RE_MONEY         = re.compile(r"-?\$?[\d,]+(?:\.\d+)?-?")
RE_PARENS_TICKER = re.compile(r"\(([A-Z0-9.\-]{1,10})/([A-Z]{2,6})\)")
RE_BARE_TICKER   = re.compile(r"\(([A-Z]{1,6})/(?:US|TSX|CDNX|NYSE|NASDAQ)\)")
RE_FILE_ACCT     = re.compile(r"(\d{3}[-]?\d{5})")

# Option position line in "Other" subsection of Portfolio Assets:
#   CALL .FNV MAR 15 2024 180   10  $4,119.45  2.000  $2,000.00  —
#   PUT .NGT JUN 21 2024 50    -20 -$8,268.05  2.750 -$5,500.00  —
# Adjusted roots may carry digits (SOXS1 after a reverse split).
RE_OPT_POS = re.compile(
    r"^(CALL|PUT)\s+\.?([A-Z][A-Z0-9]{0,5})\s+([A-Z]{3})\s+(\d{1,2})\s+(\d{4})\s+"
    r"(\d+(?:\.\d+)?)\s+(-?\d[\d,]*)\s+"
    r"(-?\$?[\d,]+\.\d+)\s+(\d+(?:\.\d+)?)\s+(-?\$?[\d,]+\.\d+)"
)

# Option txn line inside Account Activity:
#   Bought CALL .FNV MAR 15 2024 180 10 4.100 -$4,119.45
#   Sold   PUT  .NGT JUN 21 2024  50 -20 4.150 $8,268.05
RE_OPT_TXN = re.compile(
    r"\b(Bought|Sold|Expired|Exercised|Assignment|Assigned|Expire|Exercise|Assign|"
    r"Shrs in xc)\s+"
    r"(CALL|PUT)\s+\.?([A-Z][A-Z0-9]{0,5})\s+([A-Z]{3})\s+(\d{1,2})\s+(\d{4})\s+"
    r"(\d+(?:\.\d+)?)\s+(-?\d[\d,]*)\s+(\d+(?:\.\d+)?)\s+(-?\$?[\d,]+\.\d+)"
)

# Unpriced option events print the strike and, when present, the contract
# quantity followed by two blank-cell em dashes:
#   Expired PUT RIO DEC 17 2021 60 20 — —
#   Assignment PUT HL JUN 18 2026 22 2 — —
#   Shrs in xc CALL SOXS1 JAN 15 2027 35 -30 — —
# A row with only one number before the dashes contains a strike but no printed
# quantity; the optional group deliberately remains None in that case.
RE_OPT_EVENT = re.compile(
    r"\b(Expired|Exercised|Assignment|Assigned|Expire|Exercise|Assign|Shrs in xc)\s+"
    r"(CALL|PUT)\s+\.?([A-Z][A-Z0-9]{0,5})\s+([A-Z]{3})\s+(\d{1,2})\s+(\d{4})\s+"
    r"(\d+(?:\.\d+)?)(?:\s+(-?\d[\d,]*))?(?:\s+—){1,2}\s*$"
)

# In-kind transfers have a printed quantity but blank price/amount cells.
# Match the quantity immediately before those explicit blank markers.
RE_UNPRICED_QUANTITY = re.compile(
    r"(-?\d[\d,]*(?:\.\d+)?)(?:\s+—){1,2}\s*$"
)

# Page furniture repeats on every page: the statement's own period line
# ("July 1-July 31, 2026"), the previous-statement marker, and the page
# footer ("054697 account # 588-93738 page 2 of 5"). None of it is row data.
_RE_PERIOD_FURNITURE = re.compile(
    r"^\(?[A-Z][a-z]+ \d{1,2}(?:\s*[-–]\s*(?:[A-Z][a-z]+ )?\d{1,2})?,? \d{4}\)?$"
)
_RE_PREV_STATEMENT_FURNITURE = re.compile(
    r"^\(previous statement [A-Z][a-z]+ \d{1,2}, \d{4}\)$", re.IGNORECASE
)


def _is_page_furniture(s: str) -> bool:
    if "account #" in s.lower():
        return True
    return bool(_RE_PERIOD_FURNITURE.match(s) or _RE_PREV_STATEMENT_FURNITURE.match(s))


# Disclosure/legal boilerplate fragments from the page footer land inside the
# portfolio sections (wrapped mid-sentence by the text extractor). Matched by
# their fixed legal markers — never by amounts, which real rows carry.
_DISCLOSURE_MARKERS = (
    "gst/hst", "qst:", "annual fee", "conduct of our business",
    "we reserve the", "cibc world markets", "bay st",
)
_RE_POSTAL_CODE = re.compile(r"\b[A-Z]\d[A-Z] \d[A-Z]\d\b")
_RE_TOLL_FREE = re.compile(r"\b1-800-\d{3}-\d{4}\b")


def _is_disclosure_boilerplate(s: str) -> bool:
    low = s.lower()
    if any(marker in low for marker in _DISCLOSURE_MARKERS):
        return True
    return bool(_RE_POSTAL_CODE.search(s) or _RE_TOLL_FREE.search(s))


# Squeezed wire-confirmation footer fragments printed under large wire-
# settled fund redemptions — the wire reference ("1000THS=700,WIRE=AAMX0FL")
# and the gross-amount/transfer-fee pair ("GA=288662.60; TF=6.95"). Receipt
# furniture: no date prefix, no row shape, and the amounts repeat the
# settled row's proceeds rather than new events.
_RE_WIRE_REF_FURNITURE = re.compile(r"^\d+THS=\d+(?:\.\d+)?,WIRE=[A-Z0-9]+$")
_RE_WIRE_TOTAL_FURNITURE = re.compile(r"^GA=\$?[\d,]+(?:\.\d+)?;\s*TF=\$?[\d,]+(?:\.\d+)?$")


def _is_wire_confirmation_footer(s: str) -> bool:
    return bool(_RE_WIRE_REF_FURNITURE.match(s) or _RE_WIRE_TOTAL_FURNITURE.match(s))

# Stock txn (last three numbers on the activity line):
#   Bought RBB FD INC                    3,600  48.009  -$172,838.55
#   Sold NEWMONT CORPORATION            -2,000  37.484   $74,960.73
RE_STOCK_TAIL = re.compile(
    r"(-?\d[\d,]*(?:\.\d+)?)\s+(-?\$?[\d,]+(?:\.\d+)?)\s+(-?\$?[\d,]+(?:\.\d+)?)\s*$"
)


_MONTH_NAMES = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "Jun": 6, "Jul": 7, "Aug": 8,
    "Sep": 9, "Sept": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


@dataclass
class _Header:
    account_number: str
    period_start: str
    period_end: str
    account_type: str | None
    base_currency: str = "CAD"


def _detect_account_type(text: str) -> str | None:
    head = text[:4000]
    if "Tax Free Savings" in head or "Tax-Free Savings" in head:
        return "TFSA"
    if "Self-Directed RSP" in head or "Self-Directed RRSP" in head:
        return "RRSP"
    if "Self-Directed RIF" in head or "Self-Directed RRIF" in head:
        return "RRIF"
    if "Imperial Investor Service" in head:
        return "Imperial Investor"
    if "Investor's Edge Investment Account" in head:
        return "Cash"
    if "Investor's Edge" in head:
        return "Cash"
    return None


def _parse_period(text: str) -> tuple[str, str] | None:
    """Find the 'Month D-Month D, YYYY' or 'Month D-D, YYYY' header."""
    head = text[:4000]
    # 'August 1-August 31, 2022' or 'November 1-November 30, 2023'
    m = re.search(
        r"\b([A-Z][a-z]+)\s+(\d{1,2})\s*[-–]\s*([A-Z][a-z]+)?\s*(\d{1,2}),\s*(\d{4})",
        head,
    )
    if not m:
        return None
    m1, d1, m2, d2, yr = m.groups()
    mn1 = _MONTH_NAMES.get(m1)
    mn2 = _MONTH_NAMES.get(m2) if m2 else mn1
    if mn1 is None or mn2 is None:
        return None
    try:
        from datetime import date
        return (date(int(yr), mn1, int(d1)).isoformat(),
                date(int(yr), mn2, int(d2)).isoformat())
    except ValueError:
        return None


def _extract_account_number(text: str, relpath: str) -> str | None:
    m = RE_ACCOUNT_NUM.search(text)
    if m:
        return m.group(1).replace("–", "-")
    # filename fallback
    fm = RE_FILE_ACCT.search(relpath)
    if fm:
        token = fm.group(1)
        if "-" not in token and len(token) == 8:
            token = f"{token[:3]}-{token[3:]}"
        return token
    return None


def _is_tax_doc(text: str, relpath: str) -> bool:
    if "Tax-Document" in relpath or "tax-document" in relpath.lower():
        return True
    head = text[:3000]
    if "Trading Summary" in head and "Tax Year" in head:
        return True
    return False


def _split_sections(text: str) -> list[tuple[str, str, str]]:
    """Yield (kind, currency, body) for each Account Activity / Portfolio Assets block.

    "(continued)" suffixed headers are resumption markers of an already-open
    section — we skip them so that an option block that physically appears
    *after* an "Activity (continued)" header but *belongs* to Portfolio Assets
    is still attributed to the right section.
    """
    matches = []
    for m in RE_ACTIVITY_HDR.finditer(text):
        # Skip "(continued)" headers — see docstring.
        tail = text[m.end(): m.end() + 30]
        if "continued" in tail.lower():
            continue
        ccy = "CAD" if "Canadian" in m.group(0) else "USD"
        matches.append(("activity", ccy, m.start(), m.end()))
    for m in RE_PORTFOLIO_HDR.finditer(text):
        tail = text[m.end(): m.end() + 30]
        if "continued" in tail.lower():
            continue
        ccy = "CAD" if "Canadian" in m.group(0) else "USD"
        matches.append(("portfolio", ccy, m.start(), m.end()))
    matches.sort(key=lambda x: x[2])
    out: list[tuple[str, str, str]] = []
    for i, (kind, ccy, _start, end) in enumerate(matches):
        next_start = matches[i + 1][2] if i + 1 < len(matches) else len(text)
        out.append((kind, ccy, text[end:next_start]))
    return out


# ------------------------------------------------------------- Activity rows
_RE_OPTION_CALL_PUT = re.compile(r"\b(?:CALL|PUT)\b")
_RE_OPEN_CONTRACT = re.compile(r"\bOPEN\s+CONTRACT\b")


ACTIVITY_VERBS = {
    "Bought": "buy", "Sold": "sell",
    "Dividend": "dividend", "Distribution": "distribution",
    "Tax": "tax_withholding",
    "Interest": "interest_income",
    "Expired": "option_expiration", "Expire": "option_expiration",
    "Exercised": "option_exercise", "Exercise": "option_exercise",
    "Assigned": "option_assignment", "Assign": "option_assignment",
    "Assignment": "option_assignment",
    "Transfer": "transfer_in",  # direction inferred from amount sign
    "Journal": "journal",
    "Deposit": "deposit",
    "Withdrawal": "withdrawal", "Withdraw": "withdrawal",
    "Fee": "fee",
    "Adjustment": "adjustment",
    "Reinvest": "dividend", "Reinvested": "dividend",
    "Name Change": "name_change", "Symbol Change": "name_change",
    "Ticker Change": "name_change",
    "EFT DEBIT": "deposit",
    "Contrib": "transfer_in",
    # Corporate-action shares/contracts swapped for a successor security;
    # printed quantity with blank price/amount cells (e.g. an adjusted
    # option root after a reverse split, ±N with the same printed identity).
    "Shrs in xc": "journal",
    # Merger legs print qty, a blank price cell, and a signed cash value.
    "Merger": "merger",
}


def _activity_year(period_end: str) -> int:
    return int(period_end[:4])


def _classify_activity(verb: str, raw: str) -> TxnType | None:
    v = verb.strip()
    if v in ACTIVITY_VERBS:
        t = ACTIVITY_VERBS[v]
        # Refine option open/close on Bought/Sold; require CALL/PUT tokens and
        # an explicit OPEN CONTRACT phrase so issuer names like Open Text stay equity.
        if t in {"buy", "sell"} and _RE_OPTION_CALL_PUT.search(raw):
            is_open = _RE_OPEN_CONTRACT.search(raw) is not None
            if v == "Bought":
                return "option_buy_to_open" if is_open else "option_buy_to_close"
            return "option_sell_to_open" if is_open else "option_sell_to_close"
        return t
    return None


def _opt_expiry(month3: str, day: str, year: str) -> str | None:
    mn = _option_mon(month3)
    if mn is None:
        return None
    try:
        from datetime import date
        return date(int(year), mn, int(day)).isoformat()
    except ValueError:
        return None


def _make_option_instrument(root: str, expiry: str, strike: float,
                            cp: str, currency: str) -> ParsedInstrument:
    return ParsedInstrument(
        asset_type="option", symbol=root, currency=currency,
        option_root=root, option_expiry=expiry, option_strike=strike,
        option_type=cp, option_multiplier=100,
    )


def _instr_from_desc(desc: str, currency: str) -> ParsedInstrument:
    """Best-effort instrument extraction from a free-form description."""
    # Option-expiration / assignment rows that lack the OPT() ticker form
    # but spell it out:  "CALL SOXS JAN 16 2026 55.00" or
    # "PUT .TLT JAN 16 2026 75.00".
    om = re.match(
        r"^\s*(CALL|PUT)\s+\.?([A-Z][A-Z0-9.\-]{0,6})\s+"
        r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+"
        r"(\d{1,2})\s+(\d{4})\s+([\d.,]+)",
        desc.strip(), re.IGNORECASE,
    )
    if om:
        cp_word, root, mon, dd, yr, strike_s = om.groups()
        cp = "CALL" if cp_word.upper() == "CALL" else "PUT"
        _MON = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
        try:
            from datetime import date as _date
            expiry = _date(int(yr), _MON[mon.upper()], int(dd)).isoformat()
        except (KeyError, ValueError):
            expiry = None
        try:
            strike = float(strike_s.replace(",", ""))
        except ValueError:
            strike = 0.0
        return ParsedInstrument(
            asset_type="option", symbol=root.upper(), currency=currency,
            option_root=root.upper(), option_expiry=expiry,
            option_strike=strike, option_type=cp, option_multiplier=100,
            name=desc.strip()[:120],
        )
    om = re.match(
        r"^\s*(CALL|PUT)\s+\.?([A-Z][A-Z0-9.\-]{0,8})\s+"
        r"(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+"
        r"(\d{1,2})\s+(\d{4})\s*\|\s*([\d.,]+)",
        desc.strip(), re.IGNORECASE,
    )
    if om:
        cp_word, root, mon, dd, yr, strike_s = om.groups()
        cp = "CALL" if cp_word.upper() == "CALL" else "PUT"
        _MON = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
        try:
            from datetime import date as _date
            expiry = _date(int(yr), _MON[mon.upper()], int(dd)).isoformat()
        except (KeyError, ValueError):
            expiry = None
        try:
            strike = float(strike_s.replace(",", ""))
        except ValueError:
            strike = None
        return ParsedInstrument(
            asset_type="option", symbol=root.upper(), currency=currency,
            option_root=root.upper(), option_expiry=expiry,
            option_strike=strike, option_type=cp, option_multiplier=100,
            name=desc.strip()[:120],
        )
    om = re.match(r"^\s*(CALL|PUT)\s+\.?([A-Z][A-Z0-9.\-]{0,8})\b", desc.strip(), re.IGNORECASE)
    if om:
        cp_word, root = om.groups()
        cp = "CALL" if cp_word.upper() == "CALL" else "PUT"
        clean_root = root.upper().rstrip("0123456789") if "ADJ" in desc.upper() else root.upper()
        return ParsedInstrument(
            asset_type="option", symbol=clean_root, currency=currency,
            option_root=clean_root, option_type=cp, option_multiplier=100,
            name=desc.strip()[:120],
        )
    m = RE_PARENS_TICKER.search(desc)
    if m:
        sym, exch = m.group(1), m.group(2)
        atype = "etf" if " ETF" in desc.upper() else "equity"
        if "FUND" in desc.upper() or "MUTUAL" in desc.upper():
            atype = "mutual_fund"
        return ParsedInstrument(
            asset_type=atype, symbol=sym, currency=currency,
            exchange=exch, name=desc.strip()[:120],
        )
    known = resolve_ticker(desc, currency)
    if known is not None:
        ticker, asset_type = known
        return ParsedInstrument(
            asset_type=asset_type, symbol=ticker, currency=currency,
            name=desc.strip()[:120],
        )
    # mutual funds at CIBC often have no ticker; build a synthetic name-symbol.
    # Strip trailing quantity / arrow / percent fragments before underscoring,
    # so we don't end up with garbage like "ARM_HOLDINGS_PLC_-2,000_↑↑".
    cleaned = desc.strip().upper()
    cleaned = re.sub(r"[↑↓\u2191\u2193]+", "", cleaned)
    cleaned = re.sub(r"\s[-+]?\d[\d,]*\.?\d*\s*$", "", cleaned)  # trailing qty
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    sym = synthetic_symbol(cleaned, max_len=40)
    atype = "mutual_fund" if "FUND" in desc.upper() else "equity"
    return ParsedInstrument(
        asset_type=atype, symbol=sym, currency=currency, name=desc.strip()[:120],
        resolution_method="unresolved_printed_identity",
        resolution_confidence=0.0,
    )


# ------------------------------------------------------------- Activity parse
# ------------------------------------------------------- Wrapped name lines
_CONTINUATION_STOP_PREFIXES = (
    "date activity", "subtotal", "total ", "account #", "disclosures",
    "hri-", "reference",
)
_CONTINUATION_STOP_HEADERS = {
    "Equities", "Mutual Funds", "Other", "Cash & Cash Equivalents",
    "Fixed Income", "Bonds",
}


# Digit-bearing note lines CIBC prints under an activity or portfolio row.
# They carry row-local evidence — the transfer source/destination account,
# dividend record/pay dates, share counts, distribution counts, DRIP
# reinvest prices, corporate-action ratios, and printed value/par text —
# and can fuse with the wrapped name's last word ("CORP COM CASH DIV ON
# 3500 SHS"). A row itself never starts with these keywords: activity rows
# begin with a Mon DD date prefix and portfolio rows end in a numeric
# column tail.
#
# The option-assignment alternatives cover the underlying-security CUSIP
# wrap ("HECLA MINING CO A/E 9GDQHF6 2", split as "HECLA MINING CO" +
# "A/E 9GDQHF6 35") and the bare contract echo CIBC reprints under
# "ASSIGNMENT OF OPTION" note lines ("PUT HL JUN 18 2026 22"). Neither
# shape is ever a row on its own: a real option row carries a date prefix
# (guarded below) or holding columns after the strike.
_RE_REINVEST_ALT = r"REINVEST(?:ED)?\.?\s+(?:DIV\s*@|[A-Z]{3})"
RE_ROW_NOTE = re.compile(
    r"TRANSFER\s+TO\s*\d"
    r"|TRANSFER\s+FROM\s*\d"
    r"|REC\s+[A-Z]{3}\.?\s+\d"
    r"|PAY\s+[A-Z]{3}\.?\s+\d"
    r"|CASH DIV ON\s+\d"
    r"|DIST ON\s+\d"
    r"|" + _RE_REINVEST_ALT +
    r"|^\s*@\s*[\d,.]+\s*$"
    r"|\bFA\s*\d+\s*:\s*\d"
    r"|\bADJ\s*\d+\s*:\s*\d"
    r"|VALUE\s*\$"
    r"|\bA/E\s+[A-Z0-9]{5,9}\s+\d"
    r"|^(?:CALL|PUT)\s+\.?[A-Z][A-Z0-9]{0,5}\s+[A-Z]{3}\s+\d{1,2}\s+\d{4}\s+\d+(?:\.\d+)?$"
)
# Reinvested-dividend note text: "REINVESTED DIV @ 15.7635" under a
# dividend row, or the squeezed legacy "REINVEST. JAN 29 2016 @ 9.0419"
# shape. Marks a share reinvestment with no cash cell.
RE_REINVEST_NOTE = re.compile(_RE_REINVEST_ALT)


def _is_name_continuation(s: str) -> bool:
    """True when `s` is a wrapped-name/ticker line belonging to the row above.

    CIBC wraps long security names and prints an explicit ``(SYM/EXCH)``
    ticker line under the row they identify. Continuation lines carry no
    ``Mon DD`` date prefix and no digits, except the keyword-anchored note
    sub-lines matched by ``RE_ROW_NOTE``. The date-prefix guard runs first:
    a dated data row that happens to contain a note keyword (a cash
    transfer printing ``Transfer TRANSFER FROM 588-58964-22``) is a row,
    never a continuation.
    """
    if not s or s == "$$":
        return False
    if RE_DATE_PREFIX.match(s):
        return False
    if RE_ROW_NOTE.search(s):
        return True
    if re.search(r"[0-9]", s):
        return False
    if RE_DATE_PREFIX.match(s):
        return False
    low = s.lower()
    if low.startswith(_CONTINUATION_STOP_PREFIXES):
        return False
    if "account activity" in low or "portfolio assets" in low:
        return False
    if "opening cash balance" in low or "closing cash balance" in low:
        return False
    return s not in _CONTINUATION_STOP_HEADERS


def _take_name_continuations(lines: list[str], i: int) -> tuple[list[str], int]:
    """Consume the continuation lines directly following a parsed row.

    Returns the stripped continuation texts and the new index. Blank lines
    are skipped over; the run stops at the first non-continuation line.
    """
    out: list[str] = []
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if not _is_name_continuation(s):
            break
        out.append(s)
        i += 1
    return out, i


def _parse_activity_block(body: str, *, currency: str, year: int,
                          stmt: ParsedStatement) -> bool:
    lines = body.splitlines()
    i = 0
    opening_balance: float | None = None
    cash_lines: list[str] = []
    cash_complete = False
    cash_uncertain = False

    while i < len(lines):
        ln = lines[i].rstrip()
        s = ln.strip()
        i += 1
        if not s:
            continue

        # CIBC uses an en dash as a minus sign on some activity amounts.  Keep
        # the original line for provenance, but match against normalized text.
        match_s = s.replace("\u2013", "-").replace("\u2212", "-")

        # Stop at footer/section break markers.
        if s.startswith("account #") or s.startswith("Disclosures") or s.startswith("HRI-"):
            continue

        # Page furniture (period line, previous-statement marker, page footer)
        # repeats per page and is never row data.
        if _is_page_furniture(s):
            continue

        # Currency conversion footer of a USD activity block.
        if "Canadian dollar equivalent" in s or "U.S. equals" in s:
            continue

        # Squeezed wire-confirmation receipt fragments under wire-settled
        # redemptions: furniture, not activity.
        if _is_wire_confirmation_footer(s):
            continue

        # CAD-converted presentation total printed under that footer. FX
        # conversion is presentation-only and the native-currency closing
        # balance is already recorded from the dated closing-balance row.
        if s.startswith("Total closing cash balance in Canadian dollars"):
            continue

        # Detect a leading 'Mon DD' date.
        dm = RE_DATE_PREFIX.match(match_s)
        if dm:
            mon, day = dm.group(1), dm.group(2)
            try:
                from datetime import date
                trade_date = date(year, _MONTH_NAMES[mon], int(day)).isoformat()
            except (KeyError, ValueError):
                trade_date = None
            rest = match_s[dm.end():].strip()

            # Opening / Closing cash balance rows
            low = rest.lower()
            if "opening cash balance" in low:
                amt = parse_money(_last_money(rest))
                if amt is None:
                    stmt.quarantine.append(ParsedQuarantine(
                        raw_line=ln,
                        reason="opening cash balance has no valid amount",
                    ))
                else:
                    opening_balance = amt
                    cash_lines = [ln]
                continue
            if "closing cash balance" in low:
                amt = parse_money(_last_money(rest))
                if amt is None:
                    stmt.quarantine.append(ParsedQuarantine(
                        raw_line=ln,
                        reason="closing cash balance has no valid amount",
                    ))
                    continue
                # Update or insert closing balance for this currency.
                existing = next((c for c in stmt.cash_balances if c.currency == currency), None)
                if existing is not None:
                    existing.closing_balance = amt
                    existing.raw_line = "\n".join(
                        part for part in (existing.raw_line, ln) if part
                    )
                else:
                    stmt.cash_balances.append(
                        ParsedCashBalance(
                            currency=currency,
                            opening_balance=opening_balance,
                            closing_balance=amt,
                            raw_line="\n".join([*cash_lines, ln]),
                        )
                    )
                cash_complete = True
                continue

            # Try option transaction first.
            mo = RE_OPT_TXN.search(rest)
            if mo:
                verb, cp, root, mon3, dd, yr, strike_s, qty_s, price_s, amt_s = mo.groups()
                expiry = _opt_expiry(mon3, dd, yr)
                instr = _make_option_instrument(
                    root=root, expiry=expiry or "", strike=float(strike_s),
                    cp=cp, currency=currency,
                )
                qty = float(qty_s.replace(",", ""))
                txn_type = _classify_activity(verb, rest) or "buy"
                # if the verb didn't make it option_*, force based on cp
                if not txn_type.startswith("option_") and txn_type in {"buy", "sell"}:
                    txn_type = ("option_buy_to_open" if verb in {"Bought"} and qty > 0
                                else "option_sell_to_open" if verb in {"Sold"} and qty < 0
                                else "option_buy_to_close" if verb in {"Bought"} and qty < 0
                                else "option_sell_to_close")
                cont, i = _take_name_continuations(lines, i)
                stmt.transactions.append(ParsedTxn(
                    trade_date=trade_date or "", settle_date=None, txn_type=txn_type,
                    instrument=instr, quantity=qty, price=parse_money(price_s),
                    gross_amount=None, commission=None, other_fees=None,
                    net_amount=parse_money(amt_s), currency=currency,
                    description=" ".join([rest] + cont).strip(),
                    raw_line=ln,
                ))
                continue

            event = RE_OPT_EVENT.search(rest)
            if event:
                verb, cp, root, mon3, dd, yr, strike_s, qty_s = event.groups()
                expiry = _opt_expiry(mon3, dd, yr)
                instr = _make_option_instrument(
                    root=root, expiry=expiry or "", strike=float(strike_s),
                    cp=cp, currency=currency,
                )
                qty = parse_money(qty_s)
                if qty is None:
                    stmt.quarantine.append(ParsedQuarantine(
                        raw_line=ln,
                        reason="option event has no printed contract quantity",
                    ))
                cont, i = _take_name_continuations(lines, i)
                stmt.transactions.append(ParsedTxn(
                    trade_date=trade_date or "", settle_date=None,
                    txn_type=_classify_activity(verb, rest) or "option_expiration",
                    instrument=instr, quantity=qty, price=None,
                    gross_amount=None, commission=None, other_fees=None,
                    net_amount=None, currency=currency,
                    description=" ".join([rest] + cont).strip(), raw_line=ln,
                ))
                continue

            # Stock / dividend / fee / interest / merger line: extract trailing
            # numbers.
            verb_match = re.match(
                r"(EFT DEBIT|Contrib|Bought|Sold|Dividend|Distribution|Tax|Interest|Expired|Expire|"
                r"Exercised|Assigned|Assignment|Transfer|Journal|Deposit|Withdrawal|Fee|"
                r"Adjustment|Reinvested|Reinvest|Name Change|Symbol Change|"
                r"Ticker Change|Merger|Shrs in xc)\b",
                rest,
            )
            if verb_match:
                verb = verb_match.group(1)
                desc_and_nums = rest[verb_match.end():].strip()
                # An account reference like `TRANSFER FROM 588-58964-22` is
                # not a quantity: its journal suffix must not be read as a
                # negative amount by the number-tail extraction below. The
                # reference is restored into the stored description.
                acct_ref = re.search(r"\b\d{3}-\d{5}-\d{1,3}\b", desc_and_nums)
                if acct_ref:
                    desc_and_nums = desc_and_nums.replace(
                        acct_ref.group(0), "ACCTREF", 1)
                tail = RE_STOCK_TAIL.search(desc_and_nums)
                qty = price = amount = None
                desc = desc_and_nums
                if tail:
                    qty = parse_money(tail.group(1)) if tail.group(1) not in {"—", "-"} else None
                    price = parse_money(tail.group(2)) if tail.group(2) not in {"—", "-"} else None
                    amount = parse_money(tail.group(3)) if tail.group(3) not in {"—", "-"} else None
                    desc = desc_and_nums[:tail.start()].strip()
                else:
                    # Merger-style rows print qty, a blank price cell, and a
                    # signed cash value:  URANIUM ROYALTY CORP -15,000 — $41,700.00
                    dash = re.search(
                        r"(-?\d[\d,]*(?:\.\d+)?)\s+(?:—|-)\s+(-?\$?[\d,]+(?:\.\d+)?)\s*$",
                        desc_and_nums,
                    )
                    if dash:
                        qty = parse_money(dash.group(1))
                        amount = parse_money(dash.group(2))
                        desc = desc_and_nums[:dash.start()].strip()
                    else:
                        unpriced = RE_UNPRICED_QUANTITY.search(desc_and_nums)
                        if unpriced:
                            qty = parse_money(unpriced.group(1))
                            desc = desc_and_nums[:unpriced.start()].strip()
                        # 2-number tail (qty + amount, no price; common for dividends)
                        m2 = None if unpriced else re.search(
                            r"(-?\$?[\d,]+(?:\.\d+)?)\s+(-?\$?[\d,]+(?:\.\d+)?)\s*$",
                            desc_and_nums,
                        )
                        if m2:
                            # Heuristic: treat as price/amount missing, qty/amount.
                            q = m2.group(1)
                            a = m2.group(2)
                            amount = parse_money(a)
                            if q not in {"—", "-"}:
                                qty = parse_money(q)
                            desc = desc_and_nums[:m2.start()].strip()
                        elif not unpriced:
                            m1 = re.search(r"(-?\$?[\d,]+(?:\.\d+)?)\s*$", desc_and_nums)
                            if m1:
                                amount = parse_money(m1.group(1))
                                desc = desc_and_nums[:m1.start()].strip()

                # Wrapped name/ticker lines under the row identify the
                # security; merge them before resolving the instrument.
                if acct_ref:
                    desc = desc.replace("ACCTREF", acct_ref.group(0), 1)
                cont, i = _take_name_continuations(lines, i)
                if cont:
                    desc = " ".join([desc] + cont).strip()

                txn_type = _classify_activity(verb, rest)
                if txn_type is None:
                    stmt.quarantine.append((ln, f"unknown verb: {verb}"))
                    continue

                # A reinvested dividend prints shares and a reinvest price
                # with a blank cash cell — an in-kind reinvestment, not a
                # cash dividend. Retype so cash reconciliation treats it as
                # non-cash and the position replay books the shares. The
                # printed amount guards the retype: a cash dividend always
                # carries one.
                if (
                    txn_type == "dividend"
                    and amount is None
                    and qty is not None
                    and RE_REINVEST_NOTE.search(desc)
                ):
                    txn_type = "reinvest_dividend"

                # Direction sanity for transfers whose amount cell is blank but
                # quantity/name carries the outbound sign.
                if (txn_type == "transfer_in"
                    and ((amount is not None and amount < 0)
                         or (qty is not None and qty < 0)
                         or re.search(r"\s-\d[\d,]*(?:\.\d+)?\b", desc_and_nums))):
                    txn_type = "transfer_out"

                instr = _instr_from_desc(desc, currency) if desc else None
                # interest / fee rows have no instrument. A journal keeps its
                # identity attempt only when the description names a fund
                # (CIBC types such names mutual_fund): the residual-flatten
                # rows move units of a reviewed fund, while every other
                # journal is an account-internal cash movement.
                if txn_type in {"interest_income", "interest_expense", "fee",
                                "deposit", "withdrawal", "adjustment"}:
                    instr = None
                elif txn_type == "journal" and not (
                    instr is not None and instr.asset_type == "mutual_fund"
                ):
                    instr = None
                # Non-resident withholding prints a fixed label with no
                # security columns (the em dashes are the blank symbol
                # cells): a pure cash event, not an unresolved identity.
                if (txn_type == "tax_withholding"
                        and desc.upper().startswith("NON-RES TAX WITHHELD")):
                    instr = None
                # Account-to-account cash transfers print TO/FROM followed by
                # an account number. Those direction words are not tickers.
                if txn_type in {"transfer_in", "transfer_out"} and re.match(
                    r"^(?:TRANSFER\s+)?(?:TO|FROM)\s+\d", desc, re.IGNORECASE
                ):
                    instr = None

                # A verb-path position movement that keeps a quantity but no
                # resolvable identity (the mutual-fund residual flatten
                # "Shrs in xc 1000THS <FUND> -2 — —") is recorded with its
                # identity marked unresolved, not silently dropped.
                resolution_method = None
                if instr is None and qty is not None and txn_type in POSITION_AFFECTING_TYPES:
                    resolution_method = "unresolved_printed_identity"

                stmt.transactions.append(ParsedTxn(
                    trade_date=trade_date or "", settle_date=None, txn_type=txn_type,
                    instrument=instr, quantity=qty, price=price,
                    gross_amount=None, commission=None, other_fees=None,
                    net_amount=amount, currency=currency,
                    description=desc, raw_line=ln,
                    resolution_method=resolution_method,
                ))
                continue

            # Some CIBC rows have a blank Activity cell but retain an explicit
            # date, description, two blank security columns, and signed cash
            # amount. Preserve the observable cash effect as an adjustment;
            # do not invent an income/tax subtype.
            blank_cash = re.match(
                r"(.+?)\s+—\s+—\s+(-?\$?[\d,]+(?:\.\d+)?-?)\s*$",
                rest,
            )
            if blank_cash:
                amount = parse_money(blank_cash.group(2))
                if amount is not None:
                    stmt.transactions.append(ParsedTxn(
                        trade_date=trade_date or "",
                        settle_date=None,
                        txn_type="adjustment",
                        instrument=None,
                        quantity=None,
                        price=None,
                        gross_amount=None,
                        commission=None,
                        other_fees=None,
                        net_amount=amount,
                        currency=currency,
                        description=blank_cash.group(1).strip(),
                        raw_line=ln,
                    ))
                    continue

            # Date-prefixed but no recognized verb → quarantine and make the
            # cash scope non-authoritative when the row contains a number.
            stmt.quarantine.append((ln, "unrecognized activity row"))
            if re.search(r"\d", rest):
                cash_uncertain = True
            continue

        # A no-date line cannot be assigned to the preceding transaction
        # defensibly. Keep activity-like evidence in quarantine instead of
        # contaminating that transaction's description and source geometry.
        if re.search(r"\d", s):
            stmt.quarantine.append(ParsedQuarantine(
                raw_line=ln,
                reason="unclaimed activity-like row",
            ))

    if opening_balance is not None and not cash_complete:
        stmt.quarantine.append(ParsedQuarantine(
            raw_line="\n".join(cash_lines),
            reason="opening cash balance has no valid closing balance",
        ))
    return cash_complete and not cash_uncertain


def _last_money(s: str) -> str | None:
    matches = list(RE_MONEY.finditer(s))
    return matches[-1].group(0) if matches else None


# -------------------------------------------------------- Portfolio Assets
def _parse_portfolio_block(body: str, *, currency: str, period_end: str,
                           stmt: ParsedStatement) -> bool:
    section = "Equities"
    saw_section = False
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        i += 1
        if not s:
            continue
        # Sub-section headers
        if s in {"Equities", "Mutual Funds", "Other", "Cash & Cash Equivalents",
                  "Fixed Income", "Bonds"}:
            section = s
            saw_section = True
            continue
        lower = s.lower()
        if lower.startswith("subtotal") or lower.startswith("total portfolio") or \
           lower.startswith("description") or lower.startswith("price at"):
            continue
        # Currency conversion footer of a USD portfolio block.
        if "Canadian dollar equivalent" in s or "U.S. equals" in s:
            continue
        # Squeezed wire-confirmation receipt fragments under wire-settled
        # redemptions: furniture, not holdings.
        if _is_wire_confirmation_footer(s):
            continue
        # Page furniture (period line, previous-statement marker, page footer)
        # repeats per page and is never row data.
        if _is_page_furniture(s):
            continue
        # Disclosure/legal boilerplate and printer barcodes from the page
        # footer are not holdings. Portfolio rows are securities, never prose
        # or fee announcements, so skipping here cannot lose an event.
        if s.startswith("HRI-") or _is_disclosure_boilerplate(s):
            continue

        if section == "Cash & Cash Equivalents":
            # Skip — covered by cash_balances.
            continue

        if section == "Other":
            mo = RE_OPT_POS.match(s)
            if not mo:
                if re.search(r"\d", s):
                    stmt.quarantine.append(ParsedQuarantine(
                        raw_line=ln,
                        reason="unrecognized option portfolio row",
                    ))
                continue
            _, i = _take_name_continuations(lines, i)
            cp, root, mon3, dd, yr, strike_s, qty_s, book_s, mp_s, mv_s = mo.groups()
            expiry = _opt_expiry(mon3, dd, yr)
            qty = float(qty_s.replace(",", ""))
            instr = _make_option_instrument(
                root=root, expiry=expiry or "", strike=float(strike_s),
                cp=cp, currency=currency,
            )
            stmt.positions.append(ParsedPosition(
                instrument=instr, quantity=qty,
                avg_cost=None, book_value=parse_money(book_s),
                market_price=parse_money(mp_s), market_value=parse_money(mv_s),
                unrealized_pnl=None, currency=currency, raw_line=ln,
            ))
            continue

        # Equities / Mutual Funds line: trailing five numbers.
        # description ... <qty> <book> <price> <market> <segregation>
        # The segregation column may be a number, '—', or absent.
        m = re.search(
            r"(-?\d[\d,]*(?:\.\d+)?)\s+(-?\$?[\d,]+(?:\.\d+)?)\s+(-?[\d,]+(?:\.\d+)?)\s+"
            r"(-?\$?[\d,]+(?:\.\d+)?)(?:\s+(?:[\d,.]+|—|-))?\s*$",
            s,
        )
        if not m:
            if re.search(r"\d", s):
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="unrecognized portfolio row",
                ))
            continue
        qty_s, book_s, price_s, mv_s = m.group(1), m.group(2), m.group(3), m.group(4)
        # Wrapped name lines and the printed ``(SYM/EXCH)`` ticker line under
        # the row identify the security; merge them into the description.
        cont, i = _take_name_continuations(lines, i)
        desc = " ".join([s[:m.start()].strip()] + cont).strip()
        if not desc:
            stmt.quarantine.append(ParsedQuarantine(
                raw_line=ln,
                reason="portfolio row has no description",
            ))
            continue
        atype = "equity"
        up = desc.upper()
        # A printed "... ETF" name is an ETF even when the issuer contains
        # "FUND" (e.g. "SPROTT FUNDS TRUST ... MINERS ETF"); mutual-fund
        # identity is otherwise held to the stricter broker-code standard.
        if " ETF" in up:
            atype = "etf"
        elif section == "Mutual Funds" or "FUND" in up:
            atype = "mutual_fund"
        instr = _instr_from_desc(desc, currency)
        instr.asset_type = atype
        stmt.positions.append(ParsedPosition(
            instrument=instr,
            quantity=float(qty_s.replace(",", "")),
            avg_cost=None, book_value=parse_money(book_s),
            market_price=parse_money(price_s), market_value=parse_money(mv_s),
            unrealized_pnl=None, currency=currency, raw_line=ln,
        ))
    return saw_section


# ----------------------------------------------------------------- Parser
class CIBCParser:
    NAME = "cibc"
    VERSION = "2.8.4"

    def can_handle(self, folder_name: str, first_page_text: str) -> bool:
        if folder_name.startswith("CIBC "):
            return True
        head = first_page_text[:4000]
        return ("Imperial Investor Service" in head
                or "Investor's Edge" in head)

    def parse(self, pdf: PdfText) -> ParseResult:
        result = ParseResult(parser_name=self.NAME, parser_version=self.VERSION)
        # Normalize pdfplumber font-fallback artifacts: 'ð' is its standard
        # placeholder for em-dash in some CIBC e-Statements (older years), and
        # 'ƒ' is a footnote marker ("book cost estimated from market value")
        # that lands inside portfolio rows and breaks their column parsing.
        page_index = PageTextIndex.from_pdf(
            pdf,
            transform=lambda value: value.replace("\u00f0", "\u2014").replace(
                "\u00d0", "\u2014"
            ).replace("\u0192", ""),
            include_page=lambda _number, page: not (
                "Disclosures" in page
                and "Account Activity" not in page
                and "Portfolio Assets" not in page
            ),
        )
        text = page_index.text

        if _is_tax_doc(text, pdf.relpath):
            result.status = "skipped"
            result.skip_reason = "tax document; no brokerage statement extraction"
            return result

        period = _parse_period(text)
        if not period:
            result.errors.append("could not parse period header")
            return result
        period_start, period_end = period

        acct = _extract_account_number(text, pdf.relpath)
        if not acct:
            result.errors.append("could not parse account number")
            return result

        atype = _detect_account_type(text)
        base_ccy = "CAD"  # CIBC statements report in CAD with USD subsection.

        stmt = ParsedStatement(
            account=ParsedAccount(account_number=acct, account_type=atype,
                                  base_currency=base_ccy),
            period_start=period_start, period_end=period_end,
            statement_type="monthly",
            page_numbers=page_index.all_pages,
        )

        year = _activity_year(period_end)
        position_scopes: dict[str, str] = {}
        cash_scopes: dict[str, str] = {}
        for kind, ccy, body in _split_sections(text):
            try:
                if kind == "activity":
                    if _parse_activity_block(body, currency=ccy, year=year, stmt=stmt):
                        cash_scopes[ccy] = "complete"
                else:
                    if _parse_portfolio_block(body, currency=ccy,
                                              period_end=period_end, stmt=stmt):
                        position_scopes[ccy] = "complete"
            except Exception as e:
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line="<section>",
                    reason=f"section parse error: {e}",
                ))

        declare_snapshot_scopes(
            stmt,
            position_scopes=position_scopes,
            cash_scopes=cash_scopes,
        )
        result.statements.append(stmt)
        quarantine_unsupported_rows(result)
        attach_source_spans(pdf, result, parser_name=self.NAME)
        return result


register(CIBCParser())
