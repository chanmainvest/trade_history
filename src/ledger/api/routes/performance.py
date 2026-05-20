"""GET /performance — portfolio value over time.

The raw ``position_snapshots`` are per-account, per-period checkpoints.
Different accounts have different statement dates, so naive summing
produces a saw-tooth (zig-zag) line because not every account contributes
on every date.

This endpoint forward-fills each account's securities and cash checkpoints
up to the next statement and across to today, then sums per-currency at each
observed date. Later account checkpoints clear securities that disappeared
from the broker snapshot, so sold-out positions do not remain forever.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query

from ...db import sqlite as sqlite_db

router = APIRouter(prefix="/performance", tags=["performance"])


def _csv_list(v: str | None) -> list[str]:
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


def _csv_ints(v: str | None) -> list[int]:
    return [int(x) for x in _csv_list(v) if x.lstrip("-").isdigit()]


@router.get("/total")
def total(
    institution: str | None = Query(None),
    account_id: str | None = Query(None),
    symbol: str | None = Query(None),
    asset_type: str | None = Query(None),
    forward_fill: bool = Query(True, description="Carry last snapshot forward"),
    include_cash: bool = Query(True, description="Include cash balances when no security filter is active"),
) -> dict:
    return {"rows": _total_rows(
        institution=institution,
        account_id=account_id,
        symbol=symbol,
        asset_type=asset_type,
        forward_fill=forward_fill,
        include_cash=include_cash,
    )}


def _account_filters(institution: str | None, account_id: str | None) -> tuple[list[str], list]:
    sql: list[str] = []
    params: list = []
    insts = _csv_list(institution)
    if insts:
        sql.append(f" AND i.code IN ({','.join('?' * len(insts))})")
        params.extend(insts)
    accts = _csv_ints(account_id)
    if accts:
        sql.append(f" AND a.account_id IN ({','.join('?' * len(accts))})")
        params.extend(accts)
    return sql, params


def _total_rows(
    *,
    institution: str | None = None,
    account_id: str | None = None,
    symbol: str | None = None,
    asset_type: str | None = None,
    forward_fill: bool = True,
    include_cash: bool = True,
    path=None,
) -> list[dict]:
    account_filter_sql, account_params = _account_filters(institution, account_id)
    syms = [s.upper() for s in _csv_list(symbol)]

    checkpoint_sql = [
        "SELECT DISTINCT ps.as_of_date, ps.account_id",
        "  FROM position_snapshots ps",
        "  JOIN accounts a ON a.account_id = ps.account_id",
        "  JOIN institutions i ON i.institution_id = a.institution_id",
        " WHERE 1=1",
        *account_filter_sql,
        " ORDER BY ps.as_of_date",
    ]

    sql = [
        "SELECT ps.as_of_date, ps.account_id, ps.instrument_id,",
        "       ps.quantity, ps.market_value, ps.currency",
        "  FROM position_snapshots ps",
        "  JOIN accounts a ON a.account_id = ps.account_id",
        "  JOIN institutions i ON i.institution_id = a.institution_id",
        "  JOIN instruments inst ON inst.instrument_id = ps.instrument_id",
        " WHERE 1=1",
        *account_filter_sql,
    ]
    params: list = list(account_params)
    if syms:
        sql.append(f" AND inst.symbol IN ({','.join('?' * len(syms))})")
        params.extend(syms)
    if asset_type:
        sql.append(" AND inst.asset_type = ?")
        params.append(asset_type)
    sql.append(" ORDER BY ps.as_of_date")
    cash_allowed = include_cash and not syms and not asset_type
    cash_sql = [
        "SELECT cb.as_of_date, cb.account_id, cb.currency, cb.closing_balance",
        "  FROM cash_balances cb",
        "  JOIN accounts a ON a.account_id = cb.account_id",
        "  JOIN institutions i ON i.institution_id = a.institution_id",
        " WHERE 1=1",
        *account_filter_sql,
        " ORDER BY cb.as_of_date",
    ]

    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    with sqlite_db.session(db_path) as conn:
        checkpoint_rows = [dict(r) for r in conn.execute("\n".join(checkpoint_sql), account_params).fetchall()]
        raw = [dict(r) for r in conn.execute("\n".join(sql), params).fetchall()]
        raw_cash = [dict(r) for r in conn.execute("\n".join(cash_sql), account_params).fetchall()] if cash_allowed else []
    if not raw and not raw_cash:
        return []

    if not forward_fill:
        # Simple per-date sum (will zig-zag).
        out: dict[tuple[str, str], float] = {}
        for r in raw:
            if abs(r["quantity"] or 0.0) <= 1e-9:
                continue
            key = (r["as_of_date"], r["currency"])
            out[key] = out.get(key, 0.0) + (r["market_value"] or 0.0)
        for r in raw_cash:
            key = (r["as_of_date"], r["currency"])
            out[key] = out.get(key, 0.0) + (r["closing_balance"] or 0.0)
        rows = [{"as_of_date": d, "currency": c, "market_value": v}
                for (d, c), v in sorted(out.items())]
        return rows

    # Broker holdings snapshots are complete account checkpoints. When an
    # instrument disappears from a later statement, clear that account's prior
    # securities state before applying the new snapshot rows.
    dates = sorted(
        {r["as_of_date"] for r in checkpoint_rows}
        | {r["as_of_date"] for r in raw}
        | {r["as_of_date"] for r in raw_cash}
    )
    # Add today so the latest holdings extend to "now"
    today = date.today().isoformat()
    if dates[-1] < today:
        dates.append(today)
    # Position state: (acct, instr) -> (currency, market_value)
    position_state: dict[tuple[int, int], tuple[str, float]] = {}
    cash_state: dict[tuple[int, str], float] = {}
    known_currencies = {r["currency"] for r in raw} | {r["currency"] for r in raw_cash}
    # By-date events
    by_date: dict[str, list[dict]] = {}
    for r in raw:
        by_date.setdefault(r["as_of_date"], []).append(r)
    cash_by_date: dict[str, list[dict]] = {}
    for r in raw_cash:
        cash_by_date.setdefault(r["as_of_date"], []).append(r)
    checkpoint_accounts_by_date: dict[str, set[int]] = {}
    for r in checkpoint_rows:
        checkpoint_accounts_by_date.setdefault(r["as_of_date"], set()).add(r["account_id"])
    rows: list[dict] = []
    for d in dates:
        for acct in checkpoint_accounts_by_date.get(d, set()):
            for key in [key for key in position_state if key[0] == acct]:
                del position_state[key]
        for r in by_date.get(d, []):
            if abs(r["quantity"] or 0.0) > 1e-9:
                position_state[(r["account_id"], r["instrument_id"])] = (
                    r["currency"], r["market_value"] or 0.0,
                )
        for r in cash_by_date.get(d, []):
            cash_state[(r["account_id"], r["currency"])] = r["closing_balance"] or 0.0
        # Aggregate
        per_ccy: dict[str, float] = {}
        for ccy, mv in position_state.values():
            per_ccy[ccy] = per_ccy.get(ccy, 0.0) + mv
        for (_, ccy), balance in cash_state.items():
            per_ccy[ccy] = per_ccy.get(ccy, 0.0) + balance
        for ccy in sorted(known_currencies | set(per_ccy)):
            mv = per_ccy.get(ccy, 0.0)
            rows.append({"as_of_date": d, "currency": ccy, "market_value": mv})
    return rows


@router.get("/cash")
def cash(
    account_id: str | None = Query(None),
    institution: str | None = Query(None),
) -> dict:
    sql = [
        "SELECT cb.as_of_date, cb.account_id, cb.currency, cb.closing_balance ",
        "  FROM cash_balances cb",
        "  JOIN accounts a ON a.account_id = cb.account_id",
        "  JOIN institutions i ON i.institution_id = a.institution_id",
        " WHERE 1=1",
    ]
    params: list = []
    accts = _csv_ints(account_id)
    if accts:
        sql.append(f" AND a.account_id IN ({','.join('?' * len(accts))})")
        params.extend(accts)
    insts = _csv_list(institution)
    if insts:
        sql.append(f" AND i.code IN ({','.join('?' * len(insts))})")
        params.extend(insts)
    sql.append(" ORDER BY cb.as_of_date")
    with sqlite_db.session() as conn:
        rows = [dict(r) for r in conn.execute("\n".join(sql), params).fetchall()]
    return {"rows": rows}
