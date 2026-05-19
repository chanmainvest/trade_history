"""FIFO P/L calculation for closed positions (long and short)."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class FifoLot:
    quantity: Decimal
    price: Decimal
    currency: str
    trade_date: str


@dataclass
class ShortLot:
    quantity: Decimal
    price: Decimal
    currency: str
    trade_date: str
    proceeds: Decimal  # total proceeds from the opening sell


@dataclass
class ClosedTrade:
    instrument_id: int
    symbol: str
    account_id: int
    open_date: str
    close_date: str
    quantity: Decimal
    cost_basis: Decimal
    proceeds: Decimal
    realized_pnl: Decimal
    currency: str
    direction: str = field(default="long")  # "long" or "short"


def compute_pnl(conn: sqlite3.Connection) -> list[ClosedTrade]:
    """
    Compute FIFO realized P/L for all accounts.
    Supports both long (buy-then-sell) and short (sell-then-buy) positions.
    """
    rows = conn.execute(
        """
        SELECT
            t.id, t.account_id, t.instrument_id, t.trade_date, t.activity,
            t.quantity, t.price, t.amount, t.currency, t.commission,
            i.symbol
        FROM transactions t
        JOIN instruments i ON i.id = t.instrument_id
        WHERE t.activity IN ('bought', 'sold', 'exercise', 'assignment', 'expired')
          AND t.instrument_id IS NOT NULL
        ORDER BY t.account_id, t.instrument_id, t.trade_date, t.id
        """
    ).fetchall()

    # Group by (account_id, instrument_id)
    groups: dict[tuple[int, int], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        groups[(row["account_id"], row["instrument_id"])].append(row)

    closed_trades: list[ClosedTrade] = []

    for (account_id, instrument_id), txs in groups.items():
        lots: list[FifoLot] = []
        short_lots: list[ShortLot] = []
        symbol = txs[0]["symbol"]

        for tx in txs:
            qty = Decimal(str(tx["quantity"] or 0))
            price = Decimal(str(tx["price"] or 0))
            commission = Decimal(str(tx["commission"] or 0))
            currency = tx["currency"]
            activity = tx["activity"]
            total_amount = abs(Decimal(str(tx["amount"] or 0)))

            if activity in ("bought", "exercise"):
                buy_qty = abs(qty)

                # First, close outstanding short lots FIFO
                while buy_qty > 0 and short_lots:
                    sl = short_lots[0]
                    if sl.quantity <= buy_qty:
                        used_qty = sl.quantity
                        cover_cost = price * used_qty
                        short_proceeds = sl.proceeds
                        short_lots.pop(0)
                    else:
                        used_qty = buy_qty
                        ratio = used_qty / sl.quantity
                        cover_cost = price * used_qty
                        short_proceeds = sl.proceeds * ratio
                        sl.quantity -= used_qty
                        sl.proceeds -= short_proceeds

                    closed_trades.append(
                        ClosedTrade(
                            instrument_id=instrument_id,
                            symbol=symbol,
                            account_id=account_id,
                            open_date=sl.trade_date,
                            close_date=tx["trade_date"],
                            quantity=used_qty,
                            cost_basis=cover_cost,
                            proceeds=short_proceeds,
                            realized_pnl=short_proceeds - cover_cost - commission * (used_qty / abs(qty)),
                            currency=currency,
                            direction="short",
                        )
                    )
                    buy_qty -= used_qty

                # Remaining buy qty becomes a new long lot
                if buy_qty > 0:
                    lots.append(FifoLot(buy_qty, price, currency, tx["trade_date"]))

            elif activity in ("sold", "assignment", "expired"):
                if activity == "expired":
                    # Expire all remaining long lots
                    long_qty = sum(lot.quantity for lot in lots)
                    if long_qty > 0:
                        qty = long_qty
                    elif short_lots:
                        # Expire short lots (worthless expiry = full profit)
                        for sl in short_lots:
                            closed_trades.append(
                                ClosedTrade(
                                    instrument_id=instrument_id,
                                    symbol=symbol,
                                    account_id=account_id,
                                    open_date=sl.trade_date,
                                    close_date=tx["trade_date"],
                                    quantity=sl.quantity,
                                    cost_basis=Decimal(0),
                                    proceeds=sl.proceeds,
                                    realized_pnl=sl.proceeds,
                                    currency=currency,
                                    direction="short",
                                )
                            )
                        short_lots.clear()
                        continue
                    else:
                        continue

                sell_qty = abs(qty)
                proceeds = total_amount

                # First, close long lots FIFO
                while sell_qty > 0 and lots:
                    lot = lots[0]
                    if lot.quantity <= sell_qty:
                        used_qty = lot.quantity
                        cost = lot.price * used_qty
                        lots.pop(0)
                    else:
                        used_qty = sell_qty
                        cost = lot.price * used_qty
                        lot.quantity -= used_qty

                    closed_trades.append(
                        ClosedTrade(
                            instrument_id=instrument_id,
                            symbol=symbol,
                            account_id=account_id,
                            open_date=lot.trade_date,
                            close_date=tx["trade_date"],
                            quantity=used_qty,
                            cost_basis=cost,
                            proceeds=proceeds * (used_qty / abs(qty)),
                            realized_pnl=(proceeds * (used_qty / abs(qty))) - cost - commission * (used_qty / abs(qty)),
                            currency=currency,
                            direction="long",
                        )
                    )
                    sell_qty -= used_qty

                # Remaining sell qty opens new short lots
                if sell_qty > 0:
                    short_proceeds = proceeds * (sell_qty / abs(qty))
                    short_lots.append(ShortLot(
                        quantity=sell_qty,
                        price=price,
                        currency=currency,
                        trade_date=tx["trade_date"],
                        proceeds=short_proceeds,
                    ))

    return closed_trades
