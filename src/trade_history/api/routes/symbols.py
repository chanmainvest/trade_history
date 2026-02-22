from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from trade_history.api.deps import require_read_access, require_write_access
from trade_history.services.symbols import (
    delete_symbol_override,
    list_symbol_catalog,
    list_symbol_overrides,
    refresh_sectors,
    upsert_symbol_override,
)


router = APIRouter(prefix="/api/symbols", tags=["symbols"])


class SymbolOverrideRequest(BaseModel):
    market_symbol: str | None = None
    sector_override: str | None = None
    notes: str | None = None
    is_active: bool = True


class RefreshSectorRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list)


@router.get("")
def get_symbol_catalog(
    q: str | None = Query(default=None),
    _=Depends(require_read_access),
) -> dict:
    return list_symbol_catalog(query=q)


@router.get("/overrides")
def get_symbol_overrides(
    _=Depends(require_read_access),
) -> dict:
    return list_symbol_overrides()


@router.put("/overrides/{symbol_norm}")
def put_symbol_override(
    symbol_norm: str,
    payload: SymbolOverrideRequest,
    _=Depends(require_write_access),
) -> dict:
    try:
        row = upsert_symbol_override(
            symbol_norm=symbol_norm,
            market_symbol=payload.market_symbol,
            sector_override=payload.sector_override,
            notes=payload.notes,
            is_active=payload.is_active,
        )
        return {"item": row}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/overrides/{symbol_norm}")
def remove_symbol_override(
    symbol_norm: str,
    _=Depends(require_write_access),
) -> dict:
    return delete_symbol_override(symbol_norm)


@router.post("/refresh-sectors")
def refresh_symbol_sectors(
    payload: RefreshSectorRequest,
    _=Depends(require_write_access),
) -> dict:
    return refresh_sectors(payload.symbols or None)
