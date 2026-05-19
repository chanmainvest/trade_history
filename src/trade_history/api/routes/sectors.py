"""GET /sectors — sector allocation percentages."""

from __future__ import annotations

import re
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends

from trade_history.api.deps import get_sqlite

router = APIRouter()

# Maps ticker symbols AND common company-name forms to GICS sectors.
# Keys are upper-cased; both short tickers (RBC statements) and full
# company names (CIBC/TD/HSBC statements) are included.
_SECTOR_MAP: dict[str, str] = {
    # ── Communication Services ──────────────────────────────────────────
    "BCE": "Communication Services",
    "BCE INC": "Communication Services",
    "T": "Communication Services",
    "TELUS": "Communication Services",
    "TELUS CORPORATION": "Communication Services",
    "TELUS CORP": "Communication Services",
    "RCI": "Communication Services",
    "ROGERS": "Communication Services",
    "ROGERS COMMUNICATIONS INC": "Communication Services",
    "ROGERS COMMUNICATIONS": "Communication Services",
    # ── Energy ──────────────────────────────────────────────────────────
    "ENB": "Energy",
    "ENBRIDGE": "Energy",
    "ENBRIDGE INC": "Energy",
    "ENBRIDGEINC": "Energy",
    "CNQ": "Energy",
    "CANADIAN NATURAL": "Energy",
    "CANADIAN NATURAL RESOURCES": "Energy",
    "SU": "Energy",
    "SUNCOR": "Energy",
    "SUNCOR ENERGY INC": "Energy",
    "SUNCOR ENERGY INC NEW": "Energy",
    "TRP": "Energy",
    "TC ENERGY": "Energy",
    "TC ENERGY CORP": "Energy",
    "CVE": "Energy",
    "CENOVUS": "Energy",
    "CENOVUS ENERGY INC": "Energy",
    "PPL": "Energy",
    "PEMBINA": "Energy",
    "PEMBINA PIPELINE CORP": "Energy",
    "CVX": "Energy",
    "CHEVRON": "Energy",
    "CHEVRON CORPORATION": "Energy",
    "XOM": "Energy",
    "EXXON": "Energy",
    "EXXON MOBIL CORP": "Energy",
    "WDS": "Energy",
    "WOODSIDE": "Energy",
    "WOODSIDE ENERGY GROUP LTD": "Energy",
    # ── Financials ──────────────────────────────────────────────────────
    "TD": "Financials",
    "TORONTO DOMINION BANK": "Financials",
    "TORONTO-DOMINION BANK": "Financials",
    "RY": "Financials",
    "RBC": "Financials",
    "ROYAL BANK CANADA": "Financials",
    "ROYAL BANK OF CANADA": "Financials",
    "BMO": "Financials",
    "BANK MONTREAL": "Financials",
    "BANK OF MONTREAL": "Financials",
    "BNS": "Financials",
    "BANK NOVA SCOTIA": "Financials",
    "BANK OF NOVA SCOTIA": "Financials",
    "CM": "Financials",
    "CIBC": "Financials",
    "IMPERIAL BK": "Financials",
    "CANADIAN IMPERIAL BANK": "Financials",
    "NA": "Financials",
    "NATIONAL BANK": "Financials",
    "NATIONAL BANK CDA": "Financials",
    "MFC": "Financials",
    "MANULIFE": "Financials",
    "SLF": "Financials",
    "SUN LIFE": "Financials",
    "BAM": "Financials",
    "BROOKFIELD": "Financials",
    "X": "Financials",
    "TMX": "Financials",
    "TMX GROUP LIMITED": "Financials",
    "TMX GROUP": "Financials",
    # ── Materials ───────────────────────────────────────────────────────
    "CCO": "Materials",
    "CAMECO": "Materials",
    "CAMECO CORP": "Materials",
    "CAMECOCORP": "Materials",
    "ABX": "Materials",
    "BARRICK": "Materials",
    "BARRICK GOLD CORP": "Materials",
    "BARRICK MNG CORP": "Materials",
    "WPM": "Materials",
    "WHEATON": "Materials",
    "WHEATON PRECIOUS METALS": "Materials",
    "FNV": "Materials",
    "FRANCO-NEVADA": "Materials",
    "FRANCO-NEVADA CORPORATION": "Materials",
    "SSL": "Materials",
    "SANDSTORM": "Materials",
    "SANDSTORM GOLD LTD": "Materials",
    "SANDSTORM GOLD LTD COM": "Materials",
    "OR": "Materials",
    "OSISKO": "Materials",
    "OSISKO GOLD ROYALTIES LTD": "Materials",
    "AG": "Materials",
    "FIRST MAJESTIC": "Materials",
    "FIRST MAJESTIC SILVER CORP": "Materials",
    "FIRSTMAJESTICSILVERCORP": "Materials",
    "PAAS": "Materials",
    "PAN AMERICAN": "Materials",
    "PAN AMERICAN SILVER CORP": "Materials",
    "PANAMERICANSILVERCORP": "Materials",
    "MUX": "Materials",
    "MCEWEN": "Materials",
    "MCEWEN MINING INC": "Materials",
    "NEM": "Materials",
    "NEWMONT": "Materials",
    "NEWMONTCORPORATION": "Materials",
    "NEWMONT CORPORATION": "Materials",
    "BTO": "Materials",
    "B2GOLD": "Materials",
    "B2GOLD CORP": "Materials",
    "K": "Materials",
    "KINROSS": "Materials",
    "METALLA": "Materials",
    "METALLA ROYALTY & STREAMING": "Materials",
    "MTA": "Materials",
    "NOVA RTY CORP": "Materials",
    "NVA": "Materials",
    "EMPRESS ROYALTY CORP": "Materials",
    "URANIUM ROYALTY CORP": "Materials",
    "GIGA METALS CORP": "Materials",
    "NTR": "Materials",
    "NUTRIEN": "Materials",
    "NUTRIEN LTD": "Materials",
    "NUTRIENLTD": "Materials",
    "BHP": "Materials",
    "BHP GROUP LIMITED": "Materials",
    "RIO": "Materials",
    "RIO TINTO PLC": "Materials",
    "VALE": "Materials",
    "VALE S A": "Materials",
    "FCX": "Materials",
    "FREEPORT": "Materials",
    "FREEPORT MCMORAN INC": "Materials",
    "TECK": "Materials",
    "TECK RESOURCES LIMITED": "Materials",
    "AA": "Materials",
    "ALCOA": "Materials",
    "ALCOA CORP": "Materials",
    "HOWMET": "Materials",       # aluminium / aerospace fasteners
    "HOWMET AEROSPACE INC": "Materials",
    "ARCONIC": "Materials",
    "ARCONIC CORP": "Materials",
    "SPROTT PHYSICAL URANIUM": "Materials",
    "SPROTT PHYSICAL PLATINUM": "Materials",
    "SPROTT TRUST": "Materials",
    "SPROTT INC": "Materials",
    "SII": "Materials",
    # ── Technology ──────────────────────────────────────────────────────
    "SHOP": "Technology",
    "SHOPIFY": "Technology",
    "SHOPIFY INC": "Technology",
    "SHOPIFYINC": "Technology",
    "CSU": "Technology",
    "CONSTELLATION": "Technology",
    "OTEX": "Technology",
    "OPEN TEXT": "Technology",
    "AMD": "Technology",
    "ADVANCED MICRO DEVICES": "Technology",
    "MSFT": "Technology",
    "MICROSOFT": "Technology",
    "MICROSOFT CORP": "Technology",
    "NVDA": "Technology",
    "NVIDIA": "Technology",
    "NVIDIA CORP": "Technology",
    "GOOGL": "Technology",
    "GOOG": "Technology",
    "ALPHABET": "Technology",
    "ALPHABET INC CL-C": "Technology",
    "ALPHABET INC CL-A": "Technology",
    "ADBE": "Technology",
    "ADOBE": "Technology",
    "ADOBE INC": "Technology",
    "INTC": "Technology",
    "INTEL": "Technology",
    "INTEL CORPORATION": "Technology",
    "CSCO": "Technology",
    "CISCO": "Technology",
    "CISCO SYSTEMS INC": "Technology",
    "BB": "Technology",
    "BLACKBERRY": "Technology",
    "BLACKBERRY LTD": "Technology",
    "CHKP": "Technology",
    "CHECK POINT": "Technology",
    "CHECK POINT SOFTWARE": "Technology",
    "IBM": "Technology",
    "INTL BUSINESS MACHINES": "Technology",
    "QBTS": "Technology",
    "D-WAVE QUANTUM INC": "Technology",
    "QUBT": "Technology",
    "QUANTUM COMPUTING INC": "Technology",
    "CLS": "Technology",
    "CELESTICA": "Technology",
    "CELESTICA INC SV": "Technology",
    "ARM": "Technology",
    "ARM HOLDINGS PLC": "Technology",
    "SMCI": "Technology",
    "SUPER MICRO": "Technology",
    "SUPER MICRO COMPUTER": "Technology",
    "SUPER MICRO COMPUTER INC": "Technology",
    "KD": "Technology",
    "KYNDRYL": "Technology",
    "KYNDRYL HOLDINGS INC": "Technology",
    "AAPL": "Technology",
    "APPLE": "Technology",
    "APPLE INC": "Technology",
    # ── Industrials ─────────────────────────────────────────────────────
    "CNR": "Industrials",
    "CANADIAN NATIONAL": "Industrials",   # also matched under Energy above; CN wins
    "CP": "Industrials",
    "CANADIAN PACIFIC RAIL": "Industrials",
    "CANADIAN PACIFIC": "Industrials",
    "WCN": "Industrials",
    "WASTE CONNECTIONS": "Industrials",
    "CAT": "Industrials",
    "CATERPILLAR": "Industrials",
    "CATERPILLAR INC": "Industrials",
    "SMURFIT": "Industrials",
    "SMURFIT WESTROCK PLC": "Industrials",
    # ── Consumer Discretionary ──────────────────────────────────────────
    "ATD": "Consumer Discretionary",
    "TSLA": "Consumer Discretionary",
    "TESLA": "Consumer Discretionary",
    "TESLA INC": "Consumer Discretionary",
    "ABNB": "Consumer Discretionary",
    "AIRBNB": "Consumer Discretionary",
    "AIRBNB INC CL-A": "Consumer Discretionary",
    # ── Consumer Staples ────────────────────────────────────────────────
    "MRU": "Consumer Staples",
    "METRO": "Consumer Staples",
    "L": "Consumer Staples",
    "LOBLAW": "Consumer Staples",
    # ── Health Care ─────────────────────────────────────────────────────
    "PFE": "Health Care",
    "PFIZER": "Health Care",
    "PFIZER INC": "Health Care",
    "UNH": "Health Care",
    "UNITED HEALTH": "Health Care",
    "UNITED HEALTH GROUP INC": "Health Care",
    "UNITEDHEALTH": "Health Care",
    "VIATRIS": "Health Care",
    "VIATRIS INC": "Health Care",
    # ── Utilities ───────────────────────────────────────────────────────
    "FTS": "Utilities",
    "FORTIS": "Utilities",
    "FORTIS INC": "Utilities",
    "NEE": "Utilities",
    "NEXTERA": "Utilities",
    "NEXTERA ENERGY INC": "Utilities",
    "H": "Utilities",
    "HYDRO ONE": "Utilities",
    "HYDRO ONE LIMITED": "Utilities",
    "NPI": "Utilities",
    "NORTHLAND": "Utilities",
    "NORTHLAND POWER INC": "Utilities",
    # ── Real Estate ─────────────────────────────────────────────────────
    "REI": "Real Estate",
    "RIOCAN": "Real Estate",
    "RIOCAN REAL ESTATE": "Real Estate",
    "GRT": "Real Estate",
    "GRANITE": "Real Estate",
    "GRANITE REAL ESTATE": "Real Estate",
    "CAR": "Real Estate",
    "CANADIAN APARTMENT": "Real Estate",
    "CANADIAN APARTMENT PPTYS": "Real Estate",
    "BEI": "Real Estate",
    "BOARDWALK": "Real Estate",
    "BOARDWALK REAL ESTATE INVT": "Real Estate",
    "LAND": "Real Estate",
    "GLADSTONE LAND": "Real Estate",
    "GLADSTONE LAND CORPORATION": "Real Estate",
    "FPI": "Real Estate",
    "FARMLAND PARTNERS INC": "Real Estate",
    "WY": "Real Estate",
    "WEYERHAEUSER": "Real Estate",
    "WEYERHAEUSER CO": "Real Estate",
    "TPL": "Real Estate",
    "TEXAS PACIFIC LAND": "Real Estate",
    "TEXAS PACIFIC LAND CORPORATION": "Real Estate",
}

