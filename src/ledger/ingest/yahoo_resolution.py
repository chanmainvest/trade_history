"""Opt-in Yahoo verification for public listing identities.

No account/source data is sent. Search receives only the broker security name,
currency, and candidate symbol. A financial row changes only after a later
deterministic re-ingest consumes the resolved candidate.
"""
from __future__ import annotations

import difflib
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from ..db import sqlite as sqlite_db
from ..domains import utc_now_text
from ..logging_setup import jsonl_path

SearchFunction = Callable[[str], list[dict[str, Any]]]
HistoryFunction = Callable[[str], bool]
QuoteInfoFunction = Callable[[str], dict[str, Any] | None]
_QUOTE_TYPES = {"EQUITY", "ETF", "MUTUALFUND"}
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
_CANADIAN_SUFFIXES = (".TO", ".V", ".NE")
_SYMBOL_FIRST_MIN_SCORE = 0.70


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


def _default_quote_info(symbol: str) -> dict[str, Any] | None:
    import yfinance as yf

    yf.set_tz_cache_location(str(DATA_DIR / "yfinance_cache"))
    info = yf.Ticker(symbol).info
    if not info:
        return None
    return {
        "symbol": info.get("symbol") or symbol,
        "shortname": info.get("shortName"),
        "longname": info.get("longName"),
        "currency": info.get("currency"),
        "quoteType": info.get("quoteType"),
    }


def _symbol_first_candidates(symbol: str, currency: str) -> list[str]:
    """Deterministic Yahoo candidates built from the broker-printed symbol."""
    value = symbol.upper()
    if not _SYMBOL.fullmatch(value):
        return []
    base = value.replace(".", "-")
    if currency == "CAD":
        return [base + suffix for suffix in _CANADIAN_SUFFIXES]
    if currency == "USD":
        return [base]
    return []


def _acceptable_quote(quote: dict[str, Any], currency: str) -> bool:
    """Deterministic listing-family filter shared by search and LLM paths."""
    symbol = str(quote.get("symbol") or "").upper()
    quote_type = str(quote.get("quoteType") or "").upper()
    if quote_type not in _QUOTE_TYPES or not _SYMBOL.fullmatch(symbol):
        return False
    canadian_suffix = symbol.endswith((".TO", ".V", ".CN", ".NE"))
    usd_canadian_line = symbol.endswith("-U.TO")
    if currency == "CAD" and (not canadian_suffix or usd_canadian_line):
        return False
    if currency == "USD" and canadian_suffix and not usd_canadian_line:
        return False
    return True


