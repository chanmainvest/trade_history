"""GET /monthly-balances — historical monthly portfolio snapshots."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from trade_history.api.deps import get_sqlite

router = APIRouter()


@router.get("/months")
def list_months(
    conn: Annotated[sqlite3.Connection, Depends(get_sqlite)],
):
    """Return all available year_month values, newest first."""
    rows = conn.execute(
        "SELECT DISTINCT year_month FROM monthly_balances ORDER BY year_month DESC"
    ).fetchall()
    return [row["year_month"] for row in rows]


@router.get("")
def monthly_balances(
    conn: Annotated[sqlite3.Connection, Depends(get_sqlite)],
    year_month: Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")],
    group_by: Literal["account", "institution"] = "account",
    currency: Literal["CAD", "USD"] = "CAD",
):
    """Return positions for a specific month, same format as /asset-values."""
    rows = conn.execute(
        """
        SELECT
            mb.account_id AS acct_pk,
            a.institution,
            a.account_id,
            a.account_type,
            mb.instrument_id,
            i.symbol,
            i.asset_type,
            i.option_root,
            i.expiry,
            i.put_call,
            i.strike,
            mb.quantity,
            mb.avg_cost,
            mb.market_price,
            mb.market_value,
            mb.currency AS mb_currency,
            mb.as_of_date
        FROM monthly_balances mb
        JOIN accounts a ON a.id = mb.account_id
        JOIN instruments i ON i.id = mb.instrument_id
        WHERE mb.year_month = ?
        ORDER BY a.institution, a.account_id, i.symbol
        """,
        (year_month,),
    ).fetchall()

    groups: dict[str, dict] = {}
    for r in rows:
        if group_by == "account":
            key = f"{r['institution']} | {r['account_id']}"
        else:
            key = r["institution"]

        if key not in groups:
            groups[key] = {
                "group_key": key,
                "institution": r["institution"],
                "stocks": [],
                "options": [],
                "total_market_value": 0.0,
                "as_of_date": r["as_of_date"],
            }

        qty = r["quantity"] or 0
        direction = "long" if qty >= 0 else "short"

        item = {
            "symbol": r["symbol"],
            "asset_type": r["asset_type"],
            "quantity": abs(float(qty)),
            "avg_cost": float(r["avg_cost"]) if r["avg_cost"] else 0.0,
            "currency": r["mb_currency"],
            "direction": direction,
            "market_price": float(r["market_price"]) if r["market_price"] else None,
            "market_value": float(r["market_value"]) if r["market_value"] else None,
        }

        if r["asset_type"] == "option":
            item["option_root"] = r["option_root"]
            item["expiry"] = r["expiry"]
            item["put_call"] = r["put_call"]
            item["strike"] = float(r["strike"]) if r["strike"] else None
            groups[key]["options"].append(item)
        else:
            groups[key]["stocks"].append(item)

        if item["market_value"]:
            groups[key]["total_market_value"] += item["market_value"]

    return list(groups.values())
