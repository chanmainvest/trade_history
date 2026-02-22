from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from trade_history.api.auth import AuthContext
from trade_history.api.deps import require_read_access
from trade_history.api.schemas import (
    ClosedPlResponse,
    ReconciliationResponse,
    ReconciliationSnapshotResponse,
    TradesResponse,
)
from trade_history.services.analytics import (
    list_closed_positions,
    list_trades,
    monthly_reconciliation_snapshot_lines,
    monthly_statement_reconciliation,
)


router = APIRouter(prefix="/api", tags=["trades"])


@router.get("/trades", response_model=TradesResponse)
def get_trades(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=2000),
    sort_by: str = Query(default="trade_date"),
    sort_order: str = Query(default="desc"),
    account_id: str | None = Query(default=None),
    institution: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    _auth: AuthContext = Depends(require_read_access),
) -> dict:
    return list_trades(
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order.lower(),  # type: ignore[arg-type]
        account_id=account_id,
        institution=institution,
        symbol=symbol,
        event_type=event_type,
    )


@router.get("/positions/closed-pl", response_model=ClosedPlResponse)
def get_closed_positions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=2000),
    account_id: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    _auth: AuthContext = Depends(require_read_access),
) -> dict:
    return list_closed_positions(
        page=page,
        page_size=page_size,
        account_id=account_id,
        symbol=symbol,
    )


@router.get("/reconciliation/monthly", response_model=ReconciliationResponse)
def get_monthly_reconciliation(
    display_currency: str = Query(default="CAD"),
    institution: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    _auth: AuthContext = Depends(require_read_access),
) -> dict:
    return monthly_statement_reconciliation(
        display_currency=display_currency,
        institution=institution,
        account_id=account_id,
    )


@router.get("/reconciliation/monthly/snapshot-lines", response_model=ReconciliationSnapshotResponse)
def get_monthly_reconciliation_snapshot_lines(
    month: str = Query(pattern=r"^\d{4}-\d{2}$"),
    account_id: str = Query(min_length=1),
    currency_native: str | None = Query(default=None),
    display_currency: str = Query(default="CAD"),
    institution: str | None = Query(default=None),
    _auth: AuthContext = Depends(require_read_access),
) -> dict:
    return monthly_reconciliation_snapshot_lines(
        month=month,
        account_id=account_id,
        currency_native=currency_native,
        display_currency=display_currency,
        institution=institution,
    )
