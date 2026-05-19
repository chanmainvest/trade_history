"""Compute current open positions from transaction history."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class OpenPosition:
    account_id: int
    institution: str
    account_type: str
    instrument_id: int
    symbol: str
    asset_type: str
    quantity: Decimal
    avg_cost: Decimal
    currency: str
    direction: str = field(default="long")  # "long" or "short"
    market_price: float | None = None
    market_value: float | None = None
    # Option fields (None for equities)
    option_root: str | None = None
    expiry: str | None = None
    put_call: str | None = None
    strike: float | None = None


def get_open_positions(conn: sqlite3.Connection, as_of_date: str | None = None) -> list[OpenPosition]:
    """
    Compute open positions from transactions (ignoring transfer pairs).
    If as_of_date provided, only include transactions up to that date.
    Supports both long (qty > 0) and short (qty < 0) positions.
    """
    date_filter = f"AND t.trade_date <= '{as_of_date}'" if as_of_date else ""

    rows = conn.execute(
        f"""
        SELECT
            t.account_id, a.institution, a.account_type,
            t.instrument_id, i.symbol, i.asset_type,
            i.option_root, i.expiry, i.put_call, i.strike,
            t.activity, t.quantity, t.price, t.amount, t.currency, t.commission
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        JOIN instruments i ON i.id = t.instrument_id
        WHERE t.instrument_id IS NOT NULL
          AND t.activity IN ('bought', 'sold', 'exercise', 'assignment', 'expired',
                              'transfer_in', 'transfer_out')
          {date_filter}
          -- Exclude one side of matched transfer pairs (the transfer_out side)
          AND NOT EXISTS (
              SELECT 1 FROM transfer_pairs p WHERE p.from_transaction_id = t.id
          )
        ORDER BY t.account_id, t.instrument_id, t.trade_date
        """
    ).fetchall()

    # (account_id, instrument_id) → (qty, total_cost)
    holdings: dict[tuple[int, int], dict] = defaultdict(
        lambda: {"qty": Decimal(0), "total_cost": Decimal(0), "currency": "CAD"}
    )
    meta: dict[tuple[int, int], dict] = {}

    for row in rows:
        key = (row["account_id"], row["instrument_id"])
        qty = Decimal(str(row["quantity"] or 0))
        price = Decimal(str(row["price"] or 0))
        commission = Decimal(str(row["commission"] or 0))
        activity = row["activity"]

        meta[key] = {
            "institution": row["institution"],
            "account_type": row["account_type"],
            "symbol": row["symbol"],
            "asset_type": row["asset_type"],
            "currency": row["currency"],
            "option_root": row["option_root"],
            "expiry": row["expiry"],
            "put_call": row["put_call"],
            "strike": row["strike"],
        }

        if activity in ("bought", "transfer_in", "exercise"):
            holdings[key]["qty"] += qty
            holdings[key]["total_cost"] += price * qty + commission
        elif activity in ("sold", "transfer_out", "assignment", "expired"):
            holdings[key]["qty"] -= abs(qty)

    # Fetch the most recent market_price / market_value for every
    # (account_id, instrument_id) pair from the statement-based position_state.
    market_data: dict[tuple[int, int], dict] = {}
    ps_rows = conn.execute(
        """
        SELECT ps.account_id, ps.instrument_id,
               ps.market_price, ps.market_value, ps.market_currency
        FROM position_state ps
        INNER JOIN (
            SELECT account_id, instrument_id, MAX(as_of_date) AS max_date
            FROM position_state
            GROUP BY account_id, instrument_id
        ) latest ON ps.account_id = latest.account_id
               AND ps.instrument_id = latest.instrument_id
               AND ps.as_of_date = latest.max_date
        """
    ).fetchall()
    for r in ps_rows:
        market_data[(r["account_id"], r["instrument_id"])] = r

    positions = []
    for key, h in holdings.items():
        if abs(h["qty"]) <= Decimal("0.0001"):
            continue  # effectively zero / closed
        m = meta[key]
        qty = h["qty"]
        direction = "long" if qty > 0 else "short"
        avg_cost = abs(h["total_cost"] / qty) if qty else Decimal(0)

        md = market_data.get(key)
        # position_state stores market_price = total market value,
        # market_value = per-unit price (CIBC/HSBC/RBC extractor column order).
        # Swap them back for meaningful display.
        mkt_price: float | None = None
        mkt_value: float | None = None
        if md:
            raw_mp = md["market_price"]
            raw_mv = md["market_value"]
            if raw_mv is not None and raw_mv > 0:
                mkt_price = float(raw_mv)        # per-unit price
            if raw_mp is not None and raw_mp > 0:
                mkt_value = float(raw_mp)        # total market value

        positions.append(
            OpenPosition(
                account_id=key[0],
                institution=m["institution"],
                account_type=m["account_type"],
                instrument_id=key[1],
                symbol=m["symbol"],
                asset_type=m["asset_type"],
                quantity=abs(qty),
                avg_cost=avg_cost,
                currency=m["currency"],
                direction=direction,
                market_price=mkt_price,
                market_value=mkt_value,
                option_root=m["option_root"],
                expiry=m["expiry"],
                put_call=m["put_call"],
                strike=float(m["strike"]) if m["strike"] is not None else None,
            )
        )

    return positions
