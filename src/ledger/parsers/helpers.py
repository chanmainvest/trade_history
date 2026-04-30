"""Shared parser helpers: money/date/option parsing."""
from __future__ import annotations

import re
from datetime import date, datetime

# Money: handles "1,234.56", "(1,234.56)", "1234.56-", "-1,234.56", "$1,234.56"
_MONEY_RE = re.compile(r"^\s*\$?\s*\(?-?[\d,]+(\.\d+)?\)?-?\s*$")


def parse_money(s: str | None) -> float | None:
    if s is None:
        return None
    raw = str(s).strip()
    if not raw or raw in {"-", "--", "N/A", "n/a"}:
        return None
    if not _MONEY_RE.match(raw):
        # fall back to broader cleanup
        pass
    neg = False
    raw2 = raw.replace("$", "").replace(",", "").strip()
    if raw2.startswith("(") and raw2.endswith(")"):
        neg = True
        raw2 = raw2[1:-1]
    if raw2.endswith("-"):
        neg = True
        raw2 = raw2[:-1]
    if raw2.startswith("-"):
        neg = True
        raw2 = raw2[1:]
    try:
        v = float(raw2)
    except ValueError:
        return None
    return -v if neg else v


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def parse_date(s: str, *, year_hint: int | None = None) -> str | None:
    """Parse a wide variety of date formats. Returns ISO YYYY-MM-DD or None."""
    if not s:
        return None
    s2 = s.strip().rstrip(",")
    # ISO already?
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%d %B %Y",
                "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y",
                "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s2, fmt).date().isoformat()
        except ValueError:
            pass

    # "MAY 31" or "Jun 18" with year_hint
    m = re.match(r"^([A-Za-z]+)[ .\-]+(\d{1,2})$", s2)
    if m and year_hint:
        mon = _MONTHS.get(m.group(1).lower())
        if mon:
            try:
                return date(year_hint, mon, int(m.group(2))).isoformat()
            except ValueError:
                return None

    # "MMM dd" => use year_hint
    m = re.match(r"^([A-Za-z]+)\s+(\d{1,2})\b", s2)
    if m and year_hint:
        mon = _MONTHS.get(m.group(1).lower())
        if mon:
            try:
                return date(year_hint, mon, int(m.group(2))).isoformat()
            except ValueError:
                pass
    return None


def parse_option_expiry(token: str, *, year_hint: int | None = None) -> str | None:
    """Parse option expiry tokens like '25 JA' (YY MMM), 'JA 25', '01/17/25'.

    Returns ISO date for the THIRD FRIDAY of the month if no day is supplied.
    """
    t = token.strip()
    # mm/dd/yy or mm/dd/yyyy
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$", t)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None

    # YY MMM e.g. "25 JA"
    m = re.match(r"^(\d{2})\s*([A-Za-z]{2,3})$", t)
    if m:
        y = 2000 + int(m.group(1))
        mon = _option_mon(m.group(2))
        if mon:
            return _third_friday(y, mon).isoformat()

    # MMM YY
    m = re.match(r"^([A-Za-z]{2,3})\s*(\d{2})$", t)
    if m:
        mon = _option_mon(m.group(1))
        y = 2000 + int(m.group(2))
        if mon:
            return _third_friday(y, mon).isoformat()

    return None


_OPT_MON = {
    "JA": 1, "FE": 2, "MR": 3, "AP": 4, "MY": 5, "JN": 6,
    "JL": 7, "AU": 8, "SP": 9, "OC": 10, "NV": 11, "DC": 12,
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def _option_mon(s: str) -> int | None:
    return _OPT_MON.get(s.upper())


def _third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    # weekday(): Mon=0..Sun=6. Friday=4.
    offset = (4 - d.weekday()) % 7
    first_friday = 1 + offset
    return date(year, month, first_friday + 14)


def normalize_symbol(sym: str) -> str:
    return re.sub(r"\s+", "", sym.upper())
