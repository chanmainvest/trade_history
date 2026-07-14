"""Conservative, deterministic identity resolution for staged ingestion.

Parsers preserve what the statement printed.  This module may only replace an
ambiguous parsed identity with a reviewed alias/fund lookup or an exact
same-statement holding identity.  It deliberately does not call the broad
name-to-ticker repair map: an uncertain statement name remains an auditable
unresolved instrument instead of a guessed public ticker.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter

from ..parsers.types import ParsedInstrument, ParsedStatement, ParsedTxn, ParseResult
from .fund_lookup import lookup_fund_code

# Bump this when the deterministic resolver's meaning changes.  The cache also
# includes a fingerprint of reviewed aliases and reviewed fund lookups.
RESOLVER_VERSION = "identity-resolver-v1"

_EXPLICIT_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,19}$")
_UNRESOLVED_SYMBOLS = {"", "UNKNOWN", "N/A", "NONE"}


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
    payload = json.dumps(
        {"aliases": aliases, "reviewed_funds": reviewed_funds},
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


def _resolve_instrument(
    conn: sqlite3.Connection,
    *,
    institution_code: str,
    instrument: ParsedInstrument,
    description: str | None = None,
) -> str:
    """Resolve one parsed instrument without guessing from free-form names."""
    if _looks_explicit(instrument):
        method = "printed_option_contract" if instrument.asset_type == "option" else "printed_symbol"
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
        for position in statement.positions:
            methods[_resolve_instrument(
                conn,
                institution_code=institution_code,
                instrument=position.instrument,
            )] += 1

        for transaction in statement.transactions:
            if transaction.instrument is None:
                continue
            method = _resolve_instrument(
                conn,
                institution_code=institution_code,
                instrument=transaction.instrument,
                description=transaction.description,
            )
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
            methods[method] += 1
    return dict(sorted(methods.items()))
