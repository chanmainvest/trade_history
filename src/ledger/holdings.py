"""Canonical, read-only holdings reconstruction for every API consumer.

The service deliberately keeps broker checkpoints separate from transaction
movements.  A complete scope is an anchor; an incomplete scope is a quality
signal, never permission to clear or replace an earlier anchor.
"""
from __future__ import annotations

import copy
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from .config import DUCKDB_PATH
from .db import sqlite as sqlite_db
from .quantity import (
    LEGACY_UNDERIVABLE_POSITION_TYPES,
    NON_CASH_TXN_TYPES,
    POSITION_AFFECTING_TYPES,
    contextual_position_delta,
    normalized_position_delta,
)
from .statement_selection import canonical_statement_clause

EPSILON = 1e-9


@dataclass(frozen=True)
class _ScopeAnchor:
    snapshot_set_id: int
    statement_id: int
    account_id: int
    as_of_date: str
    currency: str
    scope_key: str


@dataclass
class _SecurityState:
    account_id: int
    currency: str
    scope_key: str
    instrument_id: int
    instrument_key: str
    symbol: str
    pricing_symbol: str
    asset_type: str
    option_expiry: str | None
    option_strike: float | None
    option_type: str | None
    quantity: float
    source_snapshot_id: int | None = None
    anchor: _ScopeAnchor | None = None
    initial_date: str | None = None
    anchor_quantity: float | None = None
    avg_cost: float | None = None
    book_value: float | None = None
    market_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None
    position_movement: bool = False
    cost_basis_stale: bool = False
    incomplete: bool = False
    warnings: set[str] = field(default_factory=set)
    lineage_key: str | None = None
    ticker_symbols: tuple[str, ...] = ()
    anchor_instrument_id: int | None = None


@dataclass
class _CashState:
    account_id: int
    currency: str
    scope_key: str
    balance: float
    source_cash_balance_id: int | None = None
    anchor: _ScopeAnchor | None = None
    initial_date: str | None = None
    cash_movement: bool = False
    incomplete: bool = False
    warnings: set[str] = field(default_factory=set)


def _account_clause(column: str, account_ids: list[int]) -> tuple[str, list[int]]:
    if not account_ids:
        return "", []
    return f" AND {column} IN ({','.join('?' * len(account_ids))})", list(account_ids)


def _scope_identity(row: dict) -> tuple[int, str, str]:
    return (int(row["account_id"]), str(row["currency"]), str(row["scope_key"]))


def _scope_rank(row: dict) -> tuple[str, int, int]:
    return (str(row["as_of_date"]), int(row["statement_id"]), int(row["snapshot_set_id"]))


def _latest_scopes(rows: list[dict], *, complete_only: bool) -> dict[tuple[int, str, str], dict]:
    latest: dict[tuple[int, str, str], dict] = {}
    for row in rows:
        if complete_only and row["completeness"] != "complete":
            continue
        key = _scope_identity(row)
        prior = latest.get(key)
        if prior is None or _scope_rank(row) > _scope_rank(prior):
            latest[key] = row
    return latest


def _anchor_from_row(row: dict) -> _ScopeAnchor:
    return _ScopeAnchor(
        snapshot_set_id=int(row["snapshot_set_id"]),
        statement_id=int(row["statement_id"]),
        account_id=int(row["account_id"]),
        as_of_date=str(row["as_of_date"]),
        currency=str(row["currency"]),
        scope_key=str(row["scope_key"]),
    )