# Keywords that identify ETF / fund instruments (excluded from sector allocation)
_FUND_KEYWORDS = frozenset(
    ["ETF", "FUND", " FD ", "FD INC", "MONEY MARKET", "MONEYMARKET",
     "T-BILL", "TBILL", "TREASURY", "BOND", "MONEY MKT", "SAVINGS",
     "SVGS", "HIGH INT", "CORP BD", "INDEX", "NL'FRAC", "SER/NL",
     "FRAC", "CURRENCY",
     # ETF provider prefixes (concatenated or spaced)
     "HORIZONS", "ISHARES", "ISHARESIBOXX", "VANGUARD",
     "INVESCO", "GLOBAL X", "SIMPLIFY EXCHANGE", "MACKENZIE",
     "BMO MID", "BMO SHORT", "BMO HIGH", "BMO EQUAL", "BMO EURO",
     "BMO MONEY", "RBCPREMIUM", "RBB FD", "RBBFUNDING", "RBBFDUSTR",
     "HORIZONS0", "HORZN", "PURPOSEETHER", "CIGALAXY",
     "GLB X", "IBOXX"]
)


def _sector_for(symbol: str) -> str:
    """Return the GICS sector for *symbol*, or 'Other'."""
    if not symbol:
        return "Other"
    # Normalise: strip exchange suffix (.TO, .AX, …), upper-case
    s = re.sub(r"\.[A-Z]{1,3}$", "", symbol.strip()).upper()

    # Skip ETF / fund wrappers
    if any(kw in s for kw in _FUND_KEYWORDS):
        return "ETF/Fund"

    # 1. Exact match (handles both tickers and full company names)
    if s in _SECTOR_MAP:
        return _SECTOR_MAP[s]

    # 2. First word (handles "CAMECO CORP" → "CAMECO", "ENBRIDGE INC" → "ENBRIDGE")
    first = s.split()[0] if s.split() else s
    if first in _SECTOR_MAP:
        return _SECTOR_MAP[first]

    # 3. First two words (handles "CANADIAN NATURAL …" → "CANADIAN NATURAL")
    words = s.split()
    if len(words) >= 2:
        two = " ".join(words[:2])
        if two in _SECTOR_MAP:
            return _SECTOR_MAP[two]

    return "Other"


