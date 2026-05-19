"""Best-effort security-name → ticker resolution.

Used by parsers when a statement line shows a security name but no
parens-symbol (e.g. RBC's bond / fixed-income tail rows, or any plain-text
"BOUGHT ISHARES 20 PLUS YEAR TREASURY ..." kind of line).

The map is intentionally small and conservative — we'd rather store the
descriptive name as a synthetic symbol than mis-map a row to the wrong
ticker. Add entries here as you encounter new statement quirks.
"""
from __future__ import annotations

import re

# Match in priority order; first match wins. Each pattern is a regex run
# against the upper-cased description with whitespace collapsed.
NAME_TO_TICKER: list[tuple[re.Pattern[str], str, str]] = [
    # (pattern, ticker, asset_type)
    (re.compile(r"\bISHARES\b.*\b20\+?\s*(?:PLUS\s+)?YEAR\s+TREASURY\b"), "TLT", "etf"),
    (re.compile(r"\bISHARES\b.*\b7\s*-?\s*10\s*YEAR\s+TREASURY\b"),       "IEF", "etf"),
    (re.compile(r"\bISHARES\b.*\b1\s*-?\s*3\s*YEAR\s+TREASURY\b"),        "SHY", "etf"),
    (re.compile(r"\bISHARES\b.*\bS&P\s*500\b"),                            "IVV", "etf"),
    (re.compile(r"\bISHARES\b.*\bRUSSELL\s+2000\b"),                       "IWM", "etf"),
    (re.compile(r"\bVANGUARD\b.*\bS&P\s*500\b"),                           "VOO", "etf"),
    (re.compile(r"\bVANGUARD\b.*\bTOTAL\s+STOCK\s+MARKET\b"),              "VTI", "etf"),
    (re.compile(r"\bSPDR\b.*\bS&P\s*500\b"),                               "SPY", "etf"),
    (re.compile(r"\bDIREXION\b.*\bSEMI\s*COND.*\bBEAR\b"),                 "SOXS", "etf"),
    (re.compile(r"\bDIREXION\b.*\bSEMI\s*COND.*\bBULL\b"),                 "SOXL", "etf"),
    (re.compile(r"\bPROSHARES\b.*\bULTRAPRO\s+QQQ\b"),                     "TQQQ", "etf"),
    (re.compile(r"\bPROSHARES\b.*\bULTRASHORT\s+QQQ\b"),                   "SQQQ", "etf"),
    (re.compile(r"\bINVESCO\b.*\bQQQ\b"),                                  "QQQ",  "etf"),
]

# Leading words that aren't part of the security name (verbs, qualifiers).
_LEADING_NOISE = {
    "BOUGHT", "SOLD", "BUY", "SELL",
    "DIVIDEND", "DISTRIBUTION", "INTEREST",
    "REINVEST", "REINVESTED",
    "TRANSFER", "JOURNAL", "DEPOSIT", "WITHDRAWAL", "WITHDRAW",
    "TAX", "FEE", "ADJUSTMENT",
    "OPENING", "CLOSING",
}


def strip_leading_verbs(desc: str) -> str:
    """Drop one or more leading 'verb' words from a free-form description."""
    toks = desc.strip().split()
    while toks and toks[0].upper().rstrip(":") in _LEADING_NOISE:
        toks = toks[1:]
    return " ".join(toks)


def resolve_ticker(desc: str) -> tuple[str, str] | None:
    """Return ``(ticker, asset_type)`` if the description matches a known name."""
    if not desc:
        return None
    u = re.sub(r"\s+", " ", desc.upper())
    for pat, tkr, atype in NAME_TO_TICKER:
        if pat.search(u):
            return tkr, atype
    return None


def synthetic_symbol(desc: str, max_len: int = 24) -> str:
    """Turn a descriptive name into a stable synthetic 'symbol' that at least
    isn't the leading verb. Strips noise words and tail numbers.
    """
    cleaned = strip_leading_verbs(desc).upper()
    cleaned = re.sub(r"[↑↓\u2191\u2193]+", "", cleaned)
    cleaned = re.sub(r"\s[-+]?\d[\d,]*\.?\d*", "", cleaned)
    cleaned = re.sub(r"[^A-Z0-9 ]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    sym = "_".join(cleaned.split()[:4])[:max_len] or "UNKNOWN"
    return sym