def _fetch_scope_rows(
    conn: sqlite3.Connection,
    *,
    section_type: str,
    as_of: str,
    account_ids: list[int],
) -> list[dict]:
    account_sql, account_params = _account_clause("ss.account_id", account_ids)
    canonical_sql = canonical_statement_clause("ss.statement_id")
    rows = conn.execute(
        f"""
        SELECT ss.snapshot_set_id, ss.statement_id, ss.account_id, ss.as_of_date,
               ss.currency, ss.scope_key, ss.completeness
          FROM snapshot_sets ss
         WHERE ss.section_type = ?
           AND ss.as_of_date <= ?
           AND {canonical_sql}
           {account_sql}
        """,
        (section_type, as_of, *account_params),
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_accounts(conn: sqlite3.Connection, account_ids: list[int]) -> dict[int, dict]:
    account_sql, account_params = _account_clause("a.account_id", account_ids)
    rows = conn.execute(
        f"""
        SELECT a.account_id, a.account_number, a.nickname,
               i.code AS institution_code, i.display_name AS institution_name
          FROM accounts a
          JOIN institutions i ON i.institution_id = a.institution_id
         WHERE 1 = 1 {account_sql}
        """,
        account_params,
    ).fetchall()
    return {int(row["account_id"]): dict(row) for row in rows}


def _fetch_position_rows(conn: sqlite3.Connection, snapshot_set_ids: list[int]) -> list[dict]:
    if not snapshot_set_ids:
        return []
    placeholders = ",".join("?" * len(snapshot_set_ids))
    rows = conn.execute(
        f"""
        SELECT ps.snapshot_id, ps.snapshot_set_id, ps.account_id,
               ps.instrument_id, ps.quantity,
               ps.avg_cost, ps.book_value, ps.market_price, ps.market_value,
               ps.unrealized_pnl, ps.currency,
               i.instrument_key, COALESCE(i.option_root, i.symbol) AS symbol,
               COALESCE(market.provider_symbol, i.symbol) AS pricing_symbol,
               i.asset_type, i.option_expiry, i.option_strike, i.option_type
          FROM position_snapshots ps
          JOIN instruments i ON i.instrument_id = ps.instrument_id
          LEFT JOIN instrument_market_symbols market
            ON market.instrument_id = i.instrument_id
           AND market.provider = 'yahoo'
           AND market.status IN ('candidate','verified','failed')
         WHERE ps.snapshot_set_id IN ({placeholders})
        """,
        snapshot_set_ids,
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_cash_rows(conn: sqlite3.Connection, snapshot_set_ids: list[int]) -> list[dict]:
    if not snapshot_set_ids:
        return []
    placeholders = ",".join("?" * len(snapshot_set_ids))
    rows = conn.execute(
        f"""
        SELECT cash_balance_id, snapshot_set_id, account_id, currency,
               opening_balance, closing_balance
          FROM cash_balances
         WHERE snapshot_set_id IN ({placeholders})
        """,
        snapshot_set_ids,
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_initial_positions(
    conn: sqlite3.Connection,
    *,
    as_of: str,
    account_ids: list[int],
) -> dict[tuple[int, str, str], dict]:
    account_sql, account_params = _account_clause("ip.account_id", account_ids)
    rows = conn.execute(
        f"""
        SELECT ip.account_id, ip.as_of_date, ip.instrument_id, ip.quantity,
               ip.avg_cost, ip.currency, i.instrument_key,
               COALESCE(i.option_root, i.symbol) AS symbol,
               COALESCE(market.provider_symbol, i.symbol) AS pricing_symbol, i.asset_type,
               i.option_expiry, i.option_strike, i.option_type
          FROM initial_positions ip
          JOIN instruments i ON i.instrument_id = ip.instrument_id
          LEFT JOIN instrument_market_symbols market
            ON market.instrument_id = i.instrument_id
           AND market.provider = 'yahoo'
           AND market.status IN ('candidate','verified','failed')
         WHERE ip.as_of_date <= ? {account_sql}
        """,
        (as_of, *account_params),
    ).fetchall()
    latest: dict[tuple[int, str, str], dict] = {}
    for raw_row in rows:
        row = dict(raw_row)
        key = (int(row["account_id"]), str(row["currency"]), str(row["instrument_key"]))
        prior = latest.get(key)
        if prior is None or str(row["as_of_date"]) > str(prior["as_of_date"]):
            latest[key] = row
    return latest


def _fetch_initial_cash(
    conn: sqlite3.Connection,
    *,
    as_of: str,
    account_ids: list[int],
) -> dict[tuple[int, str], dict]:
    account_sql, account_params = _account_clause("ic.account_id", account_ids)
    rows = conn.execute(
        f"""
        SELECT ic.account_id, ic.as_of_date, ic.currency, ic.balance
          FROM initial_cash ic
         WHERE ic.as_of_date <= ? {account_sql}
        """,
        (as_of, *account_params),
    ).fetchall()
    latest: dict[tuple[int, str], dict] = {}
    for raw_row in rows:
        row = dict(raw_row)
        key = (int(row["account_id"]), str(row["currency"]))
        prior = latest.get(key)
        if prior is None or str(row["as_of_date"]) > str(prior["as_of_date"]):
            latest[key] = row
    return latest


def _fetch_transactions(
    conn: sqlite3.Connection,
    *,
    as_of: str,
    account_ids: list[int],
) -> list[dict]:
    account_sql, account_params = _account_clause("t.account_id", account_ids)
    canonical_sql = canonical_statement_clause("t.statement_id")
    rows = conn.execute(
        f"""
        SELECT t.transaction_id, t.account_id, t.trade_date, t.settle_date,
               t.txn_type, t.instrument_id, t.quantity, t.position_delta,
               t.net_amount, t.cash_delta, t.cash_effective_date, t.currency,
                i.instrument_key, i.currency AS instrument_currency,
                COALESCE(i.option_root, i.symbol) AS symbol,
                COALESCE(market.provider_symbol, i.symbol) AS pricing_symbol, i.asset_type,
                i.option_expiry, i.option_strike, i.option_type,
                tc.conversion_ratio AS ticker_change_ratio,
                successor.instrument_id AS successor_instrument_id,
                successor.instrument_key AS successor_instrument_key,
                successor.currency AS successor_currency,
                COALESCE(successor.option_root, successor.symbol) AS successor_symbol,
                COALESCE(successor_market.provider_symbol, successor.symbol)
                    AS successor_pricing_symbol,
                successor.asset_type AS successor_asset_type,
                successor.option_expiry AS successor_option_expiry,
                successor.option_strike AS successor_option_strike,
                successor.option_type AS successor_option_type
          FROM transactions t
          LEFT JOIN instruments i ON i.instrument_id = t.instrument_id
          LEFT JOIN instrument_market_symbols market
            ON market.instrument_id = i.instrument_id
           AND market.provider = 'yahoo'
           AND market.status IN ('candidate','verified','failed')
          LEFT JOIN instrument_ticker_change_sources source
                 ON source.transaction_id = t.transaction_id
          LEFT JOIN instrument_ticker_changes tc
                 ON tc.ticker_change_id = source.ticker_change_id
          LEFT JOIN instruments successor
                 ON successor.instrument_id = tc.to_instrument_id
          LEFT JOIN instrument_market_symbols successor_market
            ON successor_market.instrument_id = successor.instrument_id
           AND successor_market.provider = 'yahoo'
           AND successor_market.status IN ('candidate','verified','failed')
         WHERE (t.trade_date <= ? OR COALESCE(t.cash_effective_date, t.trade_date) <= ?)
           AND {canonical_sql}
           {account_sql}
         ORDER BY t.trade_date, t.transaction_id
        """,
        (as_of, as_of, *account_params),
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_ticker_lineages(
    conn: sqlite3.Connection,
) -> dict[int, tuple[str, tuple[str, ...]]]:
    rows = conn.execute(
        """
        SELECT tc.from_instrument_id, tc.to_instrument_id, tc.effective_date,
               old.instrument_key AS old_key, old.symbol AS old_symbol,
               new.instrument_key AS new_key, new.symbol AS new_symbol
          FROM instrument_ticker_changes tc
          JOIN instruments old ON old.instrument_id = tc.from_instrument_id
          JOIN instruments new ON new.instrument_id = tc.to_instrument_id
         ORDER BY tc.effective_date, tc.ticker_change_id
        """
    ).fetchall()
    predecessor = {int(row["to_instrument_id"]): int(row["from_instrument_id"]) for row in rows}
    successor = {int(row["from_instrument_id"]): int(row["to_instrument_id"]) for row in rows}
    details: dict[int, tuple[str, str]] = {}
    for row in rows:
        details[int(row["from_instrument_id"])] = (str(row["old_key"]), str(row["old_symbol"]))
        details[int(row["to_instrument_id"])] = (str(row["new_key"]), str(row["new_symbol"]))
    output: dict[int, tuple[str, tuple[str, ...]]] = {}
    for instrument_id in details:
        root = instrument_id
        while root in predecessor:
            root = predecessor[root]
        chain: list[int] = []
        current = root
        while current in details and current not in chain:
            chain.append(current)
            if current not in successor:
                break
            current = successor[current]
        root_key = details[root][0]
        symbols = tuple(details[item][1] for item in chain)
        for item in chain:
            output[item] = (root_key, symbols)
    return output


def _fetch_reconciliation_results(
    conn: sqlite3.Connection,
    *,
    position_set_ids: list[int],
    cash_set_ids: list[int],
) -> tuple[dict[tuple[int, int], dict], dict[int, dict]]:
    position_results: dict[tuple[int, int], dict] = {}
    cash_results: dict[int, dict] = {}
    if position_set_ids:
        placeholders = ",".join("?" * len(position_set_ids))
        rows = conn.execute(
            f"""
            SELECT snapshot_set_id, instrument_id, status, reason, reconciliation_id
              FROM reconciliation_results
             WHERE kind = 'position'
               AND reconciliation_key LIKE 'recon:v1:position:%'
               AND snapshot_set_id IN ({placeholders})
             ORDER BY reconciliation_id DESC
            """,
            position_set_ids,
        ).fetchall()
        for raw_row in rows:
            row = dict(raw_row)
            if row["instrument_id"] is not None:
                position_results.setdefault(
                    (int(row["snapshot_set_id"]), int(row["instrument_id"])), row
                )
    if cash_set_ids:
        placeholders = ",".join("?" * len(cash_set_ids))
        rows = conn.execute(
            f"""
            SELECT snapshot_set_id, status, reason, reconciliation_id
              FROM reconciliation_results
             WHERE kind = 'cash'
               AND reconciliation_key LIKE 'recon:v1:cash:statement:%'
               AND snapshot_set_id IN ({placeholders})
             ORDER BY reconciliation_id DESC
            """,
            cash_set_ids,
        ).fetchall()
        for raw_row in rows:
            row = dict(raw_row)
            cash_results.setdefault(int(row["snapshot_set_id"]), row)
    return position_results, cash_results


def _stored_position_effect(row: dict) -> float | None:
    """Use the same conservative movement contract as reconciliation."""
    if row["position_delta"] is not None:
        effect = float(row["position_delta"])
        if row["txn_type"] in LEGACY_UNDERIVABLE_POSITION_TYPES and abs(effect) <= EPSILON:
            return None
        return effect
    effect = normalized_position_delta(row["txn_type"], row["quantity"])
    if effect is None:
        return None if row["txn_type"] in POSITION_AFFECTING_TYPES else 0.0
    return effect


def _scope_candidates_for_security(
    states: dict[tuple[int, str, str, str], _SecurityState],
    anchors: dict[tuple[int, str, str], _ScopeAnchor],
    *,
    account_id: int,
    currency: str,
    instrument_key: str,
) -> list[str]:
    available = {
        scope_key
        for account, state_currency, scope_key in anchors
        if account == account_id and state_currency == currency
    }
    # Transactions do not carry a snapshot scope.  Applying one to multiple
    # independently complete scopes would manufacture quantity, so leave the
    # state unchanged and let the caller mark the ambiguity instead.
    if len(available) > 1:
        return []
    existing = {
        scope_key
        for (account, state_currency, scope_key, key) in states
        if account == account_id and state_currency == currency and key == instrument_key
    }
    if len(existing) == 1:
        return sorted(existing)
    if len(available) == 1:
        return sorted(available)
    if not available:
        return ["default"]
    return []


def _scope_candidates_for_cash(
    states: dict[tuple[int, str, str], _CashState],
    anchors: dict[tuple[int, str, str], _ScopeAnchor],
    *,
    account_id: int,
    currency: str,
) -> list[str]:
    available = {
        scope_key
        for account, state_currency, scope_key in anchors
        if account == account_id and state_currency == currency
    }
    # Cash movements are likewise unscoped.  Never fan one amount out across
    # multiple complete cash scopes.
    if len(available) > 1:
        return []
    existing = {
        scope_key
        for account, state_currency, scope_key in states
        if account == account_id and state_currency == currency
    }
    if len(existing) == 1:
        return sorted(existing)
    if len(available) == 1:
        return sorted(available)
    if not available:
        return ["default"]
    return []


def _state_floor(state: _SecurityState | _CashState) -> str | None:
    if state.anchor is not None:
        return state.anchor.as_of_date
    return state.initial_date


def _warn_for_incomplete_scope(
    state: _SecurityState | _CashState,
    latest_observed: dict[tuple[int, str, str], dict],
    *,
    section: str,
) -> None:
    observed = latest_observed.get((state.account_id, state.currency, state.scope_key))
    if observed is None or observed["completeness"] == "complete":
        return
    if state.anchor is None or _scope_rank(observed) >= (
        state.anchor.as_of_date,
        state.anchor.statement_id,
        state.anchor.snapshot_set_id,
    ):
        state.warnings.add(f"incomplete_{section}_scope_after_checkpoint")
        state.incomplete = True


def _result_quality(
    result: dict | None,
    warnings: set[str],
) -> tuple[str | None, str | None, bool]:
    if result is None:
        warnings.add("reconciliation_unavailable")
        return None, None, False
    status = str(result["status"])
    reason = result["reason"]
    if status != "reconciled":
        warnings.add(f"reconciliation_{status}")
    return status, str(reason) if reason is not None else None, status in {
        "unexplained_residual",
        "incomplete_input",
        "ambiguous_transfer",
    }


def _load_market_prices(
    symbols: set[str],
    *,
    as_of: str,
    market_path: Path | str | None,
) -> dict[str, tuple[float, str]]:
    if not symbols:
        return {}
    path = market_path if market_path is not None else DUCKDB_PATH
    try:
        con = duckdb.connect(str(path), read_only=True)
        try:
            placeholders = ",".join("?" * len(symbols))
            rows = con.execute(
                f"""
                SELECT symbol, price, trade_date
                  FROM (
                    SELECT symbol, COALESCE(close, adj_close) AS price, trade_date,
                           ROW_NUMBER() OVER (
                               PARTITION BY symbol ORDER BY trade_date DESC
                           ) AS rn
                      FROM daily_prices
                     WHERE symbol IN ({placeholders})
                       AND trade_date <= ?
                       AND COALESCE(close, adj_close) IS NOT NULL
                  )
                 WHERE rn = 1
                """,
                [*sorted(symbols), as_of],
            ).fetchall()
        finally:
            con.close()
    except Exception:
        return {}
    return {str(symbol): (float(price), str(trade_date)) for symbol, price, trade_date in rows}


def _combine_security_states(states: list[_SecurityState]) -> _SecurityState:
    primary = max(
        states,
        key=lambda state: (
            state.anchor.as_of_date if state.anchor else state.initial_date or "",
            state.anchor.statement_id if state.anchor else 0,
            state.anchor.snapshot_set_id if state.anchor else 0,
        ),
    )
    if len(states) == 1:
        return primary
    combined = copy.copy(primary)
    combined.quantity = sum(state.quantity for state in states)
    combined.position_movement = any(state.position_movement for state in states)
    combined.cost_basis_stale = any(state.cost_basis_stale for state in states)
    combined.incomplete = True
    combined.warnings = {warning for state in states for warning in state.warnings}
    combined.warnings.add("duplicate_complete_position_scopes")
    return combined


def _combine_cash_states(states: list[_CashState]) -> _CashState:
    primary = max(
        states,
        key=lambda state: (
            state.anchor.as_of_date if state.anchor else state.initial_date or "",
            state.anchor.statement_id if state.anchor else 0,
            state.anchor.snapshot_set_id if state.anchor else 0,
        ),
    )
    if len(states) == 1:
        return primary
    combined = copy.copy(primary)
    combined.balance = sum(state.balance for state in states)
    combined.cash_movement = any(state.cash_movement for state in states)
    combined.incomplete = True
    combined.warnings = {warning for state in states for warning in state.warnings}
    combined.warnings.add("duplicate_complete_cash_scopes")
    return combined


def _security_record(
    state: _SecurityState,
    *,
    as_of: str,
    account: dict,
    reconciliation_results: dict[tuple[int, int], dict],
) -> dict:
    warnings = set(state.warnings)
    reconciliation_status: str | None = None
    reconciliation_reason: str | None = None
    reconciliation_incomplete = False
    if state.anchor is not None:
        reconciliation_status, reconciliation_reason, reconciliation_incomplete = _result_quality(
            reconciliation_results.get((
                state.anchor.snapshot_set_id,
                state.anchor_instrument_id or state.instrument_id,
            )),
            warnings,
        )
    else:
        warnings.add("missing_complete_position_checkpoint")
    is_reported = state.anchor is not None and state.anchor.as_of_date == as_of
    holding_state = "reported" if is_reported else "reconstructed"
    if state.incomplete or reconciliation_incomplete:
        holding_state = "incomplete"
    return {
        "as_of_date": as_of,
        "account_id": state.account_id,
        "account_number": account["account_number"],
        "nickname": account["nickname"],
        "institution_code": account["institution_code"],
        "institution_name": account["institution_name"],
        "instrument_key": state.instrument_key,
        "holding_key": (
            f"{state.account_id}|{state.lineage_key or state.instrument_key}|{state.currency}"
        ),
        "symbol": state.symbol,
        "ticker_symbols": list(state.ticker_symbols or (state.symbol,)),
        "market_symbol": state.pricing_symbol,
        "_pricing_symbol": state.pricing_symbol,
        "asset_type": state.asset_type,
        "currency": state.currency,
        "scope_key": state.scope_key,
        "option_expiry": state.option_expiry,
        "option_strike": state.option_strike,
        "option_type": state.option_type,
        "quantity": state.quantity,
        "source_ref": (
            {
                "statement_id": state.anchor.statement_id,
                "kind": "position",
                "id": state.source_snapshot_id,
                "checkpoint": not is_reported,
            }
            if state.anchor is not None and state.source_snapshot_id is not None
            else None
        ),
        "avg_cost": state.avg_cost if is_reported or not state.cost_basis_stale else None,
        "book_value": state.book_value if is_reported or not state.cost_basis_stale else None,
        "market_price": state.market_price if is_reported else None,
        "market_value": state.market_value if is_reported else None,
        "unrealized_pnl": state.unrealized_pnl if is_reported else None,
        "checkpoint_date": state.anchor.as_of_date if state.anchor else state.initial_date,
        "checkpoint_statement_id": state.anchor.statement_id if state.anchor else None,
        "checkpoint_snapshot_set_id": state.anchor.snapshot_set_id if state.anchor else None,
        "is_reported": is_reported,
        "is_reconstructed": not is_reported,
        "holding_state": holding_state,
        "reconciliation_status": reconciliation_status,
        "reconciliation_reason": reconciliation_reason,
        "price_date": state.anchor.as_of_date if is_reported and state.anchor else None,
        "price_status": "broker_reported" if is_reported else "unpriced",
        "quality_warnings": warnings,
        "_anchor_quantity": state.anchor_quantity,
        "_anchor_market_price": state.market_price,
        "_anchor_market_value": state.market_value,
    }


def _cash_record(
    state: _CashState,
    *,
    as_of: str,
    account: dict,
    reconciliation_results: dict[int, dict],
) -> dict:
    warnings = set(state.warnings)
    reconciliation_status: str | None = None
    reconciliation_reason: str | None = None
    reconciliation_incomplete = False
    if state.anchor is not None:
        reconciliation_status, reconciliation_reason, reconciliation_incomplete = _result_quality(
            reconciliation_results.get(state.anchor.snapshot_set_id),
            warnings,
        )
    else:
        warnings.add("missing_complete_cash_checkpoint")
    is_reported = state.anchor is not None and state.anchor.as_of_date == as_of
    holding_state = "reported" if is_reported else "reconstructed"
    if state.incomplete or reconciliation_incomplete:
        holding_state = "incomplete"
    instrument_key = f"cash|{state.currency}"
    return {
        "as_of_date": as_of,
        "account_id": state.account_id,
        "account_number": account["account_number"],
        "nickname": account["nickname"],
        "institution_code": account["institution_code"],
        "institution_name": account["institution_name"],
        "instrument_key": instrument_key,
        "holding_key": f"{state.account_id}|{instrument_key}|{state.currency}",
        "symbol": f"{state.currency} Cash",
        "market_symbol": None,
        "asset_type": "cash",
        "currency": state.currency,
        "scope_key": state.scope_key,
        "option_expiry": None,
        "option_strike": None,
        "option_type": None,
        "quantity": state.balance,
        "source_ref": (
            {
                "statement_id": state.anchor.statement_id,
                "kind": "cash",
                "id": state.source_cash_balance_id,
                "checkpoint": not is_reported,
            }
            if state.anchor is not None and state.source_cash_balance_id is not None
            else None
        ),
        "avg_cost": None,
        "book_value": state.balance,
        "market_price": 1.0,
        "market_value": state.balance,
        "unrealized_pnl": None,
        "checkpoint_date": state.anchor.as_of_date if state.anchor else state.initial_date,
        "checkpoint_statement_id": state.anchor.statement_id if state.anchor else None,
        "checkpoint_snapshot_set_id": state.anchor.snapshot_set_id if state.anchor else None,
        "is_reported": is_reported,
        "is_reconstructed": not is_reported,
        "holding_state": holding_state,
        "reconciliation_status": reconciliation_status,
        "reconciliation_reason": reconciliation_reason,
        "price_date": as_of,
        "price_status": "native_cash",
        "quality_warnings": warnings,
    }


def _apply_security_prices(
    records: list[dict],
    *,
    as_of: str,
    market_path: Path | str | None,
) -> None:
    market_symbols = {
        str(record["_pricing_symbol"])
        for record in records
        if not record["is_reported"]
        and record["asset_type"] not in {"cash", "option"}
    }
    prices = _load_market_prices(market_symbols, as_of=as_of, market_path=market_path)
    for record in records:
        if record["asset_type"] == "cash" or record["is_reported"]:
            continue
        warnings: set[str] = record["quality_warnings"]
        price = (
            None
            if record["asset_type"] == "option"
            else prices.get(str(record["_pricing_symbol"]))
        )
        if price is not None:
            market_price, price_date = price
            record["market_price"] = market_price
            record["market_value"] = market_price * float(record["quantity"])
            record["price_date"] = price_date
            record["price_status"] = "market"
            if price_date < as_of:
                warnings.add("market_price_stale")
            continue
        anchor_price = record["_anchor_market_price"]
        if anchor_price is not None:
            record["market_price"] = anchor_price
            record["market_value"] = float(anchor_price) * float(record["quantity"])
            record["price_date"] = record["checkpoint_date"]
            record["price_status"] = "stale_checkpoint"
            warnings.add("stale_checkpoint_price")
            continue
        anchor_value = record["_anchor_market_value"]
        anchor_quantity = record["_anchor_quantity"]
        if (
            anchor_value is not None
            and anchor_quantity is not None
            and abs(float(anchor_quantity) - float(record["quantity"])) <= EPSILON
        ):
            record["market_value"] = anchor_value
            record["price_date"] = record["checkpoint_date"]
            record["price_status"] = "stale_checkpoint"
            warnings.add("stale_checkpoint_value")
            continue
        record["price_status"] = "unpriced"
        warnings.add("unpriced")


def _finalize_records(records: list[dict]) -> list[dict]:
    for record in records:
        record["quality_warnings"] = sorted(record["quality_warnings"])
        record.pop("_anchor_quantity", None)
        record.pop("_anchor_market_price", None)
        record.pop("_anchor_market_value", None)
        record.pop("_pricing_symbol", None)
    return sorted(
        records,
        key=lambda record: (
            str(record["institution_name"]),
            str(record["account_number"]),
            str(record["symbol"]),
            str(record["currency"]),
            str(record["instrument_key"]),
        ),
    )


def holdings_at(
    as_of: str,
    account_ids: list[int] | None = None,
    *,
    path: Path | str | None = None,
    market_path: Path | str | None = None,
) -> list[dict]:
    """Return one native-currency holding row per account/instrument/currency.

    The function is read-only.  It makes a complete scoped snapshot the anchor,
    applies only later normalized movements, and returns quality/provenance
    metadata rather than manufacturing an adjustment for uncertainty.
    """
    selected_accounts = list(account_ids or [])
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    with sqlite_db.session(db_path) as conn:
        accounts = _fetch_accounts(conn, selected_accounts)
        if not accounts:
            return []
        allowed_accounts = sorted(accounts)
        position_scope_rows = _fetch_scope_rows(
            conn,
            section_type="positions",
            as_of=as_of,
            account_ids=allowed_accounts,
        )
        cash_scope_rows = _fetch_scope_rows(
            conn,
            section_type="cash",
            as_of=as_of,
            account_ids=allowed_accounts,
        )
        position_scope_data = _latest_scopes(position_scope_rows, complete_only=True)
        cash_scope_data = _latest_scopes(cash_scope_rows, complete_only=True)
        latest_position_scope = _latest_scopes(position_scope_rows, complete_only=False)
        latest_cash_scope = _latest_scopes(cash_scope_rows, complete_only=False)
        position_anchors = {
            key: _anchor_from_row(row) for key, row in position_scope_data.items()
        }
        cash_anchors = {key: _anchor_from_row(row) for key, row in cash_scope_data.items()}
        position_rows = _fetch_position_rows(
            conn,
            [anchor.snapshot_set_id for anchor in position_anchors.values()],
        )
        cash_rows = _fetch_cash_rows(
            conn,
            [anchor.snapshot_set_id for anchor in cash_anchors.values()],
        )
        initial_positions = _fetch_initial_positions(
            conn,
            as_of=as_of,
            account_ids=allowed_accounts,
        )
        initial_cash = _fetch_initial_cash(
            conn,
            as_of=as_of,
            account_ids=allowed_accounts,
        )
        transactions = _fetch_transactions(
            conn,
            as_of=as_of,
            account_ids=allowed_accounts,
        )
        ticker_lineages = _fetch_ticker_lineages(conn)
        position_results, cash_results = _fetch_reconciliation_results(
            conn,
            position_set_ids=[anchor.snapshot_set_id for anchor in position_anchors.values()],
            cash_set_ids=[anchor.snapshot_set_id for anchor in cash_anchors.values()],
        )

    position_states: dict[tuple[int, str, str, str], _SecurityState] = {}
    for row in position_rows:
        scope_key = next(
            key
            for key, anchor in position_anchors.items()
            if anchor.snapshot_set_id == int(row["snapshot_set_id"])
        )
        anchor = position_anchors[scope_key]
        state_key = (
            int(row["account_id"]),
            str(row["currency"]),
            anchor.scope_key,
            str(row["instrument_key"]),
        )
        position_states[state_key] = _SecurityState(
            account_id=int(row["account_id"]),
            currency=str(row["currency"]),
            scope_key=anchor.scope_key,
            instrument_id=int(row["instrument_id"]),
            instrument_key=str(row["instrument_key"]),
            symbol=str(row["symbol"]),
            pricing_symbol=str(row["pricing_symbol"]),
            asset_type=str(row["asset_type"]),
            option_expiry=row["option_expiry"],
            option_strike=row["option_strike"],
            option_type=row["option_type"],
            quantity=float(row["quantity"]),
            source_snapshot_id=int(row["snapshot_id"]),
            anchor=anchor,
            anchor_quantity=float(row["quantity"]),
            avg_cost=row["avg_cost"],
            book_value=row["book_value"],
            market_price=row["market_price"],
            market_value=row["market_value"],
            unrealized_pnl=row["unrealized_pnl"],
            lineage_key=ticker_lineages.get(int(row["instrument_id"]), (None, ()))[0],
            ticker_symbols=ticker_lineages.get(int(row["instrument_id"]), ("", ()))[1],
            anchor_instrument_id=int(row["instrument_id"]),
        )

    for (account_id, currency, instrument_key), row in initial_positions.items():
        state_key = (account_id, currency, "default", instrument_key)
        if (account_id, currency, "default") in position_anchors:
            continue
        position_states.setdefault(
            state_key,
            _SecurityState(
                account_id=account_id,
                currency=currency,
                scope_key="default",
                instrument_id=int(row["instrument_id"]),
                instrument_key=instrument_key,
                symbol=str(row["symbol"]),
                pricing_symbol=str(row["pricing_symbol"]),
                asset_type=str(row["asset_type"]),
                option_expiry=row["option_expiry"],
                option_strike=row["option_strike"],
                option_type=row["option_type"],
                quantity=float(row["quantity"]),
                initial_date=str(row["as_of_date"]),
                anchor_quantity=float(row["quantity"]),
                avg_cost=row["avg_cost"],
                lineage_key=ticker_lineages.get(int(row["instrument_id"]), (None, ()))[0],
                ticker_symbols=ticker_lineages.get(int(row["instrument_id"]), ("", ()))[1],
                anchor_instrument_id=int(row["instrument_id"]),
            ),
        )

    cash_states: dict[tuple[int, str, str], _CashState] = {}
    cash_rows_by_set = {int(row["snapshot_set_id"]): row for row in cash_rows}
    for scope_key, anchor in cash_anchors.items():
        row = cash_rows_by_set.get(anchor.snapshot_set_id)
        if row is None:
            continue
        cash_states[scope_key] = _CashState(
            account_id=anchor.account_id,
            currency=anchor.currency,
            scope_key=anchor.scope_key,
            balance=float(row["closing_balance"]),
            source_cash_balance_id=int(row["cash_balance_id"]),
            anchor=anchor,
        )
    for (account_id, currency), row in initial_cash.items():
        state_key = (account_id, currency, "default")
        if state_key in cash_anchors:
            continue
        cash_states.setdefault(
            state_key,
            _CashState(
                account_id=account_id,
                currency=currency,
                scope_key="default",
                balance=float(row["balance"]),
                initial_date=str(row["as_of_date"]),
            ),
        )

    for row in transactions:
        account_id = int(row["account_id"])
        if row["successor_instrument_id"] is not None and row["trade_date"] <= as_of:
            currency = str(row["instrument_currency"] or row["currency"])
            instrument_key = str(row["instrument_key"])
            scope_keys = _scope_candidates_for_security(
                position_states,
                position_anchors,
                account_id=account_id,
                currency=currency,
                instrument_key=instrument_key,
            )
            for scope_key in scope_keys:
                old_key = (account_id, currency, scope_key, instrument_key)
                old_state = position_states.get(old_key)
                if old_state is None:
                    continue
                floor = _state_floor(old_state)
                if floor is not None and str(row["trade_date"]) <= floor:
                    continue
                moved = old_state.quantity
                old_state.quantity = 0.0
                old_state.position_movement = True
                successor_key = str(row["successor_instrument_key"])
                new_key = (account_id, currency, scope_key, successor_key)
                new_state = position_states.get(new_key)
                if new_state is None:
                    successor_id = int(row["successor_instrument_id"])
                    lineage = ticker_lineages.get(successor_id, (old_state.lineage_key, old_state.ticker_symbols))
                    new_state = _SecurityState(
                        account_id=account_id,
                        currency=currency,
                        scope_key=scope_key,
                        instrument_id=successor_id,
                        instrument_key=successor_key,
                        symbol=str(row["successor_symbol"]),
                        pricing_symbol=str(row["successor_pricing_symbol"]),
                        asset_type=str(row["successor_asset_type"]),
                        option_expiry=row["successor_option_expiry"],
                        option_strike=row["successor_option_strike"],
                        option_type=row["successor_option_type"],
                        quantity=0.0,
                        source_snapshot_id=old_state.source_snapshot_id,
                        anchor=old_state.anchor,
                        initial_date=old_state.initial_date,
                        anchor_quantity=old_state.anchor_quantity,
                        avg_cost=(
                            old_state.avg_cost / float(row["ticker_change_ratio"])
                            if old_state.avg_cost is not None
                            else None
                        ),
                        book_value=old_state.book_value,
                        lineage_key=lineage[0],
                        ticker_symbols=lineage[1],
                        anchor_instrument_id=(
                            old_state.anchor_instrument_id or old_state.instrument_id
                        ),
                    )
                    position_states[new_key] = new_state
                new_state.quantity += moved * float(row["ticker_change_ratio"])
                new_state.position_movement = True
            # The source transaction is represented by the old -> new move;
            # do not also treat generic name_change as a missing delta.
            continue
        if row["instrument_id"] is not None and row["trade_date"] <= as_of:
            currency = str(row["instrument_currency"] or row["currency"])
            instrument_key = str(row["instrument_key"])
            scope_keys = _scope_candidates_for_security(
                position_states,
                position_anchors,
                account_id=account_id,
                currency=currency,
                instrument_key=instrument_key,
            )
            if not scope_keys:
                for state in position_states.values():
                    if state.account_id == account_id and state.currency == currency:
                        state.warnings.add("ambiguous_position_scope_transaction")
                        state.incomplete = True
            else:
                for scope_key in scope_keys:
                    state_key = (account_id, currency, scope_key, instrument_key)
                    state = position_states.get(state_key)
                    if state is None:
                        anchor = position_anchors.get((account_id, currency, scope_key))
                        if anchor is not None and str(row["trade_date"]) <= anchor.as_of_date:
                            continue
                        initial = (
                            None
                            if anchor is not None
                            else initial_positions.get((account_id, currency, instrument_key))
                        )
                        state = _SecurityState(
                            account_id=account_id,
                            currency=currency,
                            scope_key=scope_key,
                            instrument_id=int(row["instrument_id"]),
                            instrument_key=instrument_key,
                            symbol=str(row["symbol"]),
                            pricing_symbol=str(row["pricing_symbol"]),
                            asset_type=str(row["asset_type"]),
                            option_expiry=row["option_expiry"],
                            option_strike=row["option_strike"],
                            option_type=row["option_type"],
                            quantity=float(initial["quantity"]) if initial else 0.0,
                            anchor=anchor,
                            initial_date=str(initial["as_of_date"]) if initial else None,
                            anchor_quantity=float(initial["quantity"]) if initial else 0.0,
                            avg_cost=initial["avg_cost"] if initial else None,
                            lineage_key=ticker_lineages.get(
                                int(row["instrument_id"]), (None, ())
                            )[0],
                            ticker_symbols=ticker_lineages.get(
                                int(row["instrument_id"]), ("", ())
                            )[1],
                            anchor_instrument_id=int(row["instrument_id"]),
                        )
                        position_states[state_key] = state
                    floor = _state_floor(state)
                    if floor is not None and str(row["trade_date"]) <= floor:
                        continue
                    effect = contextual_position_delta(
                        str(row["txn_type"]),
                        row["quantity"],
                        state.quantity,
                        _stored_position_effect(row),
                    )
                    if effect is None:
                        state.warnings.add("missing_position_delta")
                        state.incomplete = True
                        continue
                    if abs(effect) > EPSILON:
                        state.quantity += effect
                        state.position_movement = True
                        state.cost_basis_stale = True

        if row["instrument_id"] is None and row["txn_type"] in POSITION_AFFECTING_TYPES:
            transaction_currency = str(row["currency"])
            for state in position_states.values():
                floor = _state_floor(state)
                if (
                    state.account_id == account_id
                    and state.currency == transaction_currency
                    and (floor is None or str(row["trade_date"]) > floor)
                ):
                    state.warnings.add("unresolved_position_transaction")
                    state.incomplete = True

        effective_date = str(row["cash_effective_date"] or row["trade_date"])
        if effective_date > as_of or row["txn_type"] in NON_CASH_TXN_TYPES:
            continue
        currency = str(row["currency"])
        scope_keys = _scope_candidates_for_cash(
            cash_states,
            cash_anchors,
            account_id=account_id,
            currency=currency,
        )
        if not scope_keys:
            for state in cash_states.values():
                if state.account_id == account_id and state.currency == currency:
                    state.warnings.add("ambiguous_cash_scope_transaction")
                    state.incomplete = True
            continue
        value = row["cash_delta"] if row["cash_delta"] is not None else row["net_amount"]
        for scope_key in scope_keys:
            state_key = (account_id, currency, scope_key)
            state = cash_states.get(state_key)
            if state is None:
                anchor = cash_anchors.get(state_key)
                if anchor is not None and effective_date <= anchor.as_of_date:
                    continue
                initial = None if anchor is not None else initial_cash.get((account_id, currency))
                state = _CashState(
                    account_id=account_id,
                    currency=currency,
                    scope_key=scope_key,
                    balance=float(initial["balance"]) if initial else 0.0,
                    anchor=anchor,
                    initial_date=str(initial["as_of_date"]) if initial else None,
                )
                cash_states[state_key] = state
            floor = _state_floor(state)
            if floor is not None and effective_date <= floor:
                continue
            if value is None:
                state.warnings.add("missing_cash_delta")
                state.incomplete = True
                continue
            delta = float(value)
            if abs(delta) > EPSILON:
                state.balance += delta
                state.cash_movement = True

    for state in position_states.values():
        _warn_for_incomplete_scope(state, latest_position_scope, section="position")
    for state in cash_states.values():
        _warn_for_incomplete_scope(state, latest_cash_scope, section="cash")

    grouped_positions: dict[tuple[int, str, str], list[_SecurityState]] = defaultdict(list)
    for state in position_states.values():
        if abs(state.quantity) > EPSILON:
            grouped_positions[(
                state.account_id,
                state.lineage_key or state.instrument_key,
                state.currency,
            )].append(state)
    records = [
        _security_record(
            _combine_security_states(states),
            as_of=as_of,
            account=accounts[key[0]],
            reconciliation_results=position_results,
        )
        for key, states in grouped_positions.items()
    ]
    grouped_cash: dict[tuple[int, str], list[_CashState]] = defaultdict(list)
    for state in cash_states.values():
        if abs(state.balance) > EPSILON:
            grouped_cash[(state.account_id, state.currency)].append(state)
    records.extend(
        _cash_record(
            _combine_cash_states(states),
            as_of=as_of,
            account=accounts[key[0]],
            reconciliation_results=cash_results,
        )
        for key, states in grouped_cash.items()
    )
    _apply_security_prices(records, as_of=as_of, market_path=market_path)
    return _finalize_records(records)


def holding_dates(
    account_ids: list[int] | None = None,
    *,
    path: Path | str | None = None,
    as_of: str | None = None,
) -> list[str]:
    """Return dates on which a complete cash or position checkpoint exists."""
    selected_accounts = list(account_ids or [])
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    with sqlite_db.session(db_path) as conn:
        account_sql, account_params = _account_clause("ss.account_id", selected_accounts)
        date_sql = " AND ss.as_of_date <= ?" if as_of else ""
        params: list = [*account_params]
        if as_of:
            params.append(as_of)
        canonical_sql = canonical_statement_clause("ss.statement_id")
        rows = conn.execute(
            f"""
            SELECT DISTINCT ss.as_of_date
              FROM snapshot_sets ss
             WHERE ss.section_type IN ('positions', 'cash')
               AND ss.can_clear_omitted = 1
               AND {canonical_sql}
               {account_sql}
               {date_sql}
             ORDER BY ss.as_of_date
            """,
            params,
        ).fetchall()
    return [str(row["as_of_date"]) for row in rows]


def latest_holdings_date(
    as_of: str | None = None,
    account_ids: list[int] | None = None,
    *,
    path: Path | str | None = None,
) -> str | None:
    """Return the latest complete holdings checkpoint on or before ``as_of``."""
    dates = holding_dates(account_ids, path=path, as_of=as_of)
    return dates[-1] if dates else None
