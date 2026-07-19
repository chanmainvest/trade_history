"""Opt-in Yahoo verification for public listing identities.

No account/source data is sent. Search receives only the broker security name,
currency, and candidate symbol. A financial row changes only after a later
deterministic re-ingest consumes the resolved candidate.
"""
from __future__ import annotations

import difflib
import re
import sqlite3
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from ..db import sqlite as sqlite_db
from ..domains import utc_now_text

SearchFunction = Callable[[str], list[dict[str, Any]]]
HistoryFunction = Callable[[str], bool]
_QUOTE_TYPES = {"EQUITY", "ETF", "MUTUALFUND"}
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")


def _normalized_name(value: str | None) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (value or "").upper())


def _name_score(query: str, quote: dict[str, Any]) -> float:
    target = _normalized_name(query)
    names = {
        _normalized_name(quote.get("shortname")),
        _normalized_name(quote.get("longname")),
    } - {""}
    if not target or not names:
        return 0.0
    return max(difflib.SequenceMatcher(None, target, name).ratio() for name in names)


def _default_search(query: str) -> list[dict[str, Any]]:
    import yfinance as yf

    yf.set_tz_cache_location(str(DATA_DIR / "yfinance_cache"))
    return list(yf.Search(query, max_results=10).quotes)


def _default_history(symbol: str) -> bool:
    import yfinance as yf

    yf.set_tz_cache_location(str(DATA_DIR / "yfinance_cache"))
    history = yf.Ticker(symbol).history(period="1mo", interval="1d", auto_adjust=False)
    return history is not None and not history.empty


def _unique_quote(
    query: str,
    quotes: list[dict[str, Any]],
    *,
    currency: str,
    minimum_score: float = 0.82,
) -> dict[str, Any] | None:
    scored = []
    for quote in quotes:
        symbol = str(quote.get("symbol") or "").upper()
        quote_type = str(quote.get("quoteType") or "").upper()
        if quote_type not in _QUOTE_TYPES or not _SYMBOL.fullmatch(symbol):
            continue
        canadian_suffix = symbol.endswith((".TO", ".V", ".CN", ".NE"))
        usd_canadian_line = symbol.endswith("-U.TO")
        if currency == "CAD" and (not canadian_suffix or usd_canadian_line):
            continue
        if currency == "USD" and canadian_suffix and not usd_canadian_line:
            continue
        score = _name_score(query, quote)
        if score >= minimum_score:
            scored.append((score, symbol, quote))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if not scored:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.08:
        return None
    return scored[0][2]


def _ledger_symbol(provider_symbol: str) -> tuple[str, str | None]:
    value = provider_symbol.upper()
    suffixes = {
        ".TO": "TSX",
        ".V": "TSXV",
        ".CN": "CSE",
        ".NE": "NEO",
    }
    for suffix, exchange in suffixes.items():
        if value.endswith(suffix):
            return value.removesuffix(suffix).replace("-", "."), exchange
    return value.replace("-", "."), None


def _verify_existing_mappings(
    conn: sqlite3.Connection,
    history: HistoryFunction,
    metrics: Counter[str],
) -> None:
    rows = conn.execute(
        """
        SELECT market.market_symbol_id, market.provider_symbol
          FROM instrument_market_symbols market
         WHERE market.provider = 'yahoo'
           AND market.status IN ('candidate','failed')
         ORDER BY market.market_symbol_id
        """
    ).fetchall()
    for row in rows:
        now = utc_now_text()
        try:
            available = history(str(row["provider_symbol"]))
        except Exception as exc:
            conn.execute(
                """
                UPDATE instrument_market_symbols
                   SET status = 'failed', last_checked_at = ?, last_error = ?
                 WHERE market_symbol_id = ?
                """,
                (now, str(exc)[:500], row["market_symbol_id"]),
            )
            metrics["market_failed"] += 1
            continue
        if available:
            conn.execute(
                """
                UPDATE instrument_market_symbols
                   SET status = 'verified', last_checked_at = ?,
                       verified_at = ?, last_error = NULL
                 WHERE market_symbol_id = ?
                """,
                (now, now, row["market_symbol_id"]),
            )
            metrics["market_verified"] += 1
        else:
            conn.execute(
                """
                UPDATE instrument_market_symbols
                   SET status = 'failed', last_checked_at = ?,
                       last_error = 'Yahoo returned no price history'
                 WHERE market_symbol_id = ?
                """,
                (now, row["market_symbol_id"]),
            )
            metrics["market_failed"] += 1


def _resolve_pending_candidates(
    conn: sqlite3.Connection,
    search: SearchFunction,
    history: HistoryFunction,
    metrics: Counter[str],
) -> None:
    rows = conn.execute(
        """
        SELECT candidate_id, display_text, asset_type, currency
          FROM instrument_resolution_candidates
         WHERE status = 'pending'
         ORDER BY candidate_id
        """
    ).fetchall()
    for row in rows:
        query = str(row["display_text"])
        try:
            quotes = search(query)
        except Exception:
            metrics["candidate_search_failed"] += 1
            continue
        quote = _unique_quote(query, quotes, currency=str(row["currency"]))
        if quote is None:
            if quotes:
                conn.execute(
                    "UPDATE instrument_resolution_candidates SET status = 'ambiguous' "
                    "WHERE candidate_id = ?",
                    (row["candidate_id"],),
                )
                metrics["candidate_ambiguous"] += 1
            else:
                conn.execute(
                    "UPDATE instrument_resolution_candidates SET status = 'not_found' "
                    "WHERE candidate_id = ?",
                    (row["candidate_id"],),
                )
                metrics["candidate_not_found"] += 1
            continue
        provider_symbol = str(quote["symbol"]).upper()
        try:
            available = history(provider_symbol)
        except Exception:
            available = False
        if not available:
            metrics["candidate_no_prices"] += 1
            continue
        symbol, exchange = _ledger_symbol(provider_symbol)
        quote_type = str(quote.get("quoteType") or "").upper()
        asset_type = "etf" if quote_type == "ETF" else str(row["asset_type"])
        name = quote.get("longname") or quote.get("shortname") or query
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type=asset_type,
            symbol=symbol,
            currency=str(row["currency"]),
            exchange=exchange,
            name=str(name),
            resolution_method="yahoo_unique_name",
            resolution_confidence=0.9,
            market_symbol=provider_symbol,
        )
        now = utc_now_text()
        conn.execute(
            """
            UPDATE instrument_market_symbols
               SET status = 'verified', last_checked_at = ?,
                   verified_at = ?, last_error = NULL
             WHERE instrument_id = ? AND provider = 'yahoo'
            """,
            (now, now, instrument_id),
        )
        conn.execute(
            """
            UPDATE instrument_resolution_candidates
               SET status = 'resolved', resolved_instrument_id = ?,
                   resolution_method = 'yahoo_unique_name',
                   resolution_confidence = 0.9,
                   last_seen_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
             WHERE candidate_id = ?
            """,
            (instrument_id, row["candidate_id"]),
        )
        metrics["candidates_resolved"] += 1


def verify_yahoo_identities(
    path: Path | str | None = None,
    *,
    search: SearchFunction | None = None,
    history: HistoryFunction | None = None,
) -> dict[str, int]:
    """Verify mappings and uniquely resolve pending public-name candidates."""
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    sqlite_db.init_db(db_path)
    metrics: Counter[str] = Counter()
    with sqlite_db.session(db_path) as conn:
        _verify_existing_mappings(conn, history or _default_history, metrics)
        _resolve_pending_candidates(
            conn,
            search or _default_search,
            history or _default_history,
            metrics,
        )
    return dict(sorted(metrics.items()))
