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
    (re.compile(r"\bISHARES\s*20\s*PLUS\s*YEAR\s*TREASURY\b"),             "TLT", "etf"),
    (re.compile(r"\bRBB\s+FD\s+INC\b.*\bUS\s+TREASURY\s+6\s+MONTH\s+BILL\s+ETF\b"), "XBIL", "etf"),
    (re.compile(r"\bRBB\s+(?:FD|FUND)\s+INC\b.*\bUS\s+TREASURY\s+12\s+MONTH\s+BILL\s+ETF\b"), "OBIL", "etf"),
    (re.compile(r"\bRBB\s+FD\s+INC\b.*\bUS\s+TREASURY\s+2\s+YEAR\s+NOTE\s+ETF\b"), "UTWO", "etf"),
    (re.compile(r"\bHORIZONS\b.*\b0\s*-?\s*3\s+MONTH\s+U\s*S\s*T\b.*\bBILL\s+ETF\b"), "UBIL.U", "etf"),
    (re.compile(r"\bGLOBAL\s+X\b.*\b0\s*-?\s*3\s+MONTH\s+U\s*S\b.*\bT\s*BILL\s+ETF\b"), "UBIL.U", "etf"),
    (re.compile(r"\bHORIZONS\b.*\bU\s*S\s*DLR\s+CURRENCY\b"),              "DLR.U", "etf"),
    (re.compile(r"\bGLOBAL\s+X\b.*\bUS\s+DLR\s+CURRENCY\b"),               "DLR.U", "etf"),
    (re.compile(r"\bISHARES\s+BITCOIN\s+(?:TR|TRUST)\b"),                  "IBIT", "etf"),
    (re.compile(r"\bISHARES\s+ETHEREUM\s+TRUST\b"),                        "ETHA", "etf"),
    (re.compile(r"\bISHARES\b.*\bMSCI\s+SINGAPORE\s+ETF\b"),              "EWS", "etf"),
    (re.compile(r"\bISHARES\b.*\bMSCI\s+MEXICO\s+ETF\b"),                 "EWW", "etf"),
    (re.compile(r"\bISHARES\b.*\bMSCI\s+BRAZIL\s+ETF\b"),                 "EWZ", "etf"),
    (re.compile(r"\bISHARES\b.*\bMSCI\s+INDONESIA\s+ETF\b"),              "EIDO", "etf"),
    (re.compile(r"\bISHARES\b.*\bMSCI\s+MALAYSIA\s+ETF\b"),               "EWM", "etf"),
    (re.compile(r"\bISHARES\b.*\bMSCI\s+INDIA\s+INDEX\s+FD\b"),           "INDA", "etf"),
    (re.compile(r"\bISHARES\s+TIPS\s+BOND\s+ETF\b"),                       "TIP", "etf"),
    (re.compile(r"\bISHARES\s+IBOXX\b.*\bINVESTMENT\b.*\bCORPORATE\s+BOND\s+ETF\b"), "LQD", "etf"),
    (re.compile(r"\bVANECK\b.*\bVIETNAM\s+ETF\b"),                         "VNM", "etf"),
    (re.compile(r"\bINVESCO\s+DB\b.*\bAGRICULTURE\s+FUND\b"),              "DBA", "etf"),
    (re.compile(r"\bU\s*S\s+GLOBAL\s+JETS\s+ETF\b"),                      "JETS", "etf"),
    (re.compile(r"\bTESLA\s+INC\b"),                                          "TSLA", "equity"),
    (re.compile(r"\bSUPER\s+MICRO\s+COMPUTER\s+INC\b"),                     "SMCI", "equity"),
    (re.compile(r"\bQUANTUM\s+COMPUTING\s+INC\b"),                          "QUBT", "equity"),
    (re.compile(r"\bCANADIAN\s+NATURAL\s+RESOURCES\b"),                    "CNQ", "equity"),
    (re.compile(r"\bNUTRIEN\s+LTD\b"),                                       "NTR", "equity"),
    (re.compile(r"\bSPROTT\s+INC\b"),                                        "SII", "equity"),
    (re.compile(r"\bCAMECO\s+CORP\b"),                                       "CCJ", "equity"),
    (re.compile(r"\bADOBE\s+INC\b"),                                        "ADBE", "equity"),
    (re.compile(r"\bPFIZER\s+INC\b"),                                       "PFE", "equity"),
    (re.compile(r"\bNVIDIA\s+CORP\b"),                                       "NVDA", "equity"),
    (re.compile(r"\bSMURFIT\s+WESTROCK\s+PLC\b"),                           "SW", "equity"),
    (re.compile(r"\bCHEVRON\s+CORPORATION\b"),                              "CVX", "equity"),
    (re.compile(r"\bEXXON\s+MOBIL\s+CORP\b"),                               "XOM", "equity"),
    (re.compile(r"\bWEYERHAEUSER\s+CO\b"),                                  "WY", "equity"),
    (re.compile(r"\bBHP\s+GROUP\s+LIMITED\b"),                              "BHP", "equity"),
    (re.compile(r"\bFARMLAND\s+PARTNERS\s+INC\b"),                          "FPI", "equity"),
    (re.compile(r"\bGLADSTONE\s+LAND\s+CORPORATION\b"),                    "LAND", "equity"),
    (re.compile(r"\bTEXAS\s+PACIFIC\s+LAND\s+CORPORATION\b"),              "TPL", "equity"),
    (re.compile(r"\bAIRBNB\s+INC\s+(?:CL\s*-?\s*A|CLASS\s+A)\b"),          "ABNB", "equity"),
    (re.compile(r"\bPDD\s+HOLDINGS\s+INC\s+ADR\b"),                         "PDD", "equity"),
    (re.compile(r"\bISHARES\b.*\b7\s*-?\s*10\s*YEAR\s+TREASURY\b"),       "IEF", "etf"),
    (re.compile(r"\bISHARES\b.*\b1\s*-?\s*3\s*YEAR\s+TREASURY\b"),        "SHY", "etf"),
    (re.compile(r"\bISHARES\b.*\bS&P\s*500\b"),                            "IVV", "etf"),
    (re.compile(r"\bISHARES\b.*\bRUSSELL\s+2000\b"),                       "IWM", "etf"),
    (re.compile(r"\bVANGUARD\b.*\bS&P\s*500\b"),                           "VOO", "etf"),
    (re.compile(r"\bVANGUARD\b.*\bTOTAL\s+STOCK\s+MARKET\b"),              "VTI", "etf"),
    (re.compile(r"\bVANGUARD\b.*\bSHORT\s+TERM\s+CORPORATE\b"),            "VCSH", "etf"),
    (re.compile(r"\bVANGUARD\b.*\bINTERMEDIATE\s+TERM\b.*\bCORPORATE\b"), "VCIT", "etf"),
    (re.compile(r"\bSPDR\b.*\bS&P\s*500\b"),                               "SPY", "etf"),
    (re.compile(r"\bSPDR\s+GOLD\s+TR\b"),                                   "GLD", "etf"),
    (re.compile(r"\bDIREXION\b.*\bSEMI\s*COND.*\bBEAR\b"),                 "SOXS", "etf"),
    (re.compile(r"\bDIREXION\b.*\bSEMI\s*COND.*\bBULL\b"),                 "SOXL", "etf"),
    (re.compile(r"\bPROSHARES\b.*\bULTRAPRO\s+QQQ\b"),                     "TQQQ", "etf"),
    (re.compile(r"\bPROSHARES\b.*\bULTRASHORT\s+QQQ\b"),                   "SQQQ", "etf"),
    (re.compile(r"\bINVESCO\b.*\bQQQ\b"),                                  "QQQ",  "etf"),
    (re.compile(r"\bSIMPLIFY\b.*\bINTEREST\s+RATE\s+HEDGE\b"),             "PFIX", "etf"),
    (re.compile(r"\bSPROTT\b.*\bACTIVE\s+GOLD\s+&?\s*SILVER\b.*\bMINERS\s+ETF\b"), "GBUG", "etf"),
    (re.compile(r"\bSPROTT\b.*\bURANIUM\s+MINERS\s+ETF\b"),                "URNM", "etf"),
    (re.compile(r"\bMACKENZIE\b.*\bUS\s+TIPS\s+INDEX\b"),                  "QTIP", "etf"),
    (re.compile(r"\bBLACKBERRY\s+LTD\b"),                                   "BB", "equity"),
    (re.compile(r"\bFIRST\s+MAJESTIC\s+SILVER\s+CORP\b"),                  "AG", "equity"),
    (re.compile(r"\bHECLA\s+MINING\s+(?:CO|COMPANY)\b"),                   "HL", "equity"),
    (re.compile(r"\bWHEATON\s+PRECIOUS\s+METALS\b"),                       "WPM", "equity"),
    (re.compile(r"\bPAN\s+AMERICAN\s+SILVER\b"),                           "PAAS", "equity"),
    # ZWP is the UNHEDGED BMO Europe High Dividend Covered Call ETF; the
    # hedged share class prints "HEDGED TO CAD" in its name (BMO distributes
    # them as separate ETFs — ZWP $0.105 vs ZWE $0.120, Feb 2026 notice).
    # The asset type matches TD's holdings-typed instrument so one identity
    # carries both the holdings snapshots and the activity rows.
    (re.compile(r"\bBMO\s+EURO(?:PE)?\s+HI(?:GH)?\s+DIV\s+COV\s+ETF\b"),   "ZWP", "equity"),
    # Dividend rows print these equity names without their wrapped symbols
    # (holdings rows pair them: "SUNCOR ENERGY INC … NEW (SU)",
    # "CANADIAN NATIONAL … RAILWAY (CNR)").
    (re.compile(r"\bSUNCOR\s+ENERGY\b"),                                    "SU", "equity"),
    (re.compile(r"\bCANADIAN\s+NATIONAL\b"),                                "CNR", "equity"),
    (re.compile(r"\bNEWMONT\s+CORPORATION\b"),                              "NEM", "equity"),
    (re.compile(r"\bROYAL\s+GOLD\s+INC\b"),                                "RGLD", "equity"),
    (re.compile(r"\bOSISKO\s+GOLD\s+ROYALTIES\s+LTD\b"),                  "OR", "equity"),
    (re.compile(r"\bOR\s+ROYALTIES\s+INC\b"),                              "OR", "equity"),
    (re.compile(r"\bSANDSTORM\s+GOLD\s+LTD\b"),                            "SAND", "equity"),
    (re.compile(r"\bNORTHLAND\s+POWER\s+INC\b"),                            "NPI", "equity"),
    (re.compile(r"\bVALE\s+S\s+A\b"),                                      "VALE", "equity"),
    (re.compile(r"\bBARRICK\s+(?:MNG|MINING)\s+CORP\b"),                   "ABX", "equity"),
    (re.compile(r"\bFRANCO-?NEVADA\s+CORPORATION\b"),                      "FNV", "equity"),
    (re.compile(r"\bGIGA\s+METALS\s+CORP\b"),                              "GIGA", "equity"),
    (re.compile(r"\bMCEWEN\s+INC\b"),                                      "MUX", "equity"),
    (re.compile(r"\bFREEPORT\s+MCMORAN\s+INC\b"),                          "FCX", "equity"),
    (re.compile(r"\bRIO\s+TINTO\s+PLC\b"),                                 "RIO", "equity"),
    (re.compile(r"\bURANIUM\s+ROYALTY\s+CORP\b"),                          "UROY", "equity"),
    (re.compile(r"\bMETALLA\s+ROYALTY\s+&?\s*STREAMING\b"),                "MTA", "equity"),
    # Names below were added from CIBC Investor's Edge statements that print
    # the identity on a wrapped ``(SYM/EXCH)`` continuation line under the
    # holding row (2026-06/2026-07 formats).
    (re.compile(r"\bCLOUDFLARE\s+INC\b"),                                   "NET", "equity"),
    (re.compile(r"\bISHARES\s+SILVER\s+SHARES\b"),                          "SLV", "etf"),
    (re.compile(r"\bSPROTT\s+PHYSICAL\s+URANIUM\b"),                        "U.UN", "equity"),
    (re.compile(r"\bENCORE\s+ENERGY\s+CORP\b"),                             "EU", "equity"),
    (re.compile(r"\bVANECK\b.*\bURANIUM\s+AND\s+NUCLEAR\s+ETF\b"),         "NLR", "etf"),
    # Names below were added from TD WebBroker rows that print no symbol:
    # reverse-split book-swap legs (2026-05) and abbreviated transfer names.
    (re.compile(r"\bTHOMSON\s+REUTERS\b"),                                  "TRI", "equity"),
    (re.compile(r"\bGLB\s+X\s+US\s+DOLL\s+CURR\b"),                         "DLR.U", "etf"),
    # TD mutual-fund series print their name and TDB#### code together on
    # the holdings row (TD WebBroker 58MRB0, 2026-04..2026-06).
    (re.compile(r"\bTD\s+CDN\s+EQ-D\b"),                                    "TDB3089C", "mutual_fund"),
    (re.compile(r"\bTD\s+DIV\s+INCM-D\b"),                                  "TDB3087C", "mutual_fund"),
    # TD abbreviates these equity names on dividend rows; the holdings rows
    # pair each name with its wrapped symbol on the same statement
    # (TD WebBroker 58MRB0, 2026-06/2026-07).
    (re.compile(r"\bCANADIAN\s+PAC(?:IFIC)?(?:\s+KANSAS(?:\s+CITY)?)?\b"),  "CP", "equity"),
    (re.compile(r"\b(?:CDN|CANADIAN)\s+IMPERIAL\s+(?:BK|BANK)(?:\s+OF\s+COMMERCE)?\b"),
                                                                            "CM", "equity"),
    # RBC pooled-fund series print the pool name on exchange rows without
    # the fund code; the holdings rows pair each name with its RBF#### code
    # (TD WebBroker 58MRB0, 2026-02/2026-03).
    (re.compile(r"\bRBC\s+QUBE\s+CDN\b"),                                   "RBF678C", "mutual_fund"),
    (re.compile(r"\bRBC\s+OSH\s+CD\b"),                                     "RBF610C", "mutual_fund"),
]

