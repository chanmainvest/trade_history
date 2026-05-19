"""FastAPI application entry point."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from trade_history.api.routes import assets, monthly, sectors, statements, trades

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise DB on startup
    from trade_history.db.sqlite import init_db

    db_path = Path(os.environ.get("DB_PATH", "data")) / "trade_history.db"
    init_db(db_path)
    yield


app = FastAPI(
    title="Trade History API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# API routes
app.include_router(trades.router, prefix="/trades", tags=["trades"])
app.include_router(assets.router, prefix="/asset-values", tags=["assets"])
app.include_router(sectors.router, prefix="/sectors", tags=["sectors"])
app.include_router(statements.router, prefix="/statements", tags=["statements"])
app.include_router(monthly.router, prefix="/monthly-balances", tags=["monthly"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# Serve built React SPA (Docker / production only)
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
