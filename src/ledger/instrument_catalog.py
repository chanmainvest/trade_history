"""Deterministic broker-name to exchange-listing identities.

The catalog is deliberately small and reviewed. It does not replace Yahoo
search: it supplies stable listing metadata for statement variants already
observed in fixtures/corpus audits. Unknown text is queued for later review.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ListingIdentity:
    symbol: str
    currency: str
    exchange: str
    yahoo_symbol: str
    asset_type: str
    issuer_key: str
    issuer_name: str
    security_key: str
    security_name: str
    aliases: tuple[str, ...] = ()
    institution_code: str | None = None
    journalable: bool = False


def compact_identity(value: str | None) -> str:
    """Normalize broker spacing/punctuation without inventing word breaks."""
    return re.sub(r"[^A-Z0-9]+", "", (value or "").upper())


LISTINGS: tuple[ListingIdentity, ...] = (
    ListingIdentity("BCE", "CAD", "TSX", "BCE.TO", "equity", "issuer:bce", "BCE Inc.",
                    "security:bce:common", "BCE common shares", ("BCEINC",), journalable=True),
    ListingIdentity("BCE", "USD", "NYSE", "BCE", "equity", "issuer:bce", "BCE Inc.",
                    "security:bce:common", "BCE common shares", journalable=True),
    ListingIdentity("ZMMK", "CAD", "TSX", "ZMMK.TO", "etf", "issuer:bmo-am", "BMO Asset Management",
                    "security:zmmk:units", "BMO Money Market Fund ETF Series",
                    ("BMOMONEYMARKETFUNDETF", "BMOMONEYMARK", "BOMMONEYMARK")),
    ListingIdentity("UBIL.U", "USD", "TSX", "UBIL-U.TO", "etf", "issuer:global-x-ca", "Global X Investments Canada",
                    "security:ubil:usd-units", "Global X 0-3 Month U.S. T-Bill ETF USD units",
                    ("HORIZONS03M", "HORIZONS03MUSTBETFA", "HORIZONS0-3M", "HORIZONS0-3MUSTBETF-A")),
    ListingIdentity("DLR", "CAD", "TSX", "DLR.TO", "etf", "issuer:global-x-ca", "Global X Investments Canada",
                    "security:dlr:units", "Global X US Dollar Currency ETF",
                    ("HORIZONSUSDO", "HORIZONSUSDOLLCURRETF"),
                    journalable=True),
    ListingIdentity("DLR.U", "USD", "TSX", "DLR-U.TO", "etf", "issuer:global-x-ca", "Global X Investments Canada",
                    "security:dlr:units", "Global X US Dollar Currency ETF USD units",
                    ("HORIZONSUSDO", "HORIZONSUSDOLLCURRETF"),
                    journalable=True),
    ListingIdentity("CASH", "CAD", "TSX", "CASH.TO", "etf", "issuer:global-x-ca", "Global X Investments Canada",
                    "security:cash:units", "Global X High Interest Savings ETF",
                    ("HORZNHIGHINTSVGSETFA", "HORZNHIGINT", "HORIZONSHIGHINTERESTSAVINGSETF")),
    ListingIdentity("LQD", "USD", "NYSEARCA", "LQD", "etf", "issuer:blackrock", "BlackRock",
                    "security:lqd:units", "iShares iBoxx Investment Grade Corporate Bond ETF",
                    ("ISHARESIBOXX", "ISHARESIBOXXINVGRCRP",
                     "ISHARESIBOXXINVESTMENTGRADECORPORATEBONDETF")),
    ListingIdentity("NTR", "CAD", "TSX", "NTR.TO", "equity", "issuer:nutrien", "Nutrien Ltd.",
                    "security:ntr:common", "Nutrien common shares", ("NUTRIENLTD", "NURIENLTD"),
                    journalable=True),
    ListingIdentity("NTR", "USD", "NYSE", "NTR", "equity", "issuer:nutrien", "Nutrien Ltd.",
                    "security:ntr:common", "Nutrien common shares", journalable=True),
    ListingIdentity("RCI.B", "CAD", "TSX", "RCI-B.TO", "equity", "issuer:rogers", "Rogers Communications Inc.",
                    "security:rogers:class-b", "Rogers Class B non-voting shares",
                    ("ROGERSCOMMUNICATIONBNV", "RCI")),
    ListingIdentity("PSA", "CAD", "TSX", "PSA.TO", "etf", "issuer:purpose", "Purpose Investments",
                    "security:psa:units", "Purpose High Interest Savings Fund ETF",
                    ("PURPOSEHIINTSVGFDETF",)),
    ListingIdentity("TBIL", "USD", "NASDAQ", "TBIL", "etf", "issuer:us-benchmark", "US Benchmark Series",
                    "security:tbil:units", "US Treasury 3 Month Bill ETF", ("RBBUSTREAS3MOBILLETF",)),
    ListingIdentity("XBIL", "USD", "NASDAQ", "XBIL", "etf", "issuer:us-benchmark", "US Benchmark Series",
                    "security:xbil:units", "US Treasury 6 Month Bill ETF", ("RBBUSTREAS6MOBILLETF",)),
    ListingIdentity("OBIL", "USD", "NASDAQ", "OBIL", "etf", "issuer:us-benchmark", "US Benchmark Series",
                    "security:obil:units", "US Treasury 12 Month Bill ETF", ("RBBFDUSTR12MBILLETF",)),
    ListingIdentity("UTWO", "USD", "NASDAQ", "UTWO", "etf", "issuer:us-benchmark", "US Benchmark Series",
                    "security:utwo:units", "US Treasury 2 Year Note ETF", ("RBBFDUSTREAS2YNTETF",)),
    ListingIdentity("BILS", "USD", "NYSEARCA", "BILS", "etf", "issuer:ssga", "State Street Global Advisors",
                    "security:bils:units", "SPDR Bloomberg 3-12 Month T-Bill ETF", ("SPDRBLM312MTBILLETF",)),
    ListingIdentity("SII", "CAD", "TSX", "SII.TO", "equity", "issuer:sprott", "Sprott Inc.",
                    "security:sii:common", "Sprott common shares", ("SPROTTINCNEW",)),
    ListingIdentity("TRP", "CAD", "TSX", "TRP.TO", "equity", "issuer:tc-energy", "TC Energy Corporation",
                    "security:trp:common", "TC Energy common shares", ("TCENERGYCORP",), journalable=True),
    ListingIdentity("TRP", "USD", "NYSE", "TRP", "equity", "issuer:tc-energy", "TC Energy Corporation",
                    "security:trp:common", "TC Energy common shares", journalable=True),
    ListingIdentity("T", "CAD", "TSX", "T.TO", "equity", "issuer:telus", "TELUS Corporation",
                    "security:telus:common", "TELUS common shares", ("TELUSCORP",), journalable=True),
    ListingIdentity("TU", "USD", "NYSE", "TU", "equity", "issuer:telus", "TELUS Corporation",
                    "security:telus:common", "TELUS common shares", journalable=True),
    ListingIdentity("VCIT", "USD", "NASDAQ", "VCIT", "etf", "issuer:vanguard", "Vanguard",
                    "security:vcit:units", "Vanguard Intermediate-Term Corporate Bond ETF",
                    ("VANGUARDINTERTRMCRPBD",)),
    ListingIdentity("VCSH", "USD", "NASDAQ", "VCSH", "etf", "issuer:vanguard", "Vanguard",
                    "security:vcsh:units", "Vanguard Short-Term Corporate Bond ETF",
                    ("VANGUARDSTCORPBNDETF", "VANGUARDS/TCORPBNDETF")),
)


CATALOG_VERSION = "listing-catalog-v1:" + hashlib.sha256(
    json.dumps([asdict(item) for item in LISTINGS], sort_keys=True).encode("utf-8")
).hexdigest()[:16]


def listing_for_symbol(symbol: str, currency: str) -> ListingIdentity | None:
    normalized = symbol.upper().strip()
    matches = [item for item in LISTINGS if item.symbol == normalized and item.currency == currency]
    return matches[0] if len(matches) == 1 else None


def listing_for_text(
    value: str | None,
    currency: str,
    *,
    institution_code: str,
) -> ListingIdentity | None:
    normalized = compact_identity(value)
    if not normalized:
        return None
    matches = [
        item
        for item in LISTINGS
        if item.currency == currency
        and (item.institution_code is None or item.institution_code == institution_code)
        and normalized in {compact_identity(alias) for alias in item.aliases}
    ]
    return matches[0] if len(matches) == 1 else None
