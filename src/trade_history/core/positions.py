from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
import math
import sqlite3
from typing import Any


POSITIVE_SIDES = {
    "BUY",
    "BUY_TO_OPEN",
    "BUY_TO_CLOSE",
    "BUY_TO_COVER",
    "BTO",
    "BTC",
    "TRANSFER_IN",
}
NEGATIVE_SIDES = {
    "SELL",
    "SELL_SHORT",
    "SELL_TO_OPEN",
    "SELL_TO_CLOSE",
    "SOLD",
    "STO",
    "STC",
    "TRANSFER_OUT",
}


@dataclass(slots=True)
class PositionState:
    quantity: float = 0.0
    cost_total_native: float = 0.0

    @property
    def avg_cost(self) -> float | None:
        if math.isclose(self.quantity, 0.0):
            return None
        return self.cost_total_native / self.quantity


def _parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def signed_quantity(side: str | None, quantity: float | None) -> float | None:
    if quantity is None:
        return None
    qty = abs(quantity)
    if side:
        s = side.upper()
        if s in POSITIVE_SIDES:
            return qty
        if s in NEGATIVE_SIDES:
            return -qty
    # Fall back to source sign when side is missing.
    return quantity


def link_transfers(conn: sqlite3.Connection, max_days: int = 10) -> int:
    conn.execute("DELETE FROM transfers")
    rows = conn.execute(
        """
        SELECT
          e.event_id,
          e.account_id,
          e.trade_date,
          e.side,
          ABS(COALESCE(e.quantity, 0)) AS quantity_abs,
          COALESCE(e.currency, 'CAD') AS currency,
          COALESCE(i.symbol_norm, 'CASH') AS symbol_norm
        FROM events e
        LEFT JOIN instruments i ON i.instrument_id = e.instrument_id
        WHERE e.instrument_id IS NOT NULL
          AND ABS(COALESCE(e.quantity, 0)) > 0
          AND (
            LOWER(COALESCE(e.event_type, '')) = 'transfer'
            OR UPPER(COALESCE(e.side, '')) IN ('TRANSFER_IN', 'TRANSFER_OUT')
          )
        ORDER BY date(e.trade_date), e.event_id
        """
    ).fetchall()

    outs: list[sqlite3.Row] = []
    ins: list[sqlite3.Row] = []
    for row in rows:
        side = (row["side"] or "").upper()
        if side == "TRANSFER_OUT":
            outs.append(row)
        elif side == "TRANSFER_IN":
            ins.append(row)

    matched_out_ids: set[int] = set()
    created = 0

    for incoming in ins:
        in_date = _parse_iso_date(incoming["trade_date"])
        candidates = []
        for outgoing in outs:
            if outgoing["event_id"] in matched_out_ids:
                continue
            if outgoing["account_id"] == incoming["account_id"]:
                continue
            if outgoing["symbol_norm"] != incoming["symbol_norm"]:
                continue
            if outgoing["currency"] != incoming["currency"]:
                continue
            if not math.isclose(outgoing["quantity_abs"], incoming["quantity_abs"], rel_tol=0.001, abs_tol=0.001):
                continue
            out_date = _parse_iso_date(outgoing["trade_date"])
            day_delta = abs((in_date - out_date).days)
            if day_delta <= max_days:
                candidates.append((day_delta, outgoing))

        if not candidates:
            continue
        candidates.sort(key=lambda item: item[0])
        matched = candidates[0][1]
        matched_out_ids.add(matched["event_id"])
        transfer_key = (
            f"{matched['symbol_norm']}:{incoming['quantity_abs']}:"
            f"{matched['event_id']}->{incoming['event_id']}"
        )
        conn.execute(
            """
            INSERT INTO transfers(from_event_id, to_event_id, transfer_group_key, continuity_mode)
            VALUES (?, ?, ?, 'carry_cost')
            """,
            (matched["event_id"], incoming["event_id"], transfer_key),
        )
        created += 1
    return created


