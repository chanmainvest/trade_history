from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from trade_history.api.auth import AuthContext
from trade_history.api.deps import require_read_access
from trade_history.api.schemas import AssetValueResponse, SectorResponse
from trade_history.services.analytics import asset_values, sector_allocation


router = APIRouter(prefix="/api/assets", tags=["assets"])


@router.get("/value", response_model=AssetValueResponse)
def get_asset_values(
    display_currency: str = Query(default="CAD"),
    group_by: str = Query(default="total"),
    institution: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    _auth: AuthContext = Depends(require_read_access),
) -> dict:
    return asset_values(
        display_currency=display_currency,
        group_by=group_by,  # type: ignore[arg-type]
        institution=institution,
        account_id=account_id,
    )


@router.get("/sector", response_model=SectorResponse)
def get_sector(
    display_currency: str = Query(default="CAD"),
    _auth: AuthContext = Depends(require_read_access),
) -> dict:
    return sector_allocation(display_currency=display_currency)
