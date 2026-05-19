"""GET /asset-values — per-account positions with stocks and options."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Depends

from trade_history.analytics.positions import get_open_positions
from trade_history.api.deps import get_sqlite

router = APIRouter()


@router.get("")
def asset_values(
    conn: Annotated[sqlite3.Connection, Depends(get_sqlite)],
    group_by: Literal["account", "institution"] = "account",
    currency: Literal["CAD", "USD"] = "CAD",
    as_of_date: str | None = None,
):
    """
    Return open positions grouped by account.
    Each group has a 'Stocks' and 'Options' section.
    group_key = '{institution} | {account_id}'
    """
    positions = get_open_positions(conn, as_of_date=as_of_date)

    groups: dict[str, dict] = {}
    for pos in positions:
        if group_by == "account":
            key = f"{pos.institution} | {pos.account_id}"
        else:
            key = pos.institution

        if key not in groups:
            groups[key] = {
                "group_key": key,
                "institution": pos.institution,
                "stocks": [],
                "options": [],
                "total_market_value": 0.0,
            }

        item = {
            "symbol": pos.symbol,
            "asset_type": pos.asset_type,
            "quantity": float(pos.quantity),
            "avg_cost": float(pos.avg_cost),
            "currency": pos.currency,
            "direction": pos.direction,
            "market_price": pos.market_price,
            "market_value": pos.market_value,
        }

        if pos.asset_type == "option":
            item["option_root"] = pos.option_root
            item["expiry"] = pos.expiry
            item["put_call"] = pos.put_call
            item["strike"] = pos.strike
            groups[key]["options"].append(item)
        else:
            groups[key]["stocks"].append(item)

        if pos.market_value:
            groups[key]["total_market_value"] += pos.market_value

    return list(groups.values())