def rebuild_positions(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.execute("DELETE FROM lot_closures")
    conn.execute("DELETE FROM position_state")
    transfer_count = link_transfers(conn)

    transfer_lookup: dict[int, int] = {
        int(row["to_event_id"]): int(row["from_event_id"])
        for row in conn.execute("SELECT from_event_id, to_event_id FROM transfers").fetchall()
    }

    events = conn.execute(
        """
        SELECT
          e.event_id,
          e.account_id,
          e.trade_date,
          e.event_type,
          e.side,
          e.quantity,
          e.price,
          e.gross_amount,
          e.commission,
          e.fees,
          COALESCE(e.currency, 'CAD') AS currency,
          e.instrument_id
        FROM events e
        WHERE e.instrument_id IS NOT NULL
          AND LOWER(COALESCE(e.event_type, '')) IN ('trade', 'transfer')
        ORDER BY
          date(e.trade_date),
          CASE
            WHEN LOWER(COALESCE(e.event_type, '')) = 'transfer'
                 AND UPPER(COALESCE(e.side, '')) = 'TRANSFER_OUT' THEN 0
            WHEN LOWER(COALESCE(e.event_type, '')) = 'transfer'
                 AND UPPER(COALESCE(e.side, '')) = 'TRANSFER_IN' THEN 2
            ELSE 1
          END,
          e.event_id
        """
    ).fetchall()

    positions: dict[tuple[str, int, str], PositionState] = defaultdict(PositionState)
    transfer_basis_by_out_event: dict[int, float] = {}
    closed_rows = 0
    processed_events = 0

    for row in events:
        processed_events += 1
        event_id = int(row["event_id"])
        account_id = str(row["account_id"])
        instrument_id = int(row["instrument_id"])
        currency = str(row["currency"] or "CAD")
        key = (account_id, instrument_id, currency)
        state = positions[key]

        side = (row["side"] or "").upper() or None
        qty_signed = signed_quantity(side, row["quantity"])
        if qty_signed is None or math.isclose(qty_signed, 0.0):
            continue

        fee_total = float(row["commission"] or 0.0) + float(row["fees"] or 0.0)
        event_type = (row["event_type"] or "").lower()
        price = float(row["price"]) if row["price"] is not None else None

        if event_type == "transfer":
            if side == "TRANSFER_OUT":
                avg = state.avg_cost if state.avg_cost is not None else 0.0
                transfer_basis_by_out_event[event_id] = avg
                state.quantity += qty_signed
                state.cost_total_native += qty_signed * avg
            elif side == "TRANSFER_IN":
                from_event_id = transfer_lookup.get(event_id)
                carry_price = transfer_basis_by_out_event.get(from_event_id) if from_event_id else None
                implied_price = None
                if row["gross_amount"] is not None and row["quantity"] not in (None, 0):
                    implied_price = abs(float(row["gross_amount"]) / float(row["quantity"]))
                fallback_price = implied_price if implied_price is not None else price
                if fallback_price is not None and abs(qty_signed) >= 100 and fallback_price > 100000:
                    fallback_price = 0.0
                effective_price = carry_price if carry_price is not None else (fallback_price or 0.0)
                state.quantity += qty_signed
                state.cost_total_native += qty_signed * effective_price
            else:
                # Unknown transfer direction, keep state unchanged.
                continue
            if math.isclose(state.quantity, 0.0, abs_tol=1e-9):
                state.quantity = 0.0
                state.cost_total_native = 0.0
            continue

        if price is None:
            if row["gross_amount"] is not None and row["quantity"] not in (None, 0):
                price = abs(float(row["gross_amount"]) / float(row["quantity"]))
            else:
                continue

        q = state.quantity
        c = state.cost_total_native

        if math.isclose(q, 0.0) or math.copysign(1, q) == math.copysign(1, qty_signed):
            state.quantity = q + qty_signed
            state.cost_total_native = c + (qty_signed * price) + fee_total
            if math.isclose(state.quantity, 0.0, abs_tol=1e-9):
                state.quantity = 0.0
                state.cost_total_native = 0.0
            continue

        avg = c / q
        close_qty = min(abs(q), abs(qty_signed))
        sign_q = 1.0 if q > 0 else -1.0

        close_ratio = close_qty / abs(qty_signed) if abs(qty_signed) > 0 else 0.0
        close_fee = fee_total * close_ratio
        open_fee = fee_total - close_fee

        realized = close_qty * (price - avg) * sign_q - close_fee
        notional_cost = close_qty * abs(avg)
        notional_proceeds = close_qty * price

        conn.execute(
            """
            INSERT INTO lot_closures(
              close_event_id, instrument_id, account_id, quantity_closed, proceeds_native,
              cost_native, realized_pl_native, currency, method
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'average_cost')
            """,
            (
                event_id,
                instrument_id,
                account_id,
                close_qty,
                notional_proceeds,
                notional_cost,
                realized,
                currency,
            ),
        )
        closed_rows += 1

        q_after_close = q - (sign_q * close_qty)
        c_after_close = c - (avg * sign_q * close_qty)
        remaining = qty_signed + (sign_q * close_qty)

        if math.isclose(remaining, 0.0, abs_tol=1e-9):
            state.quantity = q_after_close
            state.cost_total_native = c_after_close
        else:
            state.quantity = q_after_close + remaining
            state.cost_total_native = c_after_close + (remaining * price) + open_fee

        if math.isclose(state.quantity, 0.0, abs_tol=1e-9):
            state.quantity = 0.0
            state.cost_total_native = 0.0

    for (account_id, instrument_id, currency), state in positions.items():
        if math.isclose(state.quantity, 0.0, abs_tol=1e-9):
            continue
        conn.execute(
            """
            INSERT INTO position_state(
              account_id, instrument_id, currency, quantity, cost_total_native, avg_cost_native,
              as_of_event_id, as_of_trade_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                instrument_id,
                currency,
                state.quantity,
                state.cost_total_native,
                state.avg_cost,
                events[-1]["event_id"] if events else 0,
                events[-1]["trade_date"] if events else date.today().isoformat(),
            ),
        )

    return {
        "processed_events": processed_events,
        "closed_lot_rows": closed_rows,
        "open_positions": sum(1 for s in positions.values() if not math.isclose(s.quantity, 0.0)),
        "transfers_linked": transfer_count,
    }
