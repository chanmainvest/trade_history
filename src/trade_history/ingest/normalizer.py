"""Normalize raw extractor output into canonical DB records."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from pathlib import Path

from trade_history.db.sqlite import (
    lookup_symbol_by_description,
    upsert_account,
    upsert_description_symbol_map,
    upsert_instrument,
)
from trade_history.extractors.base import RawPosition, RawStatement, RawTransaction
from trade_history.extractors.utils import (
    normalize_description,
    parse_cibc_option,
    parse_rbc_option,
    parse_td_option,
)

log = logging.getLogger(__name__)

# Canonical activity verbs (raw → canonical)
_ACTIVITY_NORMALIZER: dict[str, str] = {
    "bought": "bought",
    "buy": "bought",
    "purchase": "bought",
    "sold": "sold",
    "sell": "sold",
    "dividend": "dividend",
    "div": "dividend",
    "interest": "interest",
    "fee": "fee",
    "commission": "fee",
    "transfer_in": "transfer_in",
    "transfer_out": "transfer_out",
    "transfer": "transfer_in",
    "reinvestment": "reinvestment",
    "reinvest": "reinvestment",
    "contribution": "contribution",
    "deposit": "contribution",
    "withdrawal": "withdrawal",
    "exercise": "exercise",
    "assignment": "assignment",
    "expired": "expired",
    "expiry": "expired",
    "withholding_tax": "withholding_tax",
    "withholding": "withholding_tax",
    "journalled": "journalled",
    "initial_holding": "initial_holding",
    "exchange": "exchange",
    "stock_split": "stock_split",
    "adjustment": "adjustment",
    "mark_to_market": "mark_to_market",
    "return_of_capital": "return_of_capital",
    "cash_in_lieu": "cash_in_lieu",
    "name_change": "name_change",
    "fx_conversion": "fx_conversion",
    "fx_equivalent": "fx_equivalent",
    "corporate_action": "corporate_action",
    "merger": "exchange",
    "other": "other",
    "managed": "other",
}


def normalize_activity(raw: str) -> str:
    return _ACTIVITY_NORMALIZER.get(raw.lower(), "other")


def store_statement(
    conn: sqlite3.Connection,
    stmt: RawStatement,
    transactions: list[RawTransaction],
    positions: list[RawPosition],
    source_file: Path,
    *,
    statement_id: int | None = None,
) -> int:
    """
    Upsert account, instruments, and insert transactions/positions.
    Returns the count of transactions stored.
    """
    as_of = stmt.period_end.isoformat() if stmt.period_end else None

    account_db_id = upsert_account(
        conn,
        institution=stmt.institution,
        account_id=stmt.account_id,
        account_type=stmt.account_type,
        primary_currency=stmt.primary_currency,
        as_of_date=as_of,
    )

    # Build description→symbol map from positions FIRST so transactions can use it
    _populate_symbol_map_from_positions(conn, stmt.institution, positions)

    tx_count = 0
    for raw_tx in transactions:
        instrument_id = _resolve_instrument(conn, raw_tx, stmt.institution)
        canonical_activity = normalize_activity(raw_tx.activity)

        conn.execute(
            """
            INSERT INTO transactions
                (account_id, instrument_id, statement_id, trade_date, settle_date,
                 activity, quantity, price, amount, currency, commission,
                 source_file, raw_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_db_id,
                instrument_id,
                statement_id,
                raw_tx.date.isoformat(),
                raw_tx.settle_date.isoformat() if raw_tx.settle_date else None,
                canonical_activity,
                float(raw_tx.quantity) if raw_tx.quantity is not None else None,
                float(raw_tx.price) if raw_tx.price is not None else None,
                float(raw_tx.amount),
                raw_tx.currency,
                float(raw_tx.commission),
                str(source_file),
                raw_tx.raw_text,
            ),
        )
        tx_count += 1

    # Upsert positions (as of period_end)
    if as_of:
        for pos in positions:
            pos_instrument_id = _resolve_position_instrument(conn, pos)
            conn.execute(
                """
                INSERT INTO position_state
                    (account_id, instrument_id, as_of_date,
                     quantity, book_cost, book_cost_currency,
                     market_price, market_value, market_currency)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, instrument_id, as_of_date) DO UPDATE SET
                    quantity = excluded.quantity,
                    market_price = excluded.market_price,
                    market_value = excluded.market_value
                """,
                (
                    account_db_id,
                    pos_instrument_id,
                    as_of,
                    float(pos.quantity),
                    float(pos.book_cost) if pos.book_cost is not None else None,
                    pos.currency,
                    float(pos.market_price) if pos.market_price is not None else None,
                    float(pos.market_value) if pos.market_value is not None else None,
                    pos.currency,
                ),
            )

    # Create initial_holding transactions for the first statement of this account
    if positions and _is_first_statement(conn, account_db_id, statement_id):
        initial_count = _create_initial_holdings(
            conn, account_db_id, stmt, positions, source_file, statement_id
        )
        tx_count += initial_count

    conn.commit()
    return tx_count


def _is_first_statement(
    conn: sqlite3.Connection,
    account_db_id: int,
    current_statement_id: int | None,
) -> bool:
    """Check if this is the first statement for the account."""
    # Get the account_id string to check in statement_registry
    row = conn.execute("SELECT account_id FROM accounts WHERE id = ?", (account_db_id,)).fetchone()
    if not row:
        return False
    account_id = row[0]

    # Check if any other statement_registry entries exist for this account
    # (excluding the current statement being processed)
    existing = conn.execute(
        """SELECT COUNT(*) FROM statement_registry
           WHERE account_id = ? AND status = 'ok' AND id != ?""",
        (account_id, current_statement_id or -1),
    ).fetchone()
    return existing[0] == 0


