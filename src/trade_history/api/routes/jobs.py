from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from trade_history.api.deps import require_write_access
from trade_history.api.schemas import JobResponse
from trade_history.services.jobs import rebuild_views, run_fx_ingest, run_price_ingest, run_statement_ingest


router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class StatementIngestRequest(BaseModel):
    root: str | None = None
    institutions: list[str] = Field(default_factory=list)
    force: bool = False


class PriceIngestRequest(BaseModel):
    use_stooq: bool = True
    use_yahoo: bool = True
    refresh_sector_metadata: bool = True


@router.post("/ingest/statements", response_model=JobResponse)
def ingest_statements_job(
    payload: StatementIngestRequest,
    _=Depends(require_write_access),
) -> dict:
    result = run_statement_ingest(
        root=None if payload.root is None else payload.root,
        institutions=payload.institutions or None,
        force=payload.force,
    )
    return {"status": "ok", "result": result}


@router.post("/ingest/prices", response_model=JobResponse)
def ingest_prices_job(
    payload: PriceIngestRequest,
    _=Depends(require_write_access),
) -> dict:
    result = run_price_ingest(
        use_stooq=payload.use_stooq,
        use_yahoo=payload.use_yahoo,
        refresh_sector_metadata=payload.refresh_sector_metadata,
    )
    return {"status": "ok", "result": result}


@router.post("/ingest/fx", response_model=JobResponse)
def ingest_fx_job(
    _=Depends(require_write_access),
) -> dict:
    result = run_fx_ingest()
    return {"status": "ok", "result": result}


@router.post("/rebuild/views", response_model=JobResponse)
def rebuild_views_job(
    _=Depends(require_write_access),
) -> dict:
    result = rebuild_views()
    return {"status": "ok", "result": result}
