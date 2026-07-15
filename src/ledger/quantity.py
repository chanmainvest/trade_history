"""Canonical holding-quantity movement rules.

Parsers are not perfectly consistent across institutions: most sell rows carry
negative quantities already, but a few legacy rows don't. These rules convert a
transaction type + parsed quantity into the position movement used for
reconstruction.
"""
from __future__ import annotations

_POS_ABS = {
    "buy",
    "buy_to_cover",
    "transfer_in",
    "reinvest_dividend",
    "stock_split_credit",
    "option_buy_to_open",
    "option_buy_to_close",
}
_NEG_ABS = {
    "sell",
    "short_sell",
    "transfer_out",
    "stock_split_debit",
    "option_sell_to_open",
    "option_sell_to_close",
}
_CLOSE_SIGNED_POSITION = {
    "option_assignment",
    "option_exercise",
    "option_expiration",
}
POSITION_AFFECTING_TYPES = frozenset(
    {
        "buy",
        "sell",
        "short_sell",
        "buy_to_cover",
        "option_buy_to_open",
        "option_sell_to_open",
        "option_buy_to_close",
        "option_sell_to_close",
        "option_assignment",
        "option_exercise",
        "option_expiration",
        "transfer_in",
        "transfer_out",
        "journal",
        "reinvest_dividend",
        "stock_split",
        "stock_split_credit",
        "stock_split_debit",
        "name_change",
        "spinoff",
        "merger",
    }
)
NON_CASH_TXN_TYPES = frozenset(
    {
        "stock_split",
        "stock_split_credit",
        "stock_split_debit",
        "name_change",
        "spinoff",
        "merger",
    }
)
LEGACY_UNDERIVABLE_POSITION_TYPES = frozenset(
    {"stock_split", "name_change", "spinoff", "merger"}
)


def quantity_delta(txn_type: str, quantity: float | None) -> float:
    """Return the signed position movement for one transaction row."""
    if quantity is None:
        return 0.0
    q = float(quantity)
    if txn_type == "journal":
        # Brokers print in-kind journals with an already-signed quantity.
        return q
    if txn_type in _POS_ABS:
        return abs(q)
    if txn_type in _NEG_ABS:
        return -abs(q)
    if txn_type in _CLOSE_SIGNED_POSITION:
        return -q
    return 0.0


def normalized_position_delta(txn_type: str, quantity: float | None) -> float | None:
    """Return a safe normalized effect, preserving underdetermined legacy rows.

    The legacy helper intentionally returns zero for unsupported types to keep
    older read models running. A generic split, name change, spinoff, or merger
    cannot be inferred from a raw quantity alone, so persistence and
    reconciliation must retain the unknown value instead.
    """
    if quantity is None or txn_type in LEGACY_UNDERIVABLE_POSITION_TYPES:
        return None
    return quantity_delta(txn_type, quantity)
