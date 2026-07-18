"""GET /performance — aggregate the canonical holdings state over time."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Query

from ...db import sqlite as sqlite_db
from ...holdings import holding_dates, holdings_at

router = APIRouter(prefix="/performance", tags=["performance"])
FORWARD_FILL_MAX_DAYS = 90


def _csv_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _csv_ints(value: str | None) -> list[int]:
    return [int(part) for part in _csv_list(value) if part.lstrip("-").isdigit()]


def _matching_account_ids(
    institution: str | None,
    account_id: str | None,
    *,
    path: Path | str | None = None,
) -> tuple[list[int], bool]:
    """Return matching IDs and whether a caller explicitly constrained scope."""
    institutions = _csv_list(institution)
    accounts = _csv_ints(account_id)
    constrained = bool(institutions or accounts)
    if not constrained:
        return [], False
    clauses: list[str] = []
    params: list = []
    if institutions:
        clauses.append(f"i.code IN ({','.join('?' * len(institutions))})")
        params.extend(institutions)
    if accounts:
        clauses.append(f"a.account_id IN ({','.join('?' * len(accounts))})")
        params.extend(accounts)
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    with sqlite_db.session(db_path) as conn:
        rows = conn.execute(
            """
            SELECT a.account_id
              FROM accounts a
              JOIN institutions i ON i.institution_id = a.institution_id
             WHERE """
            + " AND ".join(clauses),
            params,
        ).fetchall()
    return [int(row["account_id"]) for row in rows], True


def _filter_rows(
    rows: list[dict],
    *,
    as_of: str,
    symbols: set[str],
    asset_type: str | None,
    include_cash: bool,
    exact_checkpoint_only: bool,
    max_checkpoint_age_days: int | None,
) -> list[dict]:
    cash_allowed = include_cash and not symbols and not asset_type
    out: list[dict] = []
    for row in rows:
        if max_checkpoint_age_days is not None:
            checkpoint = row.get("checkpoint_date")
            if checkpoint is None:
                continue
            age = (date.fromisoformat(as_of) - date.fromisoformat(str(checkpoint))).days
            if age > max_checkpoint_age_days:
                continue
        if row["asset_type"] == "cash":
            if not cash_allowed:
                continue
        elif symbols and str(row["symbol"]).upper() not in symbols:
            continue
        elif asset_type and row["asset_type"] != asset_type:
            continue
        if exact_checkpoint_only and not row["is_reported"]:
            continue
        out.append(row)
    return out


@router.get("/total")
def total(
    institution: str | None = Query(None),
    account_id: str | None = Query(None),
    symbol: str | None = Query(None),
    asset_type: str | None = Query(None),
    forward_fill: bool = Query(True, description="Carry canonical holdings state forward"),
    include_cash: bool = Query(
        True,
        description="Include cash balances when no security filter is active",
    ),
) -> dict:
    return {
        "rows": _total_rows(
            institution=institution,
            account_id=account_id,
            symbol=symbol,
            asset_type=asset_type,
            forward_fill=forward_fill,
            include_cash=include_cash,
        ),
        "forward_fill_max_days": FORWARD_FILL_MAX_DAYS if forward_fill else None,
    }


def _total_rows(
    *,
    institution: str | None = None,
    account_id: str | None = None,
    symbol: str | None = None,
    asset_type: str | None = None,
    forward_fill: bool = True,
    include_cash: bool = True,
    path: Path | str | None = None,
) -> list[dict]:
    account_ids, constrained = _matching_account_ids(
        institution,
        account_id,
        path=path,
    )
    if constrained and not account_ids:
        return []
    dates = holding_dates(account_ids, path=path)
    if not dates:
        return []
    if forward_fill:
        today = date.today().isoformat()
        if dates[-1] < today:
            dates.append(today)
    symbols = {value.upper() for value in _csv_list(symbol)}
    values: dict[tuple[str, str], float] = {}
    currencies: set[str] = set()
    for as_of in dates:
        rows = _filter_rows(
            holdings_at(as_of, account_ids, path=path),
            as_of=as_of,
            symbols=symbols,
            asset_type=asset_type,
            include_cash=include_cash,
            exact_checkpoint_only=not forward_fill,
            max_checkpoint_age_days=(
                FORWARD_FILL_MAX_DAYS if forward_fill else None
            ),
        )
        for row in rows:
            currency = str(row["currency"])
            currencies.add(currency)
            key = (as_of, currency)
            values[key] = values.get(key, 0.0) + float(row["market_value"] or 0.0)
    if not currencies:
        return []
    return [
        {
            "as_of_date": as_of,
            "currency": currency,
            "market_value": values.get((as_of, currency), 0.0),
        }
        for as_of in dates
        for currency in sorted(currencies)
    ]


def _cash_rows(
    *,
    institution: str | None = None,
    account_id: str | None = None,
    path: Path | str | None = None,
) -> list[dict]:
    account_ids, constrained = _matching_account_ids(
        institution,
        account_id,
        path=path,
    )
    if constrained and not account_ids:
        return []
    rows: list[dict] = []
    for as_of in holding_dates(account_ids, path=path):
        for holding in holdings_at(as_of, account_ids, path=path):
            if holding["asset_type"] != "cash" or not holding["is_reported"]:
                continue
            rows.append(
                {
                    "as_of_date": as_of,
                    "account_id": holding["account_id"],
                    "currency": holding["currency"],
                    "closing_balance": holding["quantity"],
                    "holding_state": holding["holding_state"],
                    "reconciliation_status": holding["reconciliation_status"],
                    "quality_warnings": holding["quality_warnings"],
                }
            )
    return rows


@router.get("/cash")
def cash(
    account_id: str | None = Query(None),
    institution: str | None = Query(None),
) -> dict:
    return {"rows": _cash_rows(account_id=account_id, institution=institution)}
