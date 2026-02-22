from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from trade_history.api.routes import assets, jobs, meta, symbols, trades
from trade_history.config import settings
from trade_history.db.duck import init_db as init_duckdb
from trade_history.db.sqlite import init_db as init_sqlite


def create_app() -> FastAPI:
    init_sqlite()
    init_duckdb()

    app = FastAPI(
        title="Trade History API",
        version="0.1.0",
        description="Trading history and performance tracking backend.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(trades.router)
    app.include_router(assets.router)
    app.include_router(meta.router)
    app.include_router(symbols.router)
    app.include_router(jobs.router)

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    frontend_dist = settings.project_root / "frontend" / "dist"
    if frontend_dist.exists():
        app.mount("/", StaticFiles(directory=Path(frontend_dist), html=True), name="frontend")

    return app


app = create_app()