def _unique_quote(
    query: str,
    quotes: list[dict[str, Any]],
    *,
    currency: str,
    minimum_score: float = 0.82,
) -> dict[str, Any] | None:
    scored = []
    for quote in quotes:
        if not _acceptable_quote(quote, currency):
            continue
        score = _name_score(query, quote)
        if score >= minimum_score:
            scored.append((score, str(quote.get("symbol")).upper(), quote))
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
    llm: Any = None,
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
        currency = str(row["currency"])
        try:
            quotes = search(query)
        except Exception:
            metrics["candidate_search_failed"] += 1
            continue
        quote = _unique_quote(query, quotes, currency=currency)
        resolved_method = "yahoo_unique_name"
        if quote is None and quotes and llm is not None:
            grounded = [item for item in quotes if _acceptable_quote(item, currency)]
            try:
                llm_quote, reason = llm.choose(
                    name=query, symbol=None, currency=currency, quotes=grounded
                )
            except Exception:
                llm_quote, reason = None, "llm chooser raised"
            if llm_quote is not None and not any(llm_quote == item for item in grounded):
                llm_quote, reason = None, "llm choice not grounded in candidate list"
            if llm_quote is not None:
                quote = llm_quote
                resolved_method = "llm_assisted"
                metrics["candidates_llm_proposed"] += 1
            elif "failed" in reason or "raised" in reason:
                metrics["candidates_llm_errors"] += 1
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
        confidence = 0.9 if resolved_method == "yahoo_unique_name" else 0.85
        instrument_id = sqlite_db.upsert_instrument(
            conn,
            asset_type=asset_type,
            symbol=symbol,
            currency=currency,
            exchange=exchange,
            name=name,
            resolution_method=resolved_method,
            resolution_confidence=confidence,
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
                   resolution_method = ?,
                   resolution_confidence = ?,
                   last_seen_at = strftime('%Y-%m-%dT%H:%M:%SZ','now')
             WHERE candidate_id = ?
            """,
            (instrument_id, resolved_method, confidence, row["candidate_id"]),
        )
        if resolved_method == "llm_assisted":
            metrics["candidates_resolved_llm"] += 1
        metrics["candidates_resolved"] += 1


def _unmapped_traded_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Traded/held equity+ETF instruments that still lack a Yahoo mapping."""
    return conn.execute(
        """
        SELECT instrument_id, symbol, name, currency, exchange, asset_type,
               option_root, option_expiry, option_strike
          FROM instruments
         WHERE asset_type IN ('equity','etf')
           AND currency IN ('CAD','USD')
           AND NOT EXISTS (
                SELECT 1 FROM instrument_market_symbols market
                 WHERE market.instrument_id = instruments.instrument_id
                   AND market.provider = 'yahoo'
           )
           AND (
                EXISTS (SELECT 1 FROM transactions t WHERE t.instrument_id = instruments.instrument_id)
             OR EXISTS (SELECT 1 FROM position_snapshots p WHERE p.instrument_id = instruments.instrument_id)
             OR EXISTS (SELECT 1 FROM initial_positions initial WHERE initial.instrument_id = instruments.instrument_id)
           )
         ORDER BY symbol, instrument_id
        """
    ).fetchall()


_LLM_NAME_FLOOR = 0.50
_CONTRACT_NAME_RE = re.compile(r"\b(?:CALL|PUT)\b|\d{2,3}\.\d{2}\s+\d{2,3}\.\d{2}")


def _resolve_unmapped_instruments(
    conn: sqlite3.Connection,
    quote_info: QuoteInfoFunction,
    history: HistoryFunction,
    metrics: Counter[str],
) -> None:
    """Verify unmapped traded instruments against their own printed symbol.

    Broker-truncated names routinely fail the name-search pass, so try the
    deterministic provider candidates first and accept one only when Yahoo's
    quote name, currency, and live price history corroborate the ledger row.
    """
    rows = _unmapped_traded_rows(conn)
    taken = {
        str(r["provider_symbol"])
        for r in conn.execute(
            "SELECT provider_symbol FROM instrument_market_symbols WHERE provider = 'yahoo'"
        ).fetchall()
    }
    for row in rows:
        symbol = str(row["symbol"])
        currency = str(row["currency"])
        candidates = _symbol_first_candidates(symbol, currency)
        if not candidates:
            metrics["symbol_first_skipped_symbol"] += 1
            continue
        accepted: dict[str, Any] | None = None
        tried: list[str] = []
        for candidate in candidates:
            if candidate in taken:
                metrics["symbol_first_conflict"] += 1
                continue
            tried.append(candidate)
            try:
                quote = quote_info(candidate)
            except Exception:
                quote = None
            if not quote:
                continue
            quote_symbol = str(quote.get("symbol") or candidate).upper()
            quote_type = str(quote.get("quoteType") or "").upper()
            if quote_symbol != candidate or quote_type not in _QUOTE_TYPES:
                continue
            if str(quote.get("currency") or "").upper() != currency:
                continue
            if _name_score(str(row["name"] or symbol), quote) < _SYMBOL_FIRST_MIN_SCORE:
                metrics["symbol_first_name_mismatch"] += 1
                continue
            try:
                available = history(candidate)
            except Exception:
                available = False
            if not available:
                metrics["symbol_first_no_history"] += 1
                continue
            accepted = quote
            provider_symbol = candidate
            break
        if accepted is None:
            if tried:
                metrics["symbol_first_unverified"] += 1
            continue
        now = utc_now_text()
        market_symbol_id = sqlite_db.upsert_market_symbol(
            conn,
            instrument_id=int(row["instrument_id"]),
            provider_symbol=provider_symbol,
            status="verified",
        )
        taken.add(provider_symbol)
        conn.execute(
            """
            UPDATE instrument_market_symbols
               SET status = 'verified', last_checked_at = ?,
                   verified_at = ?, last_error = NULL
             WHERE market_symbol_id = ?
            """,
            (now, now, market_symbol_id),
        )
        if row["exchange"] is None and provider_symbol.endswith(".TO"):
            conn.execute(
                "UPDATE instruments SET exchange = 'TSX' WHERE instrument_id = ?",
                (row["instrument_id"],),
            )
        metrics["symbol_first_resolved"] += 1


def _resolve_unmapped_with_llm(
    conn: sqlite3.Connection,
    llm: Any,
    search: SearchFunction,
    history: HistoryFunction,
    metrics: Counter[str],
) -> None:
    """LLM fallback for traded instruments the deterministic passes left unmapped.

    Grounded candidates come from Yahoo search on the ledger name and the
    printed symbol; the model picks one, and acceptance still requires the
    deterministic currency/type checks, a 0.50 name floor, live price history,
    and an unclaimed provider symbol. Contract-like rows (options, bond
    descriptions) never reach the model.
    """
    rows = _unmapped_traded_rows(conn)
    if not rows:
        return
    taken = {
        str(r["provider_symbol"])
        for r in conn.execute(
            "SELECT provider_symbol FROM instrument_market_symbols WHERE provider = 'yahoo'"
        ).fetchall()
    }
    jsonl = jsonl_path("llm_resolution").open("a", encoding="utf-8")
    try:
        for row in rows:
            if row["option_root"] or row["option_expiry"] or row["option_strike"]:
                metrics["symbol_llm_skipped_contract"] += 1
                continue
            name = str(row["name"] or row["symbol"])
            if _CONTRACT_NAME_RE.search(name):
                metrics["symbol_llm_skipped_contract"] += 1
                continue
            symbol = str(row["symbol"])
            currency = str(row["currency"])
            quotes: dict[str, dict[str, Any]] = {}
            for query in dict.fromkeys([name, symbol]):
                try:
                    results = search(query)
                except Exception:
                    metrics["symbol_llm_search_failed"] += 1
                    continue
                for quote in results:
                    if _acceptable_quote(quote, currency):
                        quotes[str(quote.get("symbol") or "").upper()] = quote
            if not quotes:
                metrics["symbol_llm_no_candidates"] += 1
                continue
            try:
                chosen, reason = llm.choose(
                    name=name, symbol=symbol, currency=currency, quotes=list(quotes.values())
                )
            except Exception as exc:
                metrics["symbol_llm_errors"] += 1
                jsonl.write(json.dumps({
                    "key": symbol, "currency": currency, "status": "error",
                    "err": str(exc)[:200],
                }) + "\n")
                continue
            if chosen is not None and str(chosen.get("symbol") or "").upper() not in quotes:
                chosen, reason = None, "llm choice not grounded in candidate list"
            if chosen is None:
                metrics["symbol_llm_declined"] += 1
                jsonl.write(json.dumps({
                    "key": symbol, "currency": currency, "status": "declined",
                    "reason": reason[:200],
                }) + "\n")
                continue
            provider_symbol = str(chosen["symbol"]).upper()
            score = _name_score(name, chosen)
            entry = {
                "key": symbol,
                "currency": currency,
                "provider_symbol": provider_symbol,
                "name_score": round(score, 3),
                "chosen_name": chosen.get("longname") or chosen.get("shortname"),
                "reason": reason[:200],
            }
            if provider_symbol in taken:
                entry["status"] = "conflict"
                metrics["symbol_llm_conflict"] += 1
                jsonl.write(json.dumps(entry) + "\n")
                continue
            if score < _LLM_NAME_FLOOR:
                entry["status"] = "name_floor"
                metrics["symbol_llm_name_floor"] += 1
                jsonl.write(json.dumps(entry) + "\n")
                continue
            try:
                available = history(provider_symbol)
            except Exception:
                available = False
            if not available:
                entry["status"] = "no_history"
                metrics["symbol_llm_no_history"] += 1
                jsonl.write(json.dumps(entry) + "\n")
                continue
            now = utc_now_text()
            market_symbol_id = sqlite_db.upsert_market_symbol(
                conn,
                instrument_id=int(row["instrument_id"]),
                provider_symbol=provider_symbol,
                status="verified",
            )
            taken.add(provider_symbol)
            conn.execute(
                """
                UPDATE instrument_market_symbols
                   SET status = 'verified', last_checked_at = ?,
                       verified_at = ?, last_error = NULL
                 WHERE market_symbol_id = ?
                """,
                (now, now, market_symbol_id),
            )
            conn.execute(
                """
                UPDATE instruments
                   SET resolution_method = 'llm_assisted_yahoo',
                       resolution_confidence = 0.85
                 WHERE instrument_id = ?
                   AND (resolution_method IS NULL
                        OR resolution_method = 'unresolved_printed_identity')
                """,
                (row["instrument_id"],),
            )
            if row["exchange"] is None and provider_symbol.endswith(".TO"):
                conn.execute(
                    "UPDATE instruments SET exchange = 'TSX' WHERE instrument_id = ?",
                    (row["instrument_id"],),
                )
            entry["status"] = "ok"
            metrics["symbol_llm_resolved"] += 1
            jsonl.write(json.dumps(entry) + "\n")
    finally:
        jsonl.close()


def verify_yahoo_identities(
    path: Path | str | None = None,
    *,
    search: SearchFunction | None = None,
    history: HistoryFunction | None = None,
    quote_info: QuoteInfoFunction | None = None,
    llm: Any = None,
) -> dict[str, int]:
    """Verify mappings, resolve pending public-name candidates, and verify
    unmapped traded instruments against their own printed symbol.

    ``llm`` (any object with ``choose(name, symbol, currency, quotes)``) adds
    an LLM fallback for ambiguous candidates and still-unmapped instruments;
    every LLM choice remains grounded in Yahoo results and passes the same
    deterministic checks.
    """
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
            llm=llm,
        )
        _resolve_unmapped_instruments(
            conn,
            quote_info or _default_quote_info,
            history or _default_history,
            metrics,
        )
        if llm is not None:
            _resolve_unmapped_with_llm(
                conn,
                llm,
                search or _default_search,
                history or _default_history,
                metrics,
            )
    return dict(sorted(metrics.items()))
