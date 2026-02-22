from __future__ import annotations

from pydantic import BaseModel, Field


class TradesResponse(BaseModel):
    total: int
    items: list[dict]


class ClosedPlResponse(BaseModel):
    total: int
    items: list[dict]


class AssetValueResponse(BaseModel):
    display_currency: str
    items: list[dict]


class SectorResponse(BaseModel):
    display_currency: str
    total: float
    items: list[dict]


class ReconciliationResponse(BaseModel):
    display_currency: str
    items: list[dict]


class ReconciliationSnapshotResponse(BaseModel):
    display_currency: str
    items: list[dict]


class JobResponse(BaseModel):
    status: str = Field(default="ok")
    result: dict
