"""GET /performance — total asset value over time.

The raw ``position_snapshots`` are per-account, per-period checkpoints.
Different accounts have different statement dates, so naive summing
produces a saw-tooth (zig-zag) line because not every account contributes
on every date.

This endpoint forward-fills each (account, instrument) snapshot up to the
next statement and across to today, then sums per-currency at each
*observed* date. The result is a monotonic step series instead of jaggy.
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
) -> dict:
    sql = [
        "SELECT ps.as_of_date, ps.account_id, ps.instrument_id,",
        "       ps.market_value, ps.currency",
        "  FROM position_snapshots ps",
        "  JOIN accounts a ON a.account_id = ps.account_id",
        "  JOIN institutions i ON i.institution_id = a.institution_id",
        "  JOIN instruments inst ON inst.instrument_id = ps.instrument_id",
        " WHERE ps.quantity != 0",
    ]
    params: list = []
    insts = _csv_list(institution)
    if insts:
        sql.append(f" AND i.code IN ({','.join('?' * len(insts))})")
        params.extend(insts)
    accts = _csv_ints(account_id)
    if accts:
        sql.append(f" AND a.account_id IN ({','.join('?' * len(accts))})")
        params.extend(accts)
    syms = [s.upper() for s in _csv_list(symbol)]
    if syms:
        sql.append(f" AND inst.symbol IN ({','.join('?' * len(syms))})")
        params.extend(syms)
    if asset_type:
        sql.append(" AND inst.asset_type = ?")
        params.append(asset_type)
    sql.append(" ORDER BY ps.as_of_date")
    with sqlite_db.session() as conn:
        raw = [dict(r) for r in conn.execute("\n".join(sql), params).fetchall()]
    if not raw:
        return {"rows": []}

    if not forward_fill:
        # Simple per-date sum (will zig-zag).
        out: dict[tuple[str, str], float] = {}
        for r in raw:
            key = (r["as_of_date"], r["currency"])
            out[key] = out.get(key, 0.0) + (r["market_value"] or 0.0)
        rows = [{"as_of_date": d, "currency": c, "market_value": v}
                for (d, c), v in sorted(out.items())]
        return {"rows": rows}

    # Build a sparse table: latest known market_value per (account, instrument, currency)
    # across the observed dates, then sum.
    dates = sorted({r["as_of_date"] for r in raw})
    # Add today so the latest holdings extend to "now"
    today = date.today().isoformat()
    if dates[-1] < today:
        dates.append(today)
    # State: (acct, instr) -> (currency, market_value)
    state: dict[tuple[int, int], tuple[str, float]] = {}
    # By-date events
    by_date: dict[str, list[dict]] = {}
    for r in raw:
        by_date.setdefault(r["as_of_date"], []).append(r)
    rows: list[dict] = []
    for d in dates:
        for r in by_date.get(d, []):
            state[(r["account_id"], r["instrument_id"])] = (r["currency"], r["market_value"] or 0.0)
        # Aggregate
        per_ccy: dict[str, float] = {}
        for ccy, mv in state.values():
            per_ccy[ccy] = per_ccy.get(ccy, 0.0) + mv
        for ccy, mv in per_ccy.items():
            rows.append({"as_of_date": d, "currency": ccy, "market_value": mv})
    return {"rows": rows}


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