# Leading words that aren't part of the security name (verbs, qualifiers).
_LEADING_NOISE = {
    "BOUGHT", "SOLD", "BUY", "SELL",
    "DIVIDEND", "DISTRIBUTION", "DISTRIB", "INTEREST",
    "REINVEST", "REINVESTED",
    "TRANSFER", "JOURNAL", "DEPOSIT", "WITHDRAWAL", "WITHDRAW",
    "TAX", "FEE", "ADJUSTMENT",
    "OPENING", "CLOSING",
    "MGR", "MERGER", "EXCHANGE",
}


# Strict broker fund codes (RBC ICF / TD series). A curated name entry that
# resolves to one of these carries the broker's own printed fund-code
# identity: the holdings rows pair the name and code on the same line.
PRINTED_FUND_CODE_RE = re.compile(r"(?:RBF|TDB)\d{3,4}[A-Z]?")


def strip_leading_verbs(desc: str) -> str:
    """Drop one or more leading 'verb' words from a free-form description."""
    toks = desc.strip().split()
    while toks and toks[0].upper().rstrip(".:") in _LEADING_NOISE:
        toks = toks[1:]
    return " ".join(toks)


def resolve_ticker(desc: str, currency: str | None = None) -> tuple[str, str] | None:
    """Return ``(ticker, asset_type)`` if the description matches a known name."""
    if not desc:
        return None
    u = re.sub(r"\s+", " ", desc.upper())
    best: tuple[int, int, str, str] | None = None
    for idx, (pat, tkr, atype) in enumerate(NAME_TO_TICKER):
        match = pat.search(u)
        if not match:
            continue
        candidate = (match.start(), idx, tkr, atype)
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    if best is not None:
        _, _idx, tkr, atype = best
        if tkr == "ABX" and currency == "USD":
            return "GOLD", atype
        if tkr == "CCJ" and currency == "CAD":
            return "CCO", atype
        if tkr == "NEM" and currency == "CAD":
            return "NGT", atype
        if tkr == "DLR.U" and currency == "CAD":
            return "DLR", atype
        # First Majestic renamed its TSX ticker FR -> AG (the 2025-2026 CIBC
        # holdings rows print "(AG/TSX)"), so a bare-name CAD row resolves
        # to AG — the same identity its holdings snapshots already use.
        if tkr == "UROY" and currency == "CAD":
            return "URC", atype
        if tkr == "SAND" and currency == "CAD":
            return "SSL", atype
        # Sprott Physical Uranium Trust lists in USD under a different symbol.
        if tkr == "U.UN" and currency == "USD":
            return "U.U.UN", atype
        # "EU" is the TSX Venture listing; a U.S.-dollar Encore printing would
        # be a different security, so leave it unresolved instead of guessing.
        if tkr == "EU" and currency != "CAD":
            return None
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