def _is_junk_symbol(symbol: str) -> bool:
    """Return True for symbols that are clearly parser artifacts, not real instruments."""
    if not symbol:
        return True
    # TD transfer descriptions, RBC opening balance text, wire transfers
    _JUNK_KEYWORDS = (
        "Web Banking", "Opening Balance", "WIRE TFR", "TSF FR",
        "Assign ", "Disposition ", "COVER SHORT",
    )
    for kw in _JUNK_KEYWORDS:
        if kw in symbol:
            return True
    # Numbered list items from statement parsing (e.g., "- 3,000 BOMBARDIER INC")
    if symbol.startswith("- "):
        return True
    # Date-prefixed descriptions (e.g., "JUNE 14 FIRST MAJESTIC SILVER CORP CASH")
    if re.match(r"^[A-Z]+ \d+ ", symbol):
        return True
    return False


@router.get("")
def sector_allocation(
    conn: Annotated[sqlite3.Connection, Depends(get_sqlite)],
):
    """Return sector allocation as percentage of total market value."""
    from trade_history.analytics.positions import get_open_positions

    positions = get_open_positions(conn)

    sector_values: dict[str, float] = {}
    total = 0.0

    for pos in positions:
        if pos.asset_type not in ("equity", "etf"):
            continue
        sym = pos.symbol or ""
        if _is_junk_symbol(sym):
            continue
        sector = _sector_for(sym)
        mv = pos.market_value or float(pos.quantity * pos.avg_cost)
        # Sanity check: skip positions without market data that have
        # an implausibly large cost basis (parser artifacts)
        if pos.market_value is None and mv > 1_000_000:
            continue
        sector_values[sector] = sector_values.get(sector, 0.0) + mv
        total += mv

    if total == 0:
        return []

    return [
        {
            "sector": sector,
            "market_value": round(mv, 2),
            "percentage": round(mv / total * 100, 2),
        }
        for sector, mv in sorted(sector_values.items(), key=lambda x: -x[1])
    ]
