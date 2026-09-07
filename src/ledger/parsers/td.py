"""TD Direct Investing / TD Waterhouse parser.

Filename styles:
    Statement_<acct>_YYYY-MM.pdf       - modern monthly
    Statement_<acct>_YYYY_MM-MM.pdf    - legacy quarterly
    Statement_<acct>_YYYY_summary.pdf  - annual summary

A single PDF contains one or more account sub-statements identified by
"Account number: <acct>" + "Account type: Direct Trading - {CDN|US}". Each
emits a separate ParsedStatement with synthetic account_number
"<acct>-CDN" / "<acct>-US".

Option formats:
    Activity row (single line):
        Buy  PUT -100 SLV'26 FB@100  30   9.050   -27,187.50   -20,978.94
        Sell CALL-100 SLV'26 13FB@115 -30 8.450    25,302.51    4,323.57
        Buy  CALL-100 PAAS'27-US JA@60 20 11.550 -23,125.00 -12,636.88

    Position rows (two physical lines):
        CALL-100 PAAS'27-US  20  9.380  23,125.00  18,760.00  -4,365.00  0.98%
        JA@60
        CALL-100 SLV'26  30  0.640  4,007.49  1,920.00  -2,087.49  0.10%
        MR@115
        CALL-100 AMD'26  10  9.650  9,962.49  9,650.00  -312.49  0.50%
        18JN@300
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..pdf_text import PdfText
from .helpers import _OPT_MON, _third_friday, parse_money
from .layout import (
    PageTextIndex,
    attach_source_spans,
    declare_snapshot_scopes,
    quarantine_unsupported_rows,
)
from .name_resolver import PRINTED_FUND_CODE_RE, resolve_ticker, synthetic_symbol
from .registry import register
from .types import (
    ParsedAccount,
    ParsedAnnualPerformance,
    ParsedCashBalance,
    ParsedInstrument,
    ParsedPosition,
    ParsedQuarantine,
    ParsedScopeIssue,
    ParsedSnapshotSet,
    ParsedStatement,
    ParsedTxn,
    ParseResult,
    SourceSpan,
)

_MON = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_MON_FULL = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11,
    "December": 12,
}

RE_PERIOD_FULL = re.compile(
    r"([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})\s+to\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})",
)
# Legacy 2016-2017 format: "Statement for January 1 to January 31, 2016"
RE_PERIOD_LEGACY = re.compile(
    r"Statement for\s+([A-Z][a-z]+)\s+(\d{1,2})\s+to\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})",
)
RE_PERIOD_END_ONLY = re.compile(
    r"For the period ending\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})",
)
RE_ACCT_NUM = re.compile(r"Account number:\s+([A-Z0-9]+)")
RE_ACCT_TYPE = re.compile(r"Account type:\s+Direct Trading\s*-\s*(CDN|US)")
_SIGNED_MONEY_TOKEN = r"((?:-\$?|\$-?|\$)?\(?[\d,]+(?:\.\d+)?\)?-?)"
RE_BEGIN_BAL = re.compile(rf"Beginning cash balance\s+{_SIGNED_MONEY_TOKEN}")
RE_END_BAL = re.compile(rf"Ending cash balance\s+{_SIGNED_MONEY_TOKEN}")
RE_LEGACY_BEGIN_BAL = re.compile(rf"Cash-opening balance\s+{_SIGNED_MONEY_TOKEN}")
RE_LEGACY_END_BAL = re.compile(rf"Cash-closing balance\s+{_SIGNED_MONEY_TOKEN}")

# Option token in activity (single line). Captures: cp, mult sign, root,
# yy, [dd]mm, strike. Examples: "PUT -100 SLV'26 FB@100", "CALL-100 SLV'26 13FB@115".
# OCC adjusted roots may lead with a digit ("5SOXS") and carry the "+$"
# adjustment marker ("98TRI+$"); the marker stays part of the root so the
# activity instrument matches the holdings-table instrument.
RE_OPT_TOKEN = re.compile(
    r"(CALL|PUT)\s*[- ]\s*(?:-)?100\s*([A-Z0-9][A-Z0-9.]{0,5}(?:\+\$)?)'(\d{2})(?:-US)?\s*"
    r"(\d{0,2})([A-Z]{2})@(\d+(?:\.\d+)?)"
)
# Expiry-only token used for stitching position rows: "[dd]mm@strike"
RE_OPT_TAIL = re.compile(r"^(\d{0,2})([A-Z]{2})@(\d+(?:\.\d+)?)$")

# Income rows whose description is an interest-period memo ("Interest
# INTEREST TO JUL 16 -87.81"): cash events that never name a security.
RE_INTEREST_MEMO = re.compile(r"^INTEREST\s+TO\s+[A-Z]{3}\.?\s+\d{1,2}", re.IGNORECASE)

# A bare equity holding row: "BANK OF MONTREAL 1,600 SEG 174.230 45,606.97 278,768.00 233,161.03 16.87%"
# Symbol may appear on this line (in parens) or on the *next* line.
RE_HOLDING_LINE = re.compile(
    r"^(.+?)\s+(-?[\d,]+(?:\.\d+)?-?)\s*(?:SEG\s+)?"
    r"([\d,]+(?:\.\d+)?)\s+(-?[\d,]+(?:\.\d+)?)\s+(-?[\d,]+(?:\.\d+)?)\s+"
    r"(-?[\d,]+(?:\.\d+)?)\s+(-?[\d,]+(?:\.\d+)?)\s*%$"
)
RE_LEGACY_HOLDING_LINE = re.compile(
    r"^([\d,]+(?:\.\d+)?)\s+Seg\s+(.+?)\s+([A-Z][A-Z0-9.]{0,8})\s+"
    r"(N/D|[\d,]+(?:\.\d+)?)\s+"
    r"(N/D|-?[\d,]+(?:\.\d+)?)\s+"
    r"(N/D|-?[\d,]+(?:\.\d+)?)\s+"
    r"(-?[\d,]+(?:\.\d+)?)$"
)
# 2017-2018 WebBroker holdings print the name first, then quantity, the SEG
# marker, and a five-cell tail with an extra unrealized-gain column
# ("CDN IMPERIAL BK 1,200 SEG 105.390 61,469.99 126,468.00 64,998.01
# 15.68%"). Unpriced (defunct) securities print N/D in the price and market
# cells ("NORTEL NETWORKS 2,019 SEG N/D 21,570.00 N/D -21,570.00 0.00%").
RE_NAME_FIRST_HOLDING = re.compile(
    r"^(.+?)\s+(-?[\d,]+(?:\.\d+)?-?)\s+SEG\s+"
    r"(N/D|-?[\d,]+(?:\.\d+)?)\s+"
    r"(N/D|-?[\d,]+(?:\.\d+)?)\s+"
    r"(N/D|-?[\d,]+(?:\.\d+)?)\s+"
    r"(N/D|-?[\d,]+(?:\.\d+)?)\s+"
    r"(N/D|-?[\d,]+(?:\.\d+)?)\s*%$"
)
RE_TRAIL_SYM = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,8})\s*\)")

_ACTIVITY_NUMBER = r"-?\$?[\d,]+(?:\.\d+)?-?"
RE_ACTIVITY_NUMERIC_TAIL = re.compile(
    rf"\s+({_ACTIVITY_NUMBER})\s+({_ACTIVITY_NUMBER})\s+"
    rf"({_ACTIVITY_NUMBER})(?:\s+({_ACTIVITY_NUMBER}))?\s*$"
)
RE_LEGACY_TRADE_ROW = re.compile(
    rf"^({_ACTIVITY_NUMBER})\s+(.+?)\s+({_ACTIVITY_NUMBER})\s+"
    rf"({_ACTIVITY_NUMBER})\s+({_ACTIVITY_NUMBER})\s*$"
)
RE_TD_REFERENCE = re.compile(r"\b[A-Z]{2}-\d{6}\b")

# Activity date prefix:  "Oct 31", "Sep 30"
RE_ACT_DATE = re.compile(
    # The 2016-era layout squashes the date ("Dec31 Dividend"); the current
    # one prints a space ("Jan 05 ...").
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s?(\d{1,2})\s+(.*)$"
)

ACT_VERBS = {
    "Buy": "buy",
    "Sell": "sell",
    "Disposition": "sell",
    "Dividend": "dividend",
    "Dividends": "dividend",
    "Distribution": "distribution",
    "Distributions": "distribution",
    "Interest": "interest_income",
    "Reinvestment": "dividend",
    "Expiration": "option_expiration",
    "Exercise Option": "option_exercise",
    "Exercise": "option_exercise",
    "Assignment": "option_assignment",
    "Assigned": "option_assignment",
    "Assign": "option_assignment",
    "Transfer In": "transfer_in",
    "Transfer Out": "transfer_out",
    "Transfer": "transfer_in",
    "Web Banking": "transfer_in",
    "Contribution": "deposit",
    "Deposit": "deposit",
    "Withdrawal": "withdrawal",
    "Cash withdrawal": "withdrawal",
    "Fee": "fee",
    "Service Charge": "fee",
    "Admin FeeCharged": "fee",
    "Cheque Issued By": "withdrawal",
    "Cancel Interest": "interest_income",
    "Name Change": "name_change",
    "Symbol Change": "name_change",
    "Ticker Change": "name_change",
    "Foreign Tax": "tax_withholding",
    "Withholding Tax": "tax_withholding",
    "Non Resident Tax": "tax_withholding",
    "Tax": "tax_withholding",
    "Adjustment": "adjustment",
    "Journal": "journal",
    "Return of capital": "return_of_capital",
    "Cash in Lieu": "distribution",
    "CIL": "distribution",
    "Capital Gains": "distribution",
    "Stock split": "stock_split",
    "Reverse Split": "stock_split",
    "Stock dividend": "dividend",
    # Fractional-residue settlement notes print a 0.00 cash amount and an
    # internal vehicle name ("Security Position RBC QUBE CDN 0.001 0.00");
    # they move no listed security and no cash.
    "Security Position": "adjustment",
    # In-kind exchanges between broker-pooled fund series ("Stock Exchange
    # RBC QUBE CDN 2,660.453 0.00"): signed unit deltas with no cash.
    "Stock Exchange": "journal",
    # Fund-series exchanges with a transferred value and running balance
    # ("Exchange TD CDN EQ-D /NL'FRAC 2,379.892 -22,516.16 21,288.30").
    "Exchange": "journal",
    # Removal of a worthless security ("Defunct Security BATTERY
    # TECHNOLOGIES -1,000 0.00 1,016.12").
    "Defunct Security": "journal",
}

# TD's activity layouts often print debit/credit columns without a sign.  Use
# the printed event only where it unambiguously establishes the cash direction;
# leave ambiguous events (journals, adjustments, assignments) as printed.
_CASH_OUTFLOW_TYPES = {
    "buy",
    "option_buy_to_open",
    "option_buy_to_close",
    "withdrawal",
    "transfer_out",
    "fee",
    "tax_withholding",
}
_CASH_INFLOW_TYPES = {
    "sell",
    "option_sell_to_open",
    "option_sell_to_close",
    "dividend",
    "distribution",
    "interest_income",
    "deposit",
    "transfer_in",
    "return_of_capital",
}


@dataclass
class _Sub:
    currency: str
    account_number: str
    text: str
    page_numbers: tuple[int, ...] = ()


@dataclass
class _CashState:
    """Carry an activity cash opening across a repeated page fragment."""

    opening: float | None = None
    lines: list[str] = field(default_factory=list)
    uncertain: bool = False


@dataclass
class _ScopeState:
    positions_seen: bool = False
    positions_complete: bool = True
    cash_seen: bool = False
    cash_complete: bool = False
    cash_failed: bool = False
    cash: _CashState = field(default_factory=_CashState)
    position_issues: list[ParsedScopeIssue] = field(default_factory=list)
    cash_issues: list[ParsedScopeIssue] = field(default_factory=list)
    opening_total: float | None = None
    reported_change: float | None = None
    closing_total: float | None = None
    positions_total: float | None = None
    cash_total: float | None = None
    summary_lines: list[str] = field(default_factory=list)
    positions_total_line: str | None = None
    cash_total_line: str | None = None


def _is_td_disclosure_page(_page_number: int, page: str) -> bool:
    return re.search(r"(?im)^\s*disclosures\s*$", page) is None


def _owned_sub_pages(pdf: PdfText, pages: tuple[int, ...], currency: str) -> tuple[int, ...]:
    expected = "CDN" if currency == "CAD" else "US"
    owned: list[int] = []
    for page_number in pages:
        page = pdf.pages[page_number - 1]
        if not _is_td_disclosure_page(page_number, page):
            continue
        printed_types = set(RE_ACCT_TYPE.findall(page))
        if printed_types and expected not in printed_types:
            continue
        owned.append(page_number)
    return tuple(owned)


def _reported_value(
    text: str,
    label: str,
    *,
    value_index: int = 0,
) -> tuple[float | None, str | None]:
    pattern = re.compile(
        rf"^\s*{label}\s+.*\d.*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return None, None
    # TD prints negative summary values with the minus before the dollar
    # sign ("-$24,175.14"); the leading "-" must join the token or the sign
    # is silently dropped.
    values = re.findall(r"-?\$?\(?-?[\d,]+(?:\.\d+)?\)?-?", match.group(0))
    if value_index >= len(values):
        return None, match.group(0).strip()
    return parse_money(values[value_index]), match.group(0).strip()


def _capture_reported_totals(text: str, state: _ScopeState) -> None:
    fields = (
        ("opening_total", r"Beginning balance", 0),
        ("reported_change", r"Change in your account", 0),
        ("closing_total", r"Ending balance", 0),
        ("positions_total", r"Total equities", 1),
        ("cash_total", r"Cash", 0),
    )
    for field_name, label, value_index in fields:
        value, raw_line = _reported_value(text, label, value_index=value_index)
        if value is None:
            continue
        setattr(state, field_name, value)
        if field_name == "positions_total":
            state.positions_total_line = raw_line
        elif field_name == "cash_total":
            state.cash_total_line = raw_line
        elif raw_line and raw_line not in state.summary_lines:
            state.summary_lines.append(raw_line)


def _is_summary(relpath: str) -> bool:
    rl = relpath.lower()
    return rl.endswith("_summary.pdf") or "_summary." in rl or "mid_year_summary" in rl


# -------------------------------------------------- Annual performance report
# December monthly statements and the *_summary.pdf files attach "Your
# performance report" and "Your fees and charges report" pages for the
# calendar year. The report is its own category with its own reconciliation:
# its pages are extracted as a separate annual statement (or attached to the
# summary-sourced one) and its printed lines and numbers are stored on that
# record rather than mixed into monthly transactions or holdings.
_TD_ANNUAL_PAGE_MARKERS = ("Your performance report", "Your fees and charges report")


def _is_td_annual_report_page(text: str) -> bool:
    return any(marker in text for marker in _TD_ANNUAL_PAGE_MARKERS)


def _parse_td_performance_pages(pages: dict[int, str]) -> ParsedStatement | None:
    """Build the annual statement from the performance-report pages.

    One page prints per account currency (Canadian/U.S. dollars); each
    carries the performance table with a report-year column and a
    since-inception column, plus the personal rates of return.
    """
    perf_pages = {
        number: text for number, text in sorted(pages.items())
        if "Your performance report" in text
    }
    if not perf_pages:
        return None
    first = next(iter(perf_pages.values()))
    pm = RE_PERIOD_FULL.search(first)
    am = RE_ACCT_NUM.search(first)
    if pm is None or am is None:
        return None
    period_start = (
        f"{int(pm.group(3)):04d}-{_MON_FULL[pm.group(1)]:02d}-{int(pm.group(2)):02d}"
    )
    period_end = (
        f"{int(pm.group(6)):04d}-{_MON_FULL[pm.group(4)]:02d}-{int(pm.group(5)):02d}"
    )
    acct = am.group(1)

    performance: list[ParsedAnnualPerformance] = []
    seen_currencies: set[str] = set()
    for page_text in perf_pages.values():
        cm = re.search(r"Account currency:\s*([A-Za-z .-]+)", page_text)
        if cm is None:
            continue
        currency = (
            "CAD" if "Canadian" in cm.group(1)
            else "USD" if "U.S" in cm.group(1) or "US" in cm.group(1)
            else None
        )
        if currency is None or currency in seen_currencies:
            continue
        seen_currencies.add(currency)

        def printed_value(label: str, page_text: str = page_text) -> float | None:
            for line in page_text.splitlines():
                if line.strip().startswith(label):
                    tokens = re.findall(r"-?\$?[\d,]+\.\d{2}", line)
                    values = [parse_money(token) for token in tokens]
                    values = [value for value in values if value is not None]
                    return values[0] if values else None
            return None

        since_m = re.search(r"Since\s+([A-Z][a-z]{2})\s+(\d{1,2}),\s*(\d{4})", page_text)
        since_date = None
        since_mon = _MON.get(since_m.group(1)[:3].upper()) if since_m else None
        if since_m and since_mon:
            since_date = (
                f"{int(since_m.group(3)):04d}-{since_mon:02d}"
                f"-{int(since_m.group(2)):02d}"
            )
        rates = re.search(r"\(%\)\s*\n\s*(\d{1,3}\.\d{2})\s+(\d{1,3}\.\d{2})", page_text)
        performance.append(ParsedAnnualPerformance(
            currency=currency,
            period_start=period_start,
            period_end=period_end,
            since_date=since_date,
            beginning_market_value=printed_value(
                "Performance reporting beginning balance"),
            deposits_transfers_in=printed_value("Deposits including transfers in"),
            withdrawals_transfers_out=printed_value(
                "Withdrawals including transfers out"),
            net_investment_return=printed_value("Change in value of your account"),
            ending_market_value=printed_value(
                "Performance reporting ending balance"),
            money_weighted_1y=float(rates.group(1)) if rates else None,
            money_weighted_3y=None,
            money_weighted_5y=None,
            money_weighted_10y=None,
            money_weighted_since=float(rates.group(2)) if rates else None,
        ))
    if not performance:
        return None
    annual_pages = tuple(sorted(
        number for number, text in pages.items()
        if any(marker in text for marker in _TD_ANNUAL_PAGE_MARKERS)
    ))
    return ParsedStatement(
        account=ParsedAccount(account_number=acct, account_type="Direct Trading",
                              base_currency="CAD"),
        period_start=period_start,
        period_end=period_end,
        statement_type="annual",
        page_numbers=annual_pages,
        annual_performance=performance,
    )


def _parse_period(text: str) -> tuple[str, str] | None:
    m = RE_PERIOD_FULL.search(text)
    if m:
        try:
            sm = _MON_FULL[m.group(1)]
            sd = int(m.group(2))
            sy = int(m.group(3))
            em = _MON_FULL[m.group(4)]
            ed = int(m.group(5))
            ey = int(m.group(6))
            return date(sy, sm, sd).isoformat(), date(ey, em, ed).isoformat()
        except (KeyError, ValueError):
            pass
    ml = RE_PERIOD_LEGACY.search(text)
    if ml:
        try:
            sm = _MON_FULL[ml.group(1)]
            sd = int(ml.group(2))
            em = _MON_FULL[ml.group(3)]
            ed = int(ml.group(4))
            y = int(ml.group(5))
            return date(y, sm, sd).isoformat(), date(y, em, ed).isoformat()
        except (KeyError, ValueError):
            pass
    m2 = RE_PERIOD_END_ONLY.search(text)
    if m2:
        try:
            em = _MON_FULL[m2.group(1)]
            ed = int(m2.group(2))
            ey = int(m2.group(3))
            return date(ey, em, 1).isoformat(), date(ey, em, ed).isoformat()
        except (KeyError, ValueError):
            pass
    return None


def _legacy_period_from_match(match: re.Match[str]) -> tuple[str, str] | None:
    try:
        sm = _MON_FULL[match.group(1)]
        sd = int(match.group(2))
        em = _MON_FULL[match.group(3)]
        ed = int(match.group(4))
        year = int(match.group(5))
        return date(year, sm, sd).isoformat(), date(year, em, ed).isoformat()
    except (KeyError, ValueError):
        return None


def _full_period_from_match(match: re.Match[str]) -> tuple[str, str] | None:
    try:
        sm = _MON_FULL[match.group(1)]
        sd = int(match.group(2))
        sy = int(match.group(3))
        em = _MON_FULL[match.group(4)]
        ed = int(match.group(5))
        ey = int(match.group(6))
        return date(sy, sm, sd).isoformat(), date(ey, em, ed).isoformat()
    except (KeyError, ValueError):
        return None


def _statement_chunks(text: str) -> list[tuple[str, str, str, int, int]]:
    markers: list[tuple[int, tuple[str, str]]] = []
    for match in RE_PERIOD_LEGACY.finditer(text):
        period = _legacy_period_from_match(match)
        if period is not None:
            markers.append((match.start(), period))
    for match in RE_PERIOD_FULL.finditer(text):
        period = _full_period_from_match(match)
        if period is not None:
            markers.append((match.start(), period))
    markers.sort(key=lambda item: item[0])
    if not markers:
        period = _parse_period(text)
        return [(period[0], period[1], text, 0, len(text))] if period else []

    chunks: list[tuple[str, str, str, int, int]] = []
    current_start, current_period = markers[0]
    for start, period in markers[1:]:
        if period != current_period:
            chunks.append((
                current_period[0], current_period[1], text[current_start:start],
                current_start, start,
            ))
            current_period = period
            current_start = start
    if current_period is not None:
        chunks.append((
            current_period[0], current_period[1], text[current_start:],
            current_start, len(text),
        ))
    return chunks


def _split_subs(
    text: str,
    *,
    base_offset: int = 0,
    page_index: PageTextIndex | None = None,
) -> list[_Sub]:
    """Split the PDF text on each 'Account number: X / Account type: Direct Trading - Y' header.

    For legacy 2016-2017 statements (no 'Account type:' literal), fall back
    to splitting on lines containing 'Direct Trading - (CDN|US)' as a
    standalone marker. Account number is then taken from the filename via
    a separate regex (caller responsibility).
    """
    subs: list[_Sub] = []
    marks: list[tuple[int, str, str]] = []
    for m in RE_ACCT_TYPE.finditer(text):
        start_search = max(0, m.start() - 200)
        an = None
        for am in RE_ACCT_NUM.finditer(text, start_search, m.start()):
            an = am.group(1)
        if not an:
            continue
        ccy = "CAD" if m.group(1) == "CDN" else "USD"
        marks.append((m.start(), an, ccy))

    if not marks:
        # Legacy fallback: find 'Direct Trading - CDN'/'... - US' standalone
        # markers; account number from "Account number\n<ID>" header.
        an_match = re.search(r"Account number\s*[\n\r]+\s*([A-Z0-9]+)", text)
        an_legacy = an_match.group(1) if an_match else None
        for m in re.finditer(r"Direct Trading\s*-\s*(CDN|US)\b", text):
            if not an_legacy:
                continue
            ccy = "CAD" if m.group(1) == "CDN" else "USD"
            marks.append((m.start(), an_legacy, ccy))

    if not marks:
        return subs
    cur_an, cur_ccy = marks[0][1], marks[0][2]
    cur_start = marks[0][0]
    for i in range(1, len(marks)):
        pos, an, ccy = marks[i]
        if (an, ccy) != (cur_an, cur_ccy):
            start = base_offset + cur_start
            end = base_offset + pos
            subs.append(_Sub(
                currency=cur_ccy,
                account_number=cur_an,
                text=text[cur_start:pos],
                page_numbers=page_index.pages_for_range(start, end) if page_index else (),
            ))
            cur_an, cur_ccy, cur_start = an, ccy, pos
    start = base_offset + cur_start
    end = base_offset + len(text)
    subs.append(_Sub(
        currency=cur_ccy,
        account_number=cur_an,
        text=text[cur_start:],
        page_numbers=page_index.pages_for_range(start, end) if page_index else (),
    ))
    return subs


def _scope_issues_from_quarantine(
    rows: list[tuple[str, str] | ParsedQuarantine],
    *,
    section_type: str,
) -> list[ParsedScopeIssue]:
    issues: list[ParsedScopeIssue] = []
    for row in rows:
        if not isinstance(row, ParsedQuarantine):
            continue
        reason = row.reason.lower()
        if reason == "unrecognized holding row":
            code = "unrecognized_holding_row"
        elif reason == "holding without printed symbol":
            code = "holding_identity_missing"
        elif "closing balance" in reason:
            code = "closing_balance_missing"
        elif section_type == "positions":
            code = "position_row_unavailable"
        else:
            code = "cash_activity_unavailable"
        issues.append(ParsedScopeIssue(
            issue_code=code,
            detail={"section_type": section_type},
            quarantine=row,
        ))
    return issues


def _valid_td_option_token(yy: str, dd: str, mon: str) -> bool:
    """Reject option-token regex matches with non-month codes or invalid days."""
    month = {**_OPT_MON, "FB": 2}.get(mon.upper())
    if month is None:
        return False
    if dd:
        try:
            day = int(dd)
        except ValueError:
            return False
        if day < 1 or day > 31:
            return False
    return _option_expiry(yy, dd, mon) is not None


def _option_expiry(yy: str, dd: str, mon: str) -> str | None:
    # TD uses both FE and FB for February across statement generations.
    m = {**_OPT_MON, "FB": 2}.get(mon.upper())
    if not m:
        return None
    year = 2000 + int(yy)
    if dd:
        try:
            return date(year, m, int(dd)).isoformat()
        except ValueError:
            return None
    return _third_friday(year, m).isoformat()


def _classify(verb_phrase: str) -> str | None:
    # Try longest match first.
    folded = verb_phrase.casefold()
    for k in sorted(ACT_VERBS, key=len, reverse=True):
        if folded.startswith(k.casefold()):
            return ACT_VERBS[k]
    return None


def _activity_identity(desc: str) -> str:
    """Remove TD execution/reference suffixes from a printed security name."""
    cleaned = RE_TD_REFERENCE.sub(" ", desc)
    cleaned = re.sub(
        r"\s+AS(?:\s+OF(?:\s+[A-Z]{3})?)?\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _apply_reinvestment_plan_continuation(cur: ParsedTxn, line: str) -> None:
    """Convert a zero-cash dividend into a DRIP reinvestment.

    TD prints "Reinvestment Plan VALUE = <n>" under dividend rows whose cash
    amount bought fund units instead of paying out. The units column then
    carries the purchased quantity, so the row describes a security movement
    rather than a cash dividend. The printed VALUE stays in the description;
    nothing is fabricated beyond the printed columns.
    """
    if cur.txn_type != "dividend" or cur.net_amount not in (None, 0.0):
        return
    has_value_note = re.search(
        r"Reinvestment\s+Plan\s+VALUE\s*=\s*\$?[\d,]+(?:\.\d+)?", line, re.IGNORECASE
    )
    # The 2016-era layout splits the row: the dividend row prints no
    # numbers and the units lead the "Reinvestment Plan" continuation
    # ("Reinvestment Plan 0.553 TDCDNMNY MKT-I /NL'FRAC 0.00 60,111.90"),
    # with the reinvested value on its own "VALUE=" line.
    units = re.match(
        r"Reinvestment\s+Plan\s+([\d,]+(?:\.\d+)?)\s+(?!VALUE)",
        line,
        re.IGNORECASE,
    )
    if not has_value_note and units is None:
        return
    numbers = re.findall(r"-?[\d,]+(?:\.\d+)?", (cur.raw_line or "").splitlines()[0])
    quantity = parse_money(numbers[-3]) if len(numbers) >= 3 else None
    if quantity is None:
        if units is None:
            return
        quantity = parse_money(units.group(1))
        if quantity is None:
            return
        if cur.instrument is None:
            name_part = re.split(
                r"[\d,]+(?:\.\d+)?", line[units.end():].strip(), maxsplit=1,
            )[0].strip()
            instrument = _instrument_from_description(name_part, cur.currency)
            if instrument is not None:
                cur.instrument = instrument
    cur.txn_type = "reinvest_dividend"
    cur.quantity = quantity


def _apply_web_banking_continuation(cur: ParsedTxn, line: str) -> None:
    """Promote the cash amount printed on a Web Banking continuation line.

    The 2016-era layout splits the row across lines: "Jan 05 Web Banking"
    carries no numbers, and the direction verb, transfer reference, amount,
    and running balance print on the next line ("Deposit RX534TSFFR3301767
    10,000.00 70,111.90"). The continuation's leading verb also fixes the
    direction the bare "Web Banking" row could not.
    """
    if cur.txn_type != "transfer_in" or cur.net_amount is not None:
        return
    direction = re.match(r"(Deposit|Withdrawal)\b", line, re.IGNORECASE)
    if direction is None:
        return
    nums = re.findall(r"-?[\d,]+(?:\.\d+)?", line)
    if len(nums) < 2:
        return
    amount = parse_money(nums[-2])
    if amount is None:
        return
    cur.net_amount = -abs(amount) if direction.group(1).lower() == "withdrawal" \
        else abs(amount)
    cur.txn_type = (
        "transfer_out" if direction.group(1).lower() == "withdrawal"
        else "transfer_in"
    )


def _instrument_from_description(desc: str, currency: str) -> ParsedInstrument | None:
    sm = RE_TRAIL_SYM.search(desc)
    if sm:
        return ParsedInstrument(
            asset_type="equity", symbol=sm.group(1),
            currency=currency, name=desc[:sm.start()].strip()[:120],
        )
    identity = _activity_identity(desc)
    if not identity:
        return None
    known = resolve_ticker(identity, currency)
    if known is not None:
        symbol, asset_type = known
        # A curated name entry resolving to a strict broker fund code carries
        # that code's printed identity (holdings rows print name and code
        # together), so the staged resolver retains it.
        method = (
            "printed_fund_code"
            if asset_type == "mutual_fund"
            and PRINTED_FUND_CODE_RE.fullmatch(symbol)
            else None
        )
        return ParsedInstrument(
            asset_type=asset_type, symbol=symbol,
            currency=currency, name=identity[:120],
            resolution_method=method,
            resolution_confidence=1.0 if method else None,
        )
    asset_type = (
        "mutual_fund"
        if "FUND" in identity.upper() or "/NL'FRAC" in identity.upper()
        else "etf" if " ETF" in identity.upper()
        else "equity"
    )
    return ParsedInstrument(
        asset_type=asset_type,
        symbol=synthetic_symbol(identity),
        currency=currency,
        name=identity[:120],
        resolution_method="unresolved_printed_identity",
        resolution_confidence=0.0,
    )


def _is_option_interstitial(line: str) -> bool:
    """Return whether a line can safely occur between option head and tail."""
    lower = line.lower()
    return (
        lower.startswith("page ")
        or lower.startswith("td direct investing")
        or lower.startswith("td waterhouse")
        or lower.startswith("account number:")
        or lower.startswith("account type:")
        or lower.startswith("holdings in")
        or lower.startswith("description")
        or lower.startswith("quantity")
        or lower.startswith("market price")
        or lower.startswith("book value")
        or lower.startswith("(continued")
    )


def _parse_holdings(body: str, currency: str, stmt: ParsedStatement) -> bool:
    """Parse one holdings state machine and quarantine unclaimed data rows."""
    section = None
    saw_holdings = False
    holdings_complete = True
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        i += 1
        if not s:
            continue

        sl = s.lower()
        if sl.startswith("cash") and not RE_HOLDING_LINE.match(s):
            section = "cash"
            continue
        if (("common shares" in sl or "commonshares" in sl)
                and ("canadian" in sl or "foreign" in sl or "us " in sl)):
            section = "equity"
            saw_holdings = True
            continue
        if "preferred" in sl and "shares" in sl:
            section = "equity"
            saw_holdings = True
            continue
        if "mutual fund" in sl or "mutualfund" in sl:
            section = "mutual_fund"
            saw_holdings = True
            continue
        if "exchange traded fund" in sl or "etf" in sl:
            section = "etf"
            saw_holdings = True
            continue
        if sl.startswith("options") or sl == "options":
            section = "option"
            saw_holdings = True
            continue
        if sl.startswith("fixed income") or "bond" in sl:
            section = "bond"
            saw_holdings = True
            continue
        if sl.startswith("equities") or "(continued)" in sl or sl.startswith("total ") \
           or sl.startswith("description") or sl.startswith("quantity") \
           or sl.startswith("holdings in"):
            continue
        if (
            sl.startswith("member - canadian investor protection fund")
            or sl.startswith("account number:")
            or sl.startswith("your investment account statement:")
            or re.fullmatch(r"on [a-z]{3,9} \d{1,2}, \d{4}", sl)
            or re.fullmatch(r"page \d+ of \d+", sl)
        ):
            continue
        if sl.startswith("definitions") or sl.startswith("an explanation"):
            break

        # Holdings-page furniture: numbered footnotes ("4U=US dollars",
        # "4Book costs are converted to Canadian dollars ..."), the
        # squashed portfolio total ("Totalportfolio 341,788.09 ..."), and
        # the account header repeated on every page
        # ("Direct Trading - CDN - 77FF49").
        if re.match(r"4(?:U=|Book (?:costs|values) |The US dollar)", s):
            continue
        if sl.startswith("totalportfolio") or sl.startswith("total portfolio"):
            continue
        if sl.startswith("direct trading - "):
            continue

        legacy_holding = RE_LEGACY_HOLDING_LINE.match(s)
        if legacy_holding:
            qty_s, name, symbol, price_s, book_s, mv_s, _pct_s = legacy_holding.groups()
            if price_s == "N/D" or mv_s == "N/D":
                holdings_complete = False
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="legacy holding has unavailable price or market value",
                ))
                continue
            quantity = parse_money(qty_s)
            if quantity is None:
                holdings_complete = False
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="legacy holding has no valid quantity",
                ))
                continue
            atype = section if section in {"equity", "etf", "mutual_fund", "bond"} else "equity"
            # A printed FundServ code (TDB###/RBF###) identifies a mutual
            # fund regardless of the section header it sat under — the
            # 2016-era layout's headers don't always match the state
            # machine, and an equity-typed fund code resolves to nothing.
            if PRINTED_FUND_CODE_RE.fullmatch(symbol):
                atype = "mutual_fund"
            fund_code = atype == "mutual_fund"
            instr = ParsedInstrument(
                asset_type=atype, symbol=symbol, currency=currency,
                name=name.strip()[:120],
                resolution_method="printed_fund_code" if fund_code else None,
                resolution_confidence=1.0 if fund_code else None,
            )
            stmt.positions.append(ParsedPosition(
                instrument=instr, quantity=quantity,
                avg_cost=None, book_value=parse_money(book_s),
                market_price=parse_money(price_s), market_value=parse_money(mv_s),
                unrealized_pnl=None, currency=currency, raw_line=ln,
            ))
            continue

        # Rows RE_HOLDING_LINE rejects are the N/D-cell variants of the same
        # name-first layout; anything it accepts keeps the existing path.
        name_first = not RE_HOLDING_LINE.match(s) and RE_NAME_FIRST_HOLDING.match(s)
        if name_first:
            (name_part, qty_s, price_s, book_s, mv_s, _gain_s,
             _pct_s) = name_first.groups()
            quantity = parse_money(qty_s)
            if quantity is None:
                holdings_complete = False
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="holding has no valid quantity",
                ))
                continue
            # The ticker prints on a wrapped continuation line under the row
            # ("COMMERCE (CM )"); pure name wraps ("TECHNOLOGIES INC",
            # "CORP") merge into the security name.
            j = i
            extra = ""
            symbol = None
            consumed = i
            while j < len(lines) and j < i + 4:
                nxt = lines[j].strip()
                if not nxt:
                    j += 1
                    continue
                if RE_HOLDING_LINE.match(nxt) or RE_NAME_FIRST_HOLDING.match(nxt) \
                        or RE_LEGACY_HOLDING_LINE.match(nxt):
                    break
                sm2 = RE_TRAIL_SYM.search(nxt)
                if sm2:
                    symbol = sm2.group(1)
                    extra = (extra + " " + nxt[:sm2.start()]).strip()
                    consumed = j + 1
                    break
                extra = (extra + " " + nxt).strip()
                j += 1
            if not symbol:
                holdings_complete = False
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="holding without printed symbol",
                ))
                continue
            name = (name_part + " " + extra).strip()
            atype = section if section in {"equity", "etf", "mutual_fund", "bond"} else "equity"
            fund_code = PRINTED_FUND_CODE_RE.fullmatch(symbol)
            if fund_code:
                atype = "mutual_fund"
            instr = ParsedInstrument(
                asset_type=atype, symbol=symbol, currency=currency,
                name=name[:120],
                resolution_method="printed_fund_code" if fund_code else None,
                resolution_confidence=1.0 if fund_code else None,
            )
            # N/D price and market cells are printed no-value markers for
            # defunct securities: the row is fully captured with the cells
            # it printed, so the position persists and the scope stays
            # complete.
            stmt.positions.append(ParsedPosition(
                instrument=instr, quantity=quantity,
                avg_cost=None, book_value=parse_money(book_s),
                market_price=parse_money(price_s), market_value=parse_money(mv_s),
                unrealized_pnl=None, currency=currency,
                raw_line="\n".join([ln, *lines[i:consumed]]),
            ))
            i = consumed
            continue

        opt_head = re.match(
            r"^(CALL|PUT)\s*[- ]\s*(?:-)?100\s*([A-Z][A-Z0-9.]{0,5})'(\d{2})(?:-US)?\s+(.*)$",
            s,
        )
        if opt_head:
            cp, root, yy, num_part = opt_head.groups()
            j = i
            tail_line = ""
            tm = None
            while j < len(lines) and j < i + 8:
                candidate = lines[j].strip()
                if not candidate or _is_option_interstitial(candidate):
                    j += 1
                    continue
                tail_line = candidate
                tm = RE_OPT_TAIL.match(tail_line)
                break
            if tm:
                dd, mon, strike = tm.groups()
                expiry = _option_expiry(yy, dd, mon)
                nums = re.findall(r"-?[\d,]+(?:\.\d+)?", num_part)
                quantity = parse_money(nums[0]) if nums else None
                if quantity is None:
                    holdings_complete = False
                    stmt.quarantine.append(ParsedQuarantine(
                        raw_line=ln,
                        reason="option holding has no valid quantity",
                    ))
                    i = j + 1
                    continue
                instr = ParsedInstrument(
                    asset_type="option", symbol=root, currency=currency,
                    option_root=root, option_expiry=expiry,
                    option_strike=float(strike), option_type=cp,
                    option_multiplier=100,
                )
                stmt.positions.append(ParsedPosition(
                    instrument=instr,
                    quantity=quantity,
                    avg_cost=None,
                    book_value=parse_money(nums[2]) if len(nums) > 2 else None,
                    market_price=parse_money(nums[1]) if len(nums) > 1 else None,
                    market_value=parse_money(nums[3]) if len(nums) > 3 else None,
                    unrealized_pnl=None,
                    currency=currency,
                    raw_line="\n".join([ln, tail_line]),
                ))
                i = j + 1
                continue
            stmt.quarantine.append(ParsedQuarantine(
                raw_line=ln,
                reason="option holding without strike tail",
            ))
            holdings_complete = False
            continue

        adjusted_option_head = re.match(
            r"^(CALL|PUT)\s*[- ]\s*(?:-)?100\s+(.*)$",
            s,
        )
        if adjusted_option_head:
            cp, num_part = adjusted_option_head.groups()
            j = i
            contract_line = ""
            contract = None
            while j < len(lines) and j < i + 5:
                candidate = lines[j].strip()
                if not candidate or _is_option_interstitial(candidate):
                    j += 1
                    continue
                contract_line = candidate
                combined = f"{s} {candidate}"
                contract = re.search(
                    r"([A-Z0-9][A-Z0-9.+$]{0,10})'(\d{2})(?:-US)?\s*"
                    r"(\d{0,2})([A-Z]{2})@(\d+(?:\.\d+)?)",
                    combined,
                )
                if contract is None:
                    head_contract = re.search(
                        r"([A-Z0-9][A-Z0-9.+$]{0,10})'(\d{2})(?:-US)?",
                        s,
                    )
                    tail_contract = RE_OPT_TAIL.match(candidate)
                    if head_contract and tail_contract:
                        root, yy = head_contract.groups()
                        dd, mon, strike = tail_contract.groups()
                        contract = (root, yy, dd, mon, strike)
                break
            if contract:
                root, yy, dd, mon, strike = (
                    contract.groups() if hasattr(contract, "groups") else contract
                )
                numeric_part = re.sub(
                    r"[A-Z0-9][A-Z0-9.+$]{0,10}'\d{2}(?:-US)?",
                    " ",
                    num_part,
                    count=1,
                )
                nums = re.findall(r"-?[\d,]+(?:\.\d+)?", numeric_part)
                quantity = parse_money(nums[0]) if nums else None
                if quantity is not None:
                    stmt.positions.append(ParsedPosition(
                        instrument=ParsedInstrument(
                            asset_type="option", symbol=root, currency=currency,
                            option_root=root, option_expiry=_option_expiry(yy, dd, mon),
                            option_strike=float(strike), option_type=cp,
                            option_multiplier=100,
                        ),
                        quantity=quantity,
                        avg_cost=None,
                        book_value=parse_money(nums[2]) if len(nums) > 2 else None,
                        market_price=parse_money(nums[1]) if len(nums) > 1 else None,
                        market_value=parse_money(nums[3]) if len(nums) > 3 else None,
                        unrealized_pnl=None,
                        currency=currency,
                        raw_line="\n".join((ln, contract_line)),
                    ))
                    i = j + 1
                    continue
            stmt.quarantine.append(ParsedQuarantine(
                raw_line=ln,
                reason="option holding without complete printed contract",
            ))
            holdings_complete = False
            continue

        m = RE_HOLDING_LINE.match(s)
        if not m:
            if section is not None and re.search(r"\d", s):
                holdings_complete = False
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="unrecognized holding row",
                ))
            continue
        name_part, qty_s, price_s, book_s, mv_s, _unr_s, _pct_s = m.groups()
        sym_match = RE_TRAIL_SYM.search(name_part)
        if sym_match:
            symbol = sym_match.group(1)
            name = name_part[:sym_match.start()].strip()
            evidence_lines = [ln]
        else:
            j = i
            extra = ""
            symbol = None
            consumed = i
            while j < len(lines) and j < i + 4:
                nxt = lines[j].strip()
                if not nxt:
                    j += 1
                    continue
                if RE_HOLDING_LINE.match(nxt):
                    break
                sm2 = RE_TRAIL_SYM.search(nxt)
                if sm2:
                    symbol = sm2.group(1)
                    extra = (extra + " " + nxt[:sm2.start()]).strip()
                    consumed = j + 1
                    break
                extra = (extra + " " + nxt).strip()
                j += 1
            if not symbol:
                holdings_complete = False
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="holding without printed symbol",
                ))
                continue
            name = (name_part + " " + extra).strip()
            evidence_lines = [ln, *lines[i:consumed]]
            i = consumed

        if section == "cash":
            continue
        quantity = parse_money(qty_s)
        if quantity is None:
            holdings_complete = False
            stmt.quarantine.append(ParsedQuarantine(
                raw_line=ln,
                reason="holding has no valid quantity",
            ))
            continue
        atype = section if section in {"equity", "etf", "mutual_fund", "bond"} else "equity"
        fund_code = atype == "mutual_fund" and PRINTED_FUND_CODE_RE.fullmatch(symbol)
        instr = ParsedInstrument(
            asset_type=atype, symbol=symbol, currency=currency,
            name=name[:120],
            resolution_method="printed_fund_code" if fund_code else None,
            resolution_confidence=1.0 if fund_code else None,
        )
        stmt.positions.append(ParsedPosition(
            instrument=instr, quantity=quantity,
            avg_cost=None, book_value=parse_money(book_s),
            market_price=parse_money(price_s), market_value=parse_money(mv_s),
            unrealized_pnl=None, currency=currency,
            raw_line="\n".join(evidence_lines),
        ))
    return saw_holdings and holdings_complete


def _parse_activity(body: str, currency: str, year_end: int,
                    period_end_month: int, stmt: ParsedStatement,
                    *, cash_state: _CashState | None = None) -> bool:
    """Parse one activity state machine without inventing cash or quantities."""
    shared_cash_state = cash_state is not None
    state = cash_state or _CashState()
    closing = None
    cash_lines = state.lines
    cash_complete = False
    cur: ParsedTxn | None = None
    for ln in body.splitlines():
        s = ln.strip()
        if not s:
            continue
        mb = RE_BEGIN_BAL.search(s) or RE_LEGACY_BEGIN_BAL.search(s)
        if mb:
            amount = parse_money(mb.group(1))
            if amount is None:
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="opening cash balance has no valid amount",
                ))
            else:
                state.opening = amount
                cash_lines.append(ln)
            continue
        me = RE_END_BAL.search(s) or RE_LEGACY_END_BAL.search(s)
        if me:
            amount = parse_money(me.group(1))
            if amount is None:
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="closing cash balance has no valid amount",
                ))
            else:
                closing = amount
                cash_lines.append(ln)
                cash_complete = True
            continue
        low = s.lower()
        if low.startswith("pending activity in your account"):
            break
        if "beginning cash balance" in low or "cash-opening balance" in low:
            stmt.quarantine.append(ParsedQuarantine(
                raw_line=ln,
                reason="opening cash balance has no valid amount",
            ))
            continue
        if "ending cash balance" in low or "cash-closing balance" in low:
            stmt.quarantine.append(ParsedQuarantine(
                raw_line=ln,
                reason="closing cash balance has no valid amount",
            ))
            continue
        # noise
        if (s.startswith("Order-Execution-Only") or s.startswith("TD Waterhouse")
            or s.startswith("Page ") or s.startswith("Member ")
            or s.startswith("(continued") or s.startswith("Activity in your account")
            or s.startswith("Date Activity") or s.startswith("Cash")
            or s.startswith("This period") or s.startswith("Earnings/")
            or s.startswith("Account number:") or s.startswith("Account type:")
            or s.startswith("Your investment") or s.startswith("i Important")
            or s.startswith("Details of")):
            continue

        m = RE_ACT_DATE.match(s)
        if not m:
            # continuation
            if cur is None:
                cur = next(
                    (
                        transaction
                        for transaction in reversed(stmt.transactions)
                        if transaction.currency == currency
                    ),
                    None,
                )
            if cur is not None:
                cur.description = (cur.description or "") + " | " + s
                _apply_reinvestment_plan_continuation(cur, s)
                _apply_web_banking_continuation(cur, s)
            elif re.search(r"\d", s):
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="unrecognized activity row",
                ))
            continue
        mon_s, dd_s, rest = m.groups()
        mn = _MON.get(mon_s[:3].upper())
        if not mn:
            continue
        # Year inference: if month > period_end_month, txn is from prior year
        ty = year_end if mn <= period_end_month else year_end - 1
        try:
            trade_date = date(ty, mn, int(dd_s)).isoformat()
        except ValueError:
            stmt.quarantine.append(ParsedQuarantine(
                raw_line=ln,
                reason="activity row has an invalid date",
            ))
            state.uncertain = True
            continue

        txn_type = _classify(rest)
        if txn_type is None:
            stmt.quarantine.append(ParsedQuarantine(
                raw_line=ln,
                reason=f"unknown verb in '{rest[:60]}'",
            ))
            if re.search(r"\d", rest):
                state.uncertain = True
            cur = None
            continue

        # Strip the verb phrase from rest to get description
        verb_key = next(
            (
                k
                for k in sorted(ACT_VERBS, key=len, reverse=True)
                if rest.casefold().startswith(k.casefold())
            ),
            None,
        )
        desc = rest[len(verb_key):].strip() if verb_key else rest

        # Pull instrument + numbers
        instrument: ParsedInstrument | None = None
        qty = price = amount = None
        printed_signed = False
        nums = re.findall(r"-?[\d,]+(?:\.\d+)?", desc)
        # An option token in description?
        m_opt = RE_OPT_TOKEN.search(desc)
        if m_opt:
            cp, root, yy, dd, mon, strike = m_opt.groups()
            if not _valid_td_option_token(yy, dd, mon):
                m_opt = None
        if m_opt:
            cp, root, yy, dd, mon, strike = m_opt.groups()
            expiry = _option_expiry(yy, dd, mon)
            instrument = ParsedInstrument(
                asset_type="option", symbol=root, currency=currency,
                option_root=root, option_expiry=expiry,
                option_strike=float(strike) if strike else None,
                option_type=cp, option_multiplier=100,
            )
            tail = desc[m_opt.end():]
            tnums = re.findall(r"-?[\d,]+(?:\.\d+)?", tail)
            if txn_type in {"buy", "sell"}:
                if len(tnums) < 4:
                    stmt.quarantine.append(ParsedQuarantine(
                        raw_line=ln,
                        reason="option buy/sell row has no complete numeric tail",
                    ))
                    cur = None
                    continue
                qty = parse_money(tnums[0])
                price = parse_money(tnums[1])
                amount = parse_money(tnums[2])
                if qty is None:
                    stmt.quarantine.append(ParsedQuarantine(
                        raw_line=ln,
                        reason="option buy/sell row has no valid quantity",
                    ))
                    cur = None
                    continue
                # Map to canonical option_*_to_open/close: open if direction
                # adds to a position, close if reduces. With TD we don't know
                # prior position; leave generic and let downstream infer.
                if txn_type == "buy":
                    if qty > 0:
                        txn_type = "option_buy_to_open"
                    elif qty < 0:
                        txn_type = "option_buy_to_close"
                else:
                    if qty < 0:
                        txn_type = "option_sell_to_open"
                    elif qty > 0:
                        txn_type = "option_sell_to_close"
            elif txn_type in {"option_expiration", "option_exercise", "option_assignment"} and tnums:
                qty = parse_money(tnums[0])
        elif verb_key and verb_key.casefold() == "disposition":
            numeric_tail = RE_ACTIVITY_NUMERIC_TAIL.search(desc)
            if numeric_tail is None or numeric_tail.group(4) is not None:
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="disposition row has no quantity/amount/balance tail",
                ))
                state.uncertain = True
                cur = None
                continue
            qty = parse_money(numeric_tail.group(1))
            amount = parse_money(numeric_tail.group(2))
            instrument = _instrument_from_description(
                desc[:numeric_tail.start()].strip(),
                currency,
            )
            if qty is None or instrument is None:
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="disposition row lacks a valid quantity or instrument",
                ))
                state.uncertain = True
                cur = None
                continue
        elif txn_type in {"buy", "sell"}:
            legacy_trade = RE_LEGACY_TRADE_ROW.match(desc)
            numeric_tail = RE_ACTIVITY_NUMERIC_TAIL.search(desc)
            if legacy_trade is not None:
                qty = parse_money(legacy_trade.group(1))
                instrument = _instrument_from_description(
                    legacy_trade.group(2).strip(),
                    currency,
                )
                price = parse_money(legacy_trade.group(3))
                amount = parse_money(legacy_trade.group(4))
            elif numeric_tail is None:
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="buy/sell row has no complete numeric tail",
                ))
                cur = None
                continue
            else:
                qty = parse_money(numeric_tail.group(1))
                price = parse_money(numeric_tail.group(2))
                amount = parse_money(numeric_tail.group(3))
                instrument = _instrument_from_description(
                    desc[:numeric_tail.start()].strip(),
                    currency,
                )
            if qty is None:
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="buy/sell row has no valid quantity",
                ))
                cur = None
                continue
            if instrument is None:
                stmt.quarantine.append(ParsedQuarantine(
                    raw_line=ln,
                    reason="buy/sell row has no printed or reviewed instrument identity",
                ))
                cur = None
                continue
        elif txn_type in {"transfer_in", "transfer_out", "journal"}:
            numeric_tail = RE_ACTIVITY_NUMERIC_TAIL.search(desc)
            transfer_amount = (
                parse_money(numeric_tail.group(2))
                if numeric_tail is not None and numeric_tail.group(4) is None
                else None
            )
            if transfer_amount == 0.0:
                # In-kind transfers print quantity, zero cash, then running
                # balance. Preserve the security movement and its zero cash
                # effect instead of treating the quantity as dollars.
                qty = parse_money(numeric_tail.group(1))
                amount = parse_money(numeric_tail.group(2))
                printed_signed = numeric_tail.group(2).lstrip().startswith("-")
                instrument = _instrument_from_description(
                    desc[:numeric_tail.start()].strip(),
                    currency,
                )
                if instrument is None:
                    qty = None
            elif (
                numeric_tail is not None
                and numeric_tail.group(4) is None
                and numeric_tail.group(3) is not None
                and transfer_amount is not None
            ):
                # Fund-series exchange legs print quantity, the transferred
                # value, and the running balance. Both the movement and the
                # printed value are real row data; the balance column is a
                # running total, not the row's cash.
                qty = parse_money(numeric_tail.group(1))
                amount = transfer_amount
                printed_signed = numeric_tail.group(2).lstrip().startswith("-")
                instrument = _instrument_from_description(
                    desc[:numeric_tail.start()].strip(),
                    currency,
                )
            if instrument is None and nums:
                amount = parse_money(nums[-2]) if len(nums) >= 2 else parse_money(nums[-1])
                printed_signed = (nums[-2] if len(nums) >= 2 else nums[-1]).startswith("-")
        elif txn_type in {"dividend", "distribution", "interest_income",
                          "tax_withholding", "return_of_capital"} and nums:
            # Last number is running cash balance; second-last is amount.
            if len(nums) >= 2:
                amount = parse_money(nums[-2])
                printed_signed = nums[-2].startswith("-")
            else:
                amount = parse_money(nums[-1])
                printed_signed = nums[-1].startswith("-")
            if RE_INTEREST_MEMO.match(desc):
                # "INTEREST TO JUL 16" is a cash memo, not a security event:
                # the row never claims an instrument, so it is stored
                # deliberately without one (no unresolved-identity marker).
                instrument = None
            else:
                instrument = _instrument_from_description(desc, currency)
        elif txn_type == "stock_split":
            numeric_tail = RE_ACTIVITY_NUMERIC_TAIL.search(desc)
            if numeric_tail is None or numeric_tail.group(4) is not None:
                # Unrecognized split shape: keep the generic amount-only
                # treatment (second-to-last number, last is the balance).
                if nums:
                    amount = parse_money(nums[-2]) if len(nums) >= 2 else parse_money(nums[-1])
                    printed_signed = (nums[-2] if len(nums) >= 2 else nums[-1]).startswith("-")
            else:
                amount = parse_money(numeric_tail.group(2))
                split_qty = parse_money(numeric_tail.group(1))
                if amount is not None and amount != 0.0 and split_qty is not None:
                    # A two-leg book-value swap (non-zero amount) prints the
                    # out/in share quantities as signed deltas — TD calls
                    # them Reverse Split. A 0.00 split note instead prints
                    # the resulting total, which is not a delta, so its
                    # quantity stays uncaptured.
                    qty = split_qty
                    instrument = _instrument_from_description(
                        desc[:numeric_tail.start()].strip(),
                        currency,
                    )
                    if instrument is not None:
                        txn_type = "journal"
        elif nums:
            amount = parse_money(nums[-2]) if len(nums) >= 2 else parse_money(nums[-1])
            printed_signed = (nums[-2] if len(nums) >= 2 else nums[-1]).startswith("-")

        if amount is not None:
            if verb_key and verb_key.casefold() == "web banking":
                upper_desc = desc.upper()
                if " TSF TO " in f" {upper_desc} " or amount < 0:
                    txn_type = "transfer_out"
                else:
                    txn_type = "transfer_in"
            # Canonical cash directions apply only when TD prints an unsigned
            # amount; a printed sign is source evidence and is preserved.
            if not printed_signed:
                if txn_type in _CASH_OUTFLOW_TYPES:
                    amount = -abs(amount)
                elif txn_type in _CASH_INFLOW_TYPES:
                    amount = abs(amount)

        cur = ParsedTxn(
            trade_date=trade_date, settle_date=None, txn_type=txn_type,
            instrument=instrument, quantity=qty, price=price,
            gross_amount=None, commission=None, other_fees=None,
            net_amount=amount, currency=currency,
            description=desc.strip(), raw_line=ln,
        )
        stmt.transactions.append(cur)

    if closing is not None:
        raw_line = "\n".join(cash_lines) or None
        existing = next(
            (cash for cash in stmt.cash_balances if cash.currency == currency),
            None,
        )
        if existing is None:
            stmt.cash_balances.append(ParsedCashBalance(
                currency=currency, opening_balance=state.opening,
                closing_balance=closing,
                raw_line=raw_line,
            ))
        else:
            if existing.opening_balance is None:
                existing.opening_balance = state.opening
            existing.closing_balance = closing
            existing.raw_line = "\n".join(
                part for part in (existing.raw_line, raw_line) if part
            ) or None
        state.opening = None
        state.lines.clear()
    elif state.opening is not None and not shared_cash_state:
        stmt.quarantine.append(ParsedQuarantine(
            raw_line="\n".join(cash_lines),
            reason="opening cash balance has no matching valid closing balance",
        ))
    return cash_complete and not state.uncertain


# ---------------------------------------------------------------- Parser
class TDParser:
    NAME = "td"
    VERSION = "2.9.0"

    def can_handle(self, folder_name: str, first_page_text: str) -> bool:
        if folder_name == "TD Webbroker":
            return True
        return ("TD Direct Investing" in first_page_text
                or "TD Waterhouse Canada" in first_page_text)

    def parse(self, pdf: PdfText) -> ParseResult:
        result = ParseResult(parser_name=self.NAME, parser_version=self.VERSION)
        is_summary = _is_summary(pdf.relpath)
        page_index = PageTextIndex.from_pdf(
            pdf,
            # The attached annual performance/fees report pages are their own
            # category: monthly statements must not parse them as rows (the
            # "January 1 to December 31" headers would otherwise create
            # bogus year-long monthly statements). Summary files are annual
            # reports themselves and keep every page.
            include_page=lambda number, page: (
                _is_td_disclosure_page(number, page)
                and (is_summary or not _is_td_annual_report_page(page))
            ),
        )
        text = page_index.text

        if _is_summary(pdf.relpath):
            ym = re.search(r"_(\d{4})_(?:mid_year_)?summary", pdf.relpath)
            if not ym:
                ym = re.search(r"(\d{4})", pdf.relpath)
            an = re.search(r"Statement_([A-Z0-9]+)_", pdf.relpath)
            if ym and an:
                year = int(ym.group(1))
                shell = ParsedStatement(
                    account=ParsedAccount(account_number=an.group(1),
                                          account_type="Direct Trading",
                                          base_currency="CAD"),
                    period_start=f"{year}-01-01",
                    period_end=f"{year}-12-31",
                    statement_type="annual",
                    page_numbers=page_index.all_pages,
                )
                # The summary file carries the same performance-report pages
                # as a December statement; attach their printed numbers.
                report = _parse_td_performance_pages(
                    {number: text for number, text in enumerate(pdf.pages, 1)}
                )
                if report is not None:
                    shell.annual_performance = report.annual_performance
                result.statements.append(shell)
            attach_source_spans(pdf, result, parser_name=self.NAME)
            return result

        chunks = _statement_chunks(text)
        if not chunks:
            result.errors.append("could not parse period")
            return result

        # A statement may repeat an account/currency header after a page break.
        # Aggregate all of those fragments before declaring its snapshot scopes so
        # a source cannot emit duplicate account-period statement identities.
        statements: dict[tuple[str, str, str, str], ParsedStatement] = {}
        scope_state: dict[tuple[str, str, str, str], _ScopeState] = {}
        for ps, pe, chunk_text, chunk_start, _chunk_end in chunks:
            period_end_month = int(pe[5:7])
            period_end_year = int(pe[:4])

            for sub in _split_subs(
                chunk_text,
                base_offset=chunk_start,
                page_index=page_index,
            ):
                sub.page_numbers = _owned_sub_pages(pdf, sub.page_numbers, sub.currency)
                key = (ps, pe, sub.account_number, sub.currency)
                stmt = statements.get(key)
                if stmt is None:
                    stmt = ParsedStatement(
                        account=ParsedAccount(
                            account_number=f"{sub.account_number}-{sub.currency[:3]}",
                            account_type="Direct Trading",
                            base_currency=sub.currency,
                        ),
                        period_start=ps, period_end=pe, statement_type="monthly",
                        page_numbers=sub.page_numbers,
                    )
                    statements[key] = stmt
                    scope_state[key] = _ScopeState()
                else:
                    stmt.page_numbers = tuple(
                        sorted(set(stmt.page_numbers) | set(sub.page_numbers))
                    )
                state = scope_state[key]
                _capture_reported_totals(sub.text, state)
                hm = re.search(r"Holdings in your account|^Holdings\b", sub.text, re.MULTILINE)
                am = re.search(
                    r"Activity in your account this period|^Activities\b",
                    sub.text,
                    re.MULTILINE,
                )
                if hm:
                    state.positions_seen = True
                    holdings_end = am.start() if am and am.start() > hm.start() else len(sub.text)
                    quarantine_start = len(stmt.quarantine)
                    try:
                        fragment_complete = _parse_holdings(
                            sub.text[hm.end():holdings_end], sub.currency, stmt
                        )
                        state.positions_complete = (
                            state.positions_complete and fragment_complete
                        )
                    except Exception as exc:
                        state.positions_complete = False
                        quarantine = ParsedQuarantine(
                            raw_line="<holdings section>",
                            reason=f"holdings section parse error: {exc}",
                        )
                        stmt.quarantine.append(quarantine)
                    state.position_issues.extend(_scope_issues_from_quarantine(
                        stmt.quarantine[quarantine_start:],
                        section_type="positions",
                    ))
                if am:
                    state.cash_seen = True
                    tail = sub.text[am.end():]
                    if hm and hm.start() > am.start():
                        tail = tail[:hm.start() - am.end()]
                    end = re.search(r"Details of investment income|^\s*Disclosures",
                                    tail, re.MULTILINE)
                    act_body = tail[:end.start()] if end else tail
                    quarantine_start = len(stmt.quarantine)
                    try:
                        fragment_complete = _parse_activity(
                            act_body, sub.currency, period_end_year,
                            period_end_month, stmt, cash_state=state.cash,
                        )
                        state.cash_complete = (
                            state.cash_complete or fragment_complete
                        ) and not state.cash_failed
                    except Exception as exc:
                        state.cash_failed = True
                        state.cash_complete = False
                        quarantine = ParsedQuarantine(
                            raw_line="<activity section>",
                            reason=f"activity section parse error: {exc}",
                        )
                        stmt.quarantine.append(quarantine)
                    fragment_issues = _scope_issues_from_quarantine(
                        stmt.quarantine[quarantine_start:],
                        section_type="cash",
                    )
                    if fragment_issues:
                        state.cash_failed = True
                        state.cash_complete = False
                    state.cash_issues.extend(fragment_issues)

        for key, stmt in statements.items():
            state = scope_state[key]
            currency = stmt.account.base_currency
            if state.cash.opening is not None:
                quarantine = ParsedQuarantine(
                    raw_line="\n".join(state.cash.lines),
                    reason="opening cash balance has no matching valid closing balance",
                )
                stmt.quarantine.append(quarantine)
                state.cash_failed = True
                state.cash_complete = False
                state.cash_issues.extend(_scope_issues_from_quarantine(
                    [quarantine], section_type="cash",
                ))
            position_scopes = {}
            if state.positions_seen or any(
                position.currency == currency for position in stmt.positions
            ):
                position_scopes[currency] = (
                    "complete" if state.positions_complete else "unknown"
                )
            cash_scopes = {}
            if state.cash_seen or any(
                cash.currency == currency for cash in stmt.cash_balances
            ):
                cash_scopes[currency] = (
                    "complete" if state.cash_complete else "unknown"
                )
            declare_snapshot_scopes(
                stmt,
                position_scopes=position_scopes,
                cash_scopes=cash_scopes,
                position_issues={currency: state.position_issues},
                cash_issues={currency: state.cash_issues},
            )
            for scope in stmt.snapshot_sets:
                if scope.currency != currency:
                    continue
                if scope.section_type == "positions" and state.positions_total is not None:
                    scope.reported_total = state.positions_total
                    if state.positions_total_line:
                        scope.source_span = SourceSpan(
                            raw_text=state.positions_total_line,
                            parser_rule="td:positions-total",
                        )
                elif scope.section_type == "cash" and state.cash_total is not None:
                    scope.reported_total = state.cash_total
                    if state.cash_total_line:
                        scope.source_span = SourceSpan(
                            raw_text=state.cash_total_line,
                            parser_rule="td:cash-total",
                        )
            if state.closing_total is not None:
                stmt.snapshot_sets.append(ParsedSnapshotSet(
                    currency=currency,
                    section_type="summary",
                    completeness="complete",
                    opening_total=state.opening_total,
                    reported_change=state.reported_change,
                    reported_total=state.closing_total,
                    validation_status="valid",
                    source_span=SourceSpan(
                        raw_text="\n".join(state.summary_lines) or None,
                        parser_rule="td:account-summary",
                    ),
                ))
            result.statements.append(stmt)
        # December statements attach the calendar-year performance and fees
        # reports; extract them as their own annual statement.
        annual_pages = {
            number: text for number, text in enumerate(pdf.pages, start=1)
            if any(marker in text for marker in _TD_ANNUAL_PAGE_MARKERS)
        }
        if annual_pages:
            annual = _parse_td_performance_pages(annual_pages)
            if annual is not None:
                result.statements.append(annual)
        quarantine_unsupported_rows(result)
        attach_source_spans(pdf, result, parser_name=self.NAME)
        return result


register(TDParser())
