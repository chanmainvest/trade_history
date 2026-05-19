"""FastAPI app."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import config as config_route
from .routes import monthly, performance, research, statements, transactions, viz

app = FastAPI(title="Ledger API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(transactions.router)
app.include_router(monthly.router)
app.include_router(performance.router)
app.include_router(research.router)
app.include_router(viz.router)
app.include_router(config_route.router)
app.include_router(statements.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
