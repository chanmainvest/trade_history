"""GET /export/yahoo-csv — selected portfolio as a Yahoo Finance lots CSV.

Yahoo's "Import a CSV" portfolio flow accepts one row per holding lot with the
columns ``Symbol, Trade Date, Purchase Price, Quantity``.  Trade Date must be
numeric or empty, so this export leaves it empty (the ledger stores an
aggregated average cost, not dated lots).  Yahoo rejects negative share lots,
so short and other non-positive quantities are exported with quantity ``0``.
Rows that cannot be exported without inventing identity — cash, incomplete
holdings, instruments without a Yahoo mapping — are skipped and reported back
via the ``X-Yahoo-Export-Skipped`` header.
"""
from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Response

from ...db import sqlite as sqlite_db
from ...holdings import holdings_at, latest_holdings_date
from .monthly import _csv_ints

router = APIRouter(prefix="/export", tags=["export"])

# Same status set the holdings service joins on, minus 'failed': a failed
# mapping means Yahoo rejected the identity, so exporting it would send a
# symbol Yahoo has already refused.
_YAHOO_STATUSES = ("candidate", "verified")

_MAX_REPORTED_SKIPS = 20


def _yahoo_symbol_map() -> dict[str, str]:
    with sqlite_db.session(sqlite_db.SQLITE_PATH) as conn:
        rows = conn.execute(
            """
            SELECT i.instrument_key, m.provider_symbol
              FROM instrument_market_symbols m
              JOIN instruments i ON i.instrument_id = m.instrument_id
             WHERE m.provider = 'yahoo'
               AND m.status IN ('candidate', 'verified')
            """
        ).fetchall()
    return {str(row["instrument_key"]): str(row["provider_symbol"]) for row in rows}


def _yahoo_csv(rows: list[dict], symbol_map: dict[str, str]) -> tuple[str, list[str]]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Symbol", "Trade Date", "Purchase Price", "Quantity"])
    skipped: list[str] = []
    seen_symbols: set[str] = set()
    for row in rows:
        symbol = str(row["symbol"])
        if row["asset_type"] == "cash":
            continue
        yahoo_symbol = symbol_map.get(str(row["instrument_key"]))
        if yahoo_symbol is None:
            if symbol not in seen_symbols:
                skipped.append(symbol)
                seen_symbols.add(symbol)
            continue
        if row["holding_state"] == "incomplete":
            if symbol not in seen_symbols:
                skipped.append(symbol)
                seen_symbols.add(symbol)
            continue
        if float(row["quantity"] or 0.0) <= 0.0:
            # Yahoo rejects negative share lots; represent shorts as 0 so the
            # symbol still appears in the portfolio rather than vanishing.
            writer.writerow([
                yahoo_symbol,
                "",
                _number(row["avg_cost"]),
                "0",
            ])
            continue
        writer.writerow([
            yahoo_symbol,
            "",
            _number(row["avg_cost"]),
            _number(row["quantity"]),
        ])
    return buffer.getvalue(), skipped


def _number(value: float | None) -> str:
    if value is None:
        return ""
    text = f"{float(value):.10f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


@router.get("/yahoo-csv")
def yahoo_csv(
    month_end: date | None = None,
    account_id: str | None = None,
) -> Response:
    accts = _csv_ints(account_id)
    as_of = (
        month_end.isoformat()
        if month_end is not None
        else latest_holdings_date(account_ids=accts)
    )
    rows = holdings_at(as_of, accts) if as_of else []
    csv_text, skipped = _yahoo_csv(rows, _yahoo_symbol_map())
    reported = ",".join(skipped[:_MAX_REPORTED_SKIPS])
    if len(skipped) > _MAX_REPORTED_SKIPS:
        reported += ",…"
    headers = {"X-Yahoo-Export-Skipped": f"{len(skipped)}:{reported}"}
    if as_of:
        filename = f"yahoo_portfolio_{as_of.replace('-', '')}.csv"
    else:
        filename = "yahoo_portfolio.csv"
    headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers=headers,
    )
