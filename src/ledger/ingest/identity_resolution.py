"""Conservative, deterministic identity resolution for staged ingestion.

Parsers preserve what the statement printed. This module may replace an
ambiguous identity only with a reviewed alias, deterministic listing-catalog
entry, previously resolved candidate, or exact known holding identity.
Uncertain text remains auditable and is queued instead of becoming a ticker.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter

from ..db import sqlite as sqlite_db
from ..instrument_catalog import (
    CATALOG_VERSION,
    ListingIdentity,
    compact_identity,
    listing_for_symbol,
    listing_for_text,
)
from ..parsers.name_resolver import resolve_ticker
from ..parsers.types import (
    ParsedInstrument,
    ParsedQuarantine,
    ParsedScopeIssue,
    ParsedStatement,
    ParsedTxn,
    ParseResult,
)
from .fund_lookup import lookup_fund_code

# Bump this when the deterministic resolver's meaning changes.  The cache also
# includes a fingerprint of reviewed aliases and reviewed fund lookups.
RESOLVER_VERSION = "identity-resolver-v5"

_EXPLICIT_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,8}$")
_UNRESOLVED_SYMBOLS = {"", "UNKNOWN", "N/A", "NONE"}
_NAME_TOKEN_SUFFIXES = ("INC", "LTD", "CORP", "FUND", "TRUST", "ETF")


def _normalized(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^A-Z0-9]+", " ", value.upper()).strip()


def _identity_terms(
    instrument: ParsedInstrument,
    *,
    description: str | None = None,
) -> list[str]:
    terms = [
        _normalized(instrument.symbol),
        _normalized(instrument.name),
        _normalized(description),
    ]
    return list(dict.fromkeys(term for term in terms if term))


def _looks_explicit(instrument: ParsedInstrument) -> bool:
    """Return true only for an identity safe to retain without a lookup."""
    if instrument.resolution_method == "unresolved_printed_identity":
        return False
    if instrument.asset_type == "option":
        return bool(
            instrument.option_root
            and instrument.option_expiry
            and instrument.option_strike is not None
            and instrument.option_type
        )
    symbol = (instrument.symbol or "").upper()
    if symbol in _UNRESOLVED_SYMBOLS or "_" in symbol:
        return False
    # Mutual-fund rows commonly use a synthetic printed name in this field;
    # only a reviewed identifier should turn one into a public fund code.
    if instrument.asset_type == "mutual_fund":
        return False
    compact_symbol = compact_identity(symbol)
    compact_name = compact_identity(instrument.name)
    if compact_name == compact_symbol and compact_symbol.endswith(_NAME_TOKEN_SUFFIXES):
        return False
    return bool(_EXPLICIT_SYMBOL.fullmatch(symbol))


def _set_resolution(
    instrument: ParsedInstrument,
    method: str,
    confidence: float,
) -> None:
    instrument.resolution_method = method
    instrument.resolution_confidence = confidence


def _copy_instrument_identity(target: ParsedInstrument, source: ParsedInstrument) -> None:
    target.asset_type = source.asset_type
    target.symbol = source.symbol
    target.currency = source.currency
    target.exchange = source.exchange
    target.name = source.name
    target.option_root = source.option_root
    target.option_expiry = source.option_expiry
    target.option_strike = source.option_strike
    target.option_type = source.option_type
    target.option_multiplier = source.option_multiplier
    target.issuer_key = source.issuer_key
    target.issuer_name = source.issuer_name
    target.security_key = source.security_key
    target.security_name = source.security_name
    target.journalable = source.journalable
    target.market_symbol = source.market_symbol


def _apply_listing(target: ParsedInstrument, listing: ListingIdentity) -> None:
    target.asset_type = listing.asset_type
    target.symbol = listing.symbol
    target.currency = listing.currency
    target.exchange = listing.exchange
    target.name = listing.security_name
    target.issuer_key = listing.issuer_key
    target.issuer_name = listing.issuer_name
    target.security_key = listing.security_key
    target.security_name = listing.security_name
    target.journalable = listing.journalable
    target.market_symbol = listing.yahoo_symbol


def _copy_database_identity(target: ParsedInstrument, row: sqlite3.Row) -> None:
    target.asset_type = str(row["asset_type"])
    target.symbol = str(row["symbol"])
    target.currency = str(row["currency"])
    target.exchange = row["exchange"]
    target.name = row["name"]
    target.option_root = row["option_root"]
    target.option_expiry = row["option_expiry"]
    target.option_strike = row["option_strike"]
    target.option_type = row["option_type"]
    target.option_multiplier = row["option_multiplier"] or 100
    keys = set(row.keys())
    target.issuer_key = row["issuer_key"] if "issuer_key" in keys else None
    target.issuer_name = row["issuer_name"] if "issuer_name" in keys else None
    target.security_key = row["security_key"] if "security_key" in keys else None
    target.security_name = row["security_name"] if "security_name" in keys else None
    target.journalable = bool(row["journalable"]) if "journalable" in keys else False
    target.market_symbol = row["provider_symbol"] if "provider_symbol" in keys else None


def resolver_cache_version(conn: sqlite3.Connection) -> str:
    """Version the resolver together with user-reviewed identity inputs."""
    aliases = [
        tuple(row)
        for row in conn.execute(
            """
            SELECT COALESCE(inst.code, ''), lower(ia.alias), i.instrument_key
              FROM instrument_aliases ia
              JOIN instruments i ON i.instrument_id = ia.instrument_id
              LEFT JOIN institutions inst ON inst.institution_id = ia.institution_id
             ORDER BY COALESCE(inst.code, ''), lower(ia.alias), i.instrument_key
            """
        )
    ]
    reviewed_funds = [
        tuple(row)
        for row in conn.execute(
            """
            SELECT institution_code, normalized_name, currency, asset_type,
                   resolved_symbol, resolved_exchange, resolved_name
              FROM instrument_identifier_lookups
             WHERE status = 'resolved' AND resolved_symbol IS NOT NULL
             ORDER BY institution_code, normalized_name, currency, asset_type,
                      resolved_symbol, resolved_exchange, resolved_name
            """
        )
    ]
    resolved_candidates = [
        tuple(row)
        for row in conn.execute(
            """
            SELECT institution.code, candidate.normalized_text,
                   candidate.asset_type, candidate.currency, instrument.instrument_key
              FROM instrument_resolution_candidates candidate
              JOIN institutions institution
                ON institution.institution_id = candidate.institution_id
              JOIN instruments instrument
                ON instrument.instrument_id = candidate.resolved_instrument_id
             WHERE candidate.status = 'resolved'
             ORDER BY institution.code, candidate.normalized_text,
                      candidate.asset_type, candidate.currency
            """
        )
    ]
    market_symbols = [
        tuple(row)
        for row in conn.execute(
            """
            SELECT instrument.instrument_key, market.provider,
                   market.provider_symbol
              FROM instrument_market_symbols market
              JOIN instruments instrument
                ON instrument.instrument_id = market.instrument_id
             WHERE market.status <> 'retired'
             ORDER BY instrument.instrument_key, market.provider
            """
        )
    ]
    payload = json.dumps(
        {
            "catalog": CATALOG_VERSION,
            "aliases": aliases,
            "reviewed_funds": reviewed_funds,
            "resolved_candidates": resolved_candidates,
            "market_symbols": market_symbols,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{RESOLVER_VERSION}:{digest}"


def _reviewed_alias(
    conn: sqlite3.Connection,
    *,
    institution_code: str,
    terms: list[str],
) -> sqlite3.Row | None:
    normalized_terms = set(terms)
    rows = conn.execute(
        """
        SELECT i.*, ia.alias
          FROM instrument_aliases ia
          JOIN instruments i ON i.instrument_id = ia.instrument_id
          LEFT JOIN institutions scoped ON scoped.institution_id = ia.institution_id
         WHERE ia.institution_id IS NULL OR scoped.code = ?
         ORDER BY CASE WHEN ia.institution_id IS NULL THEN 1 ELSE 0 END,
                  ia.alias_id
        """,
        (institution_code,),
    ).fetchall()
    for row in rows:
        if _normalized(row["alias"]) in normalized_terms:
            return row
    return None


def _database_identity(
    conn: sqlite3.Connection,
    *,
    institution_code: str,
    instrument: ParsedInstrument,
    terms: list[str],
) -> sqlite3.Row | None:
    compact_terms = {compact_identity(term) for term in terms if compact_identity(term)}
    if not compact_terms:
        return None
    institution = conn.execute(
        "SELECT institution_id FROM institutions WHERE code = ?", (institution_code,)
    ).fetchone()
    if institution is not None:
        placeholders = ",".join("?" * len(compact_terms))
        candidate = conn.execute(
            f"""
            SELECT i.*, issuer.issuer_key, issuer.canonical_name AS issuer_name,
                   security.security_key, security.canonical_name AS security_name,
                   security.journalable, market.provider_symbol
              FROM instrument_resolution_candidates candidate
              JOIN instruments i ON i.instrument_id = candidate.resolved_instrument_id
              LEFT JOIN securities security ON security.security_id = i.security_id
              LEFT JOIN security_issuers issuer ON issuer.issuer_id = security.issuer_id
              LEFT JOIN instrument_market_symbols market
                ON market.instrument_id = i.instrument_id AND market.provider = 'yahoo'
             WHERE candidate.institution_id = ?
               AND candidate.normalized_text IN ({placeholders})
               AND candidate.asset_type = ? AND candidate.currency = ?
               AND candidate.status = 'resolved'
            """,
            (
                institution["institution_id"],
                *sorted(compact_terms),
                instrument.asset_type,
                instrument.currency,
            ),
        ).fetchall()
        if len(candidate) == 1:
            return candidate[0]

    rows = conn.execute(
        """
        SELECT i.*, issuer.issuer_key, issuer.canonical_name AS issuer_name,
               security.security_key, security.canonical_name AS security_name,
               security.journalable, market.provider_symbol
          FROM instruments i
          LEFT JOIN securities security ON security.security_id = i.security_id
          LEFT JOIN security_issuers issuer ON issuer.issuer_id = security.issuer_id
          LEFT JOIN instrument_market_symbols market
            ON market.instrument_id = i.instrument_id AND market.provider = 'yahoo'
         WHERE i.currency = ? AND i.asset_type IN ('equity','etf','bond')
           AND (i.security_id IS NOT NULL OR market.market_symbol_id IS NOT NULL)
        """,
        (instrument.currency,),
    ).fetchall()
    matches = [
        row
        for row in rows
        if compact_identity(row["name"]) in compact_terms
        or compact_identity(row["symbol"]) in compact_terms
    ]
    distinct = {int(row["instrument_id"]): row for row in matches}
    return next(iter(distinct.values())) if len(distinct) == 1 else None


def _catalog_identity(
    instrument: ParsedInstrument,
    *,
    institution_code: str,
    description: str | None,
) -> ListingIdentity | None:
    direct = listing_for_symbol(instrument.symbol, instrument.currency)
    if direct is not None:
        return direct
    for value in (instrument.symbol, instrument.name, description):
        match = listing_for_text(
            value,
            instrument.currency,
            institution_code=institution_code,
        )
        if match is not None:
            return match
    return None


def _resolve_instrument(
    conn: sqlite3.Connection,
    *,
    institution_code: str,
    instrument: ParsedInstrument,
    description: str | None = None,
) -> str:
    """Resolve one parsed instrument without guessing from free-form names."""
    # A fully printed option contract is already a stronger identity than any
    # underlying ticker catalog entry. Resolving NTR/BCE/etc. through the
    # listing catalog would erase expiry, strike, and call/put identity and
    # incorrectly store a short option as a negative equity holding.
    if instrument.asset_type == "option" and _looks_explicit(instrument):
        _set_resolution(instrument, "printed_option_contract", 1.0)
        return "printed_option_contract"

    catalog = _catalog_identity(
        instrument,
        institution_code=institution_code,
        description=description,
    )
    if catalog is not None:
        original_symbol = instrument.symbol
        _apply_listing(instrument, catalog)
        method = "catalog_symbol" if original_symbol == catalog.symbol else "catalog_name"
        _set_resolution(instrument, method, 1.0)
        return method

    terms = _identity_terms(instrument, description=description)
    alias = _reviewed_alias(
        conn,
        institution_code=institution_code,
        terms=terms,
    )
    if alias is not None:
        _copy_database_identity(instrument, alias)
        _set_resolution(instrument, "reviewed_alias", 1.0)
        return "reviewed_alias"

    known = _database_identity(
        conn,
        institution_code=institution_code,
        instrument=instrument,
        terms=terms,
    )
    if known is not None:
        _copy_database_identity(instrument, known)
        _set_resolution(instrument, "known_listing", 1.0)
        return "known_listing"

    if _looks_explicit(instrument):
        method = (
            "printed_ticker_change"
            if instrument.resolution_method == "printed_ticker_change"
            else "printed_option_contract"
            if instrument.asset_type == "option"
            else "printed_symbol"
        )
        _set_resolution(instrument, method, 1.0)
        return method

    if instrument.asset_type == "mutual_fund":
        match = lookup_fund_code(
            conn,
            fund_name=instrument.name or instrument.symbol,
            currency=instrument.currency,
            institution_code=institution_code,
            sample_description=description or instrument.name or instrument.symbol,
        )
        if match is not None:
            instrument.asset_type = match.asset_type
            instrument.symbol = match.symbol
            instrument.exchange = match.exchange
            instrument.name = match.name
            instrument.option_root = None
            instrument.option_expiry = None
            instrument.option_strike = None
            instrument.option_type = None
            instrument.option_multiplier = 100
            _set_resolution(instrument, "reviewed_fund_lookup", 1.0)
            return "reviewed_fund_lookup"

    resolved_name = resolve_ticker(
        description or instrument.name or instrument.symbol,
        instrument.currency,
    )
    if resolved_name is not None:
        resolved_symbol, _asset_type = resolved_name
        catalog = listing_for_symbol(resolved_symbol, instrument.currency)
        if catalog is not None:
            _apply_listing(instrument, catalog)
            _set_resolution(instrument, "catalog_name", 1.0)
            return "catalog_name"

    candidate_text = next(
        (value for value in (instrument.name, description, instrument.symbol) if value),
        instrument.symbol,
    )
    normalized_candidate = compact_identity(candidate_text)
    if normalized_candidate:
        sqlite_db.queue_instrument_resolution_candidate(
            conn,
            institution_code=institution_code,
            normalized_text=normalized_candidate,
            display_text=candidate_text,
            asset_type=instrument.asset_type,
            currency=instrument.currency,
        )

    _set_resolution(instrument, "unresolved_printed_identity", 0.0)
    return "unresolved_printed_identity"


def _same_statement_holding(
    statement: ParsedStatement,
    transaction: ParsedTxn,
) -> ParsedInstrument | None:
    if transaction.instrument is None:
        return None
    targets: list[ParsedInstrument] = []
    transaction_terms = set(
        _identity_terms(transaction.instrument, description=transaction.description)
    )
    if not transaction_terms:
        return None
    for position in statement.positions:
        if transaction_terms.intersection(_identity_terms(position.instrument)):
            targets.append(position.instrument)
    # Multiple exact holdings can still be ambiguous (for example, a duplicate
    # broker description in CAD and USD).  Do not choose one silently.
    distinct = {
        (
            item.asset_type,
            item.symbol,
            item.currency,
            item.option_root,
            item.option_expiry,
            item.option_strike,
            item.option_type,
            item.option_multiplier,
        ): item
        for item in targets
    }
    return next(iter(distinct.values())) if len(distinct) == 1 else None


def _same_statement_symbol_holding(
    statement: ParsedStatement,
    transaction: ParsedTxn,
) -> ParsedInstrument | None:
    """Return one holding with the exact printed symbol/native currency.

    Brokers inconsistently label an ETF as ``equity`` in holdings and ``etf``
    in activity. Symbol and currency identify the same printed listing; copying
    the resolved holding identity prevents asset-type-only key splits without
    using a name or a reconciliation residual.
    """
    if transaction.instrument is None:
        return None
    symbol = compact_identity(transaction.instrument.symbol)
    currency = transaction.instrument.currency
    matches = [
        position.instrument
        for position in statement.positions
        if position.instrument.currency == currency
        and compact_identity(position.instrument.symbol) == symbol
    ]
    distinct = {
        (
            item.asset_type,
            item.symbol,
            item.currency,
            item.option_root,
            item.option_expiry,
            item.option_strike,
            item.option_type,
            item.option_multiplier,
        ): item
        for item in matches
    }
    return next(iter(distinct.values())) if len(distinct) == 1 else None


def resolve_parse_result(
    conn: sqlite3.Connection,
    *,
    institution_code: str,
    result: ParseResult,
) -> dict[str, int]:
    """Resolve a validated result in-place and return deterministic counts.

    Call this inside the source activation transaction.  A reviewed lookup may
    enqueue an unresolved fund name, so a later failed staging operation rolls
    that incidental derived state back as well.
    """
    methods: Counter[str] = Counter()
    for statement in result.statements:
        resolved_positions = []
        for position in statement.positions:
            method = _resolve_instrument(
                conn,
                institution_code=institution_code,
                instrument=position.instrument,
            )
            if method == "unresolved_printed_identity":
                quarantine = ParsedQuarantine(
                    raw_line=position.raw_line or "",
                    reason="position identity unresolved; row not persisted",
                    source_span=position.source_span,
                )
                statement.quarantine.append(quarantine)
                for scope in statement.snapshot_sets:
                    if (
                        scope.currency == position.currency
                        and scope.section_type == "positions"
                        and scope.scope_key == position.scope_key
                    ):
                        if scope.completeness == "complete":
                            scope.completeness = "unknown"
                            scope.validation_status = "warning"
                        scope.issues.append(ParsedScopeIssue(
                            issue_code="holding_identity_missing",
                            severity="error",
                            detail={"resolution_method": method},
                            blocks_completeness=True,
                            source_span=position.source_span,
                            quarantine=quarantine,
                        ))
                methods["quarantined_unresolved_position"] += 1
                continue
            resolved_positions.append(position)
            methods[method] += 1
        statement.positions = resolved_positions

        for transaction in statement.transactions:
            if transaction.instrument is None:
                continue
            method = _resolve_instrument(
                conn,
                institution_code=institution_code,
                instrument=transaction.instrument,
                description=transaction.description,
            )
            if method == "printed_symbol":
                holding = _same_statement_symbol_holding(statement, transaction)
                if (
                    holding is not None
                    and holding.asset_type != transaction.instrument.asset_type
                ):
                    _copy_instrument_identity(transaction.instrument, holding)
                    _set_resolution(
                        transaction.instrument,
                        "same_statement_symbol",
                        1.0,
                    )
                    method = "same_statement_symbol"
            if method == "unresolved_printed_identity":
                holding = _same_statement_holding(statement, transaction)
                if holding is not None:
                    _copy_instrument_identity(transaction.instrument, holding)
                    _set_resolution(transaction.instrument, "same_statement_holding", 1.0)
                    method = "same_statement_holding"
            transaction.resolution_method = transaction.instrument.resolution_method
            transaction.resolution_confidence = transaction.instrument.resolution_confidence
            if transaction.resolution_evidence is None:
                transaction.resolution_evidence = transaction.instrument.resolution_evidence or transaction.source_span
            if method == "unresolved_printed_identity":
                # Preserve the transaction and printed description, but never
                # persist a made-up name token as though it were a ticker.
                transaction.instrument = None
            methods[method] += 1
            if transaction.related_instrument is not None:
                related_method = _resolve_instrument(
                    conn,
                    institution_code=institution_code,
                    instrument=transaction.related_instrument,
                    description=transaction.description,
                )
                if related_method == "unresolved_printed_identity":
                    transaction.related_instrument = None
                methods[f"related:{related_method}"] += 1
    return dict(sorted(methods.items()))
