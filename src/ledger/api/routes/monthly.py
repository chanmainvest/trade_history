"""GET /monthly — canonical point-in-time holdings and comparisons."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated

import duckdb
from fastapi import APIRouter, Query

from ...config import DUCKDB_PATH
from ...holdings import holdings_at, latest_holdings_date

router = APIRouter(prefix="/monthly", tags=["monthly"])


def _csv_ints(v: str | None) -> list[int]:
    if not v:
        return []
    return [int(x) for x in v.split(",") if x.strip().lstrip("-").isdigit()]


def _holdings_at(
    as_of: str,
    account_ids: list[int],
    path: Path | str | None = None,
) -> list[dict]:
    """Compatibility wrapper for callers/tests; logic lives in ``holdings``."""
    return holdings_at(as_of, account_ids, path=path)


def _fx_rate(base: str, quote: str, as_of: str) -> tuple[float | None, str | None]:
    if base == quote:
        return 1.0, as_of
    try:
        con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
        try:
            row = con.execute(
                """
                SELECT rate, rate_date
                  FROM fx_rates
                 WHERE base = ? AND quote = ? AND rate_date <= ?
                 ORDER BY rate_date DESC
                 LIMIT 1
                """,
                [base, quote, as_of],
            ).fetchone()
            if row:
                return float(row[0]), str(row[1])
            inverse = con.execute(
                """
                SELECT rate, rate_date
                  FROM fx_rates
                 WHERE base = ? AND quote = ? AND rate_date <= ?
                 ORDER BY rate_date DESC
                 LIMIT 1
                """,
                [quote, base, as_of],
            ).fetchone()
            if inverse and inverse[0]:
                return 1.0 / float(inverse[0]), str(inverse[1])
        finally:
            con.close()
    except Exception:
        return None, None
    return None, None


def _snapshot_totals(rows: list[dict], as_of: str) -> dict:
    native: dict[str, float] = {}
    for row in rows:
        currency = row.get("currency") or ""
        if not currency:
            continue
        native[currency] = native.get(currency, 0.0) + float(row.get("market_value") or 0.0)
    usd_to_cad, cad_fx_date = _fx_rate("USD", "CAD", as_of)
    cad_to_usd, usd_fx_date = _fx_rate("CAD", "USD", as_of)
    combined: dict[str, float | str | None] = {}
    if usd_to_cad is not None:
        combined["CAD"] = native.get("CAD", 0.0) + native.get("USD", 0.0) * usd_to_cad
        combined["usd_cad"] = usd_to_cad
        combined["cad_fx_date"] = cad_fx_date
    if cad_to_usd is not None:
        combined["USD"] = native.get("USD", 0.0) + native.get("CAD", 0.0) * cad_to_usd
        combined["cad_usd"] = cad_to_usd
        combined["usd_fx_date"] = usd_fx_date
    return {"native": native, "combined": combined}


@router.get("/snapshot")
def snapshot(
    month_end: Annotated[
        date | None, Query(description="ISO date; defaults to latest")
    ] = None,
    account_id: str | None = Query(None),
) -> dict:
    accts = _csv_ints(account_id)
    month_end = month_end.isoformat() if month_end is not None else latest_holdings_date(account_ids=accts)
    rows = _holdings_at(month_end, accts) if month_end else []
    return {
        "as_of_date": month_end or "",
        "rows": rows,
        "totals": _snapshot_totals(rows, month_end) if month_end else {},
    }


@router.get("/diff")
def diff(
    a: Annotated[date, Query()],
    b: Annotated[date, Query()],
    account_id: str | None = Query(None),
) -> dict:
    accts = _csv_ints(account_id)
    a_text, b_text = a.isoformat(), b.isoformat()
    rows_a = {
        (row["account_id"], row["instrument_key"], row["currency"]): row
        for row in _holdings_at(a_text, accts)
    }
    rows_b = {
        (row["account_id"], row["instrument_key"], row["currency"]): row
        for row in _holdings_at(b_text, accts)
    }
    keys = set(rows_a) | set(rows_b)
    diffs = []
    for key in sorted(keys, key=lambda value: (value[1], value[0], value[2])):
        row_a, row_b = rows_a.get(key), rows_b.get(key)
        quantity_a = row_a["quantity"] if row_a else 0.0
        quantity_b = row_b["quantity"] if row_b else 0.0
        if abs((quantity_b or 0.0) - (quantity_a or 0.0)) < 1e-9:
            continue
        reference = row_b or row_a
        diffs.append(
            {
                "holding_key": reference["holding_key"],
                "account_id": reference["account_id"],
                "account_number": reference["account_number"],
                "institution_code": reference["institution_code"],
                "instrument_key": reference["instrument_key"],
                "symbol": reference["symbol"],
                "asset_type": reference["asset_type"],
                "currency": reference["currency"],
                "option_expiry": reference["option_expiry"],
                "option_strike": reference["option_strike"],
                "option_type": reference["option_type"],
                "qty_a": quantity_a,
                "qty_b": quantity_b,
                "qty_delta": (quantity_b or 0.0) - (quantity_a or 0.0),
                "mv_a": (row_a or {}).get("market_value"),
                "mv_b": (row_b or {}).get("market_value"),
            }
        )
    return {"a": a_text, "b": b_text, "rows": diffs}