def _create_initial_holdings(
    conn: sqlite3.Connection,
    account_db_id: int,
    stmt: RawStatement,
    positions: list[RawPosition],
    source_file: Path,
    statement_id: int | None,
) -> int:
    """Create synthetic initial_holding transactions for the first statement."""
    # Use Jan 1 of the statement's year
    initial_date = date(stmt.period_start.year, 1, 1)
    count = 0

    for pos in positions:
        if pos.asset_type == "cash" or pos.quantity == 0:
            continue

        instrument_id = _resolve_position_instrument(conn, pos)
        amount = float(pos.book_cost or pos.market_value or 0)

        conn.execute(
            """
            INSERT INTO transactions
                (account_id, instrument_id, statement_id, trade_date, activity,
                 quantity, price, amount, currency, source_file, raw_text)
            VALUES (?, ?, ?, ?, 'initial_holding', ?, ?, ?, ?, ?, ?)
            """,
            (
                account_db_id,
                instrument_id,
                statement_id,
                initial_date.isoformat(),
                float(pos.quantity),
                float(pos.market_price) if pos.market_price else None,
                amount,
                pos.currency,
                str(source_file),
                f"Initial holding: {pos.description}",
            ),
        )
        count += 1
        log.info(
            "Created initial_holding: %s qty=%s for account %s",
            pos.description, pos.quantity, stmt.account_id,
        )

    return count


def _populate_symbol_map_from_positions(
    conn: sqlite3.Connection,
    institution: str,
    positions: list[RawPosition],
) -> None:
    """Build description→symbol map from position/holdings data."""
    for pos in positions:
        if not pos.symbol or pos.asset_type == "cash":
            continue
        # Use normalized description as the key
        desc_norm = normalize_description(pos.description)
        if desc_norm:
            upsert_description_symbol_map(
                conn,
                institution=institution,
                description=desc_norm,
                symbol=pos.symbol,
            )


# Activities that never need an instrument (purely cash movements)
_CASH_ONLY_ACTIVITIES = {
    "contribution", "withdrawal", "interest", "fee",
    "withholding_tax", "journalled", "other",
    "mark_to_market", "fx_conversion", "fx_equivalent",
    "adjustment", "cash_in_lieu",
}


def _resolve_instrument(
    conn: sqlite3.Connection, tx: RawTransaction, institution: str = "",
) -> int | None:
    """Map a raw transaction to an instrument_id (or None for cash activities)."""
    # Cash-only activities genuinely have no instrument
    canonical = normalize_activity(tx.activity)
    if canonical in _CASH_ONLY_ACTIVITIES:
        return None

    # Try option parsers first — they produce canonical symbols.
    # Extractors may strip trailing numerics (day/year/strike) from descriptions,
    # so raw_text fallbacks are used for CIBC and RBC where this is common.
    opt = (
        parse_cibc_option(tx.description)
        or parse_cibc_option(tx.raw_text)
        or parse_rbc_option(tx.description)
        or parse_rbc_option(tx.raw_text)
        or parse_td_option(tx.description)
    )

    if opt:
        symbol = f"{opt.put_call.upper()}{opt.root}{opt.expiry.strftime('%y%m%d')}{int(opt.strike)}"
        return upsert_instrument(
            conn,
            symbol=symbol,
            name=tx.description,
            asset_type="option",
            option_root=opt.root,
            strike=float(opt.strike),
            expiry=opt.expiry.isoformat(),
            put_call=opt.put_call,
            multiplier=opt.multiplier,
        )

    # For equities: try description→symbol map first (from holdings data),
    # then fall back to normalized description.
    desc_symbol = normalize_description(tx.description)

    # Look up in description_symbol_map (populated from holdings)
    if desc_symbol and institution:
        mapped_symbol = lookup_symbol_by_description(conn, institution, desc_symbol)
        if mapped_symbol:
            return upsert_instrument(
                conn,
                symbol=mapped_symbol,
                name=tx.description,
                asset_type="equity",
            )

    # Fall back to normalized description or extractor-provided symbol
    symbol = desc_symbol or tx.symbol
    if symbol is None:
        return None

    return upsert_instrument(
        conn,
        symbol=symbol,
        name=tx.description,
        asset_type="equity",
    )


def _resolve_position_instrument(conn: sqlite3.Connection, pos: RawPosition) -> int:
    """Map a raw position to an instrument_id."""
    if pos.asset_type == "option" and pos.symbol:
        opt = (
            parse_cibc_option(pos.description)
            or parse_rbc_option(pos.description)
            or parse_td_option(pos.description)
        )
        if opt:
            symbol = f"{opt.put_call.upper()}{opt.root}{opt.expiry.strftime('%y%m%d')}{int(opt.strike)}"
            return upsert_instrument(
                conn,
                symbol=symbol,
                name=pos.description,
                asset_type="option",
                option_root=opt.root,
                strike=float(opt.strike),
                expiry=opt.expiry.isoformat(),
                put_call=opt.put_call,
            )

    symbol = pos.symbol or pos.description[:20]
    return upsert_instrument(
        conn,
        symbol=symbol,
        name=pos.description,
        asset_type=pos.asset_type,
    )
