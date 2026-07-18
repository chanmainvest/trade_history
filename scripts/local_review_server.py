"""Serve the built frontend and one explicit SQLite ledger for local review."""
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def create_app(*, database: Path, dist: Path) -> FastAPI:
    """Bind the database before importing API modules, then mount the built UI."""
    from ledger import config as ledger_config

    ledger_config.SQLITE_PATH = database.resolve()

    # Import only after binding config: SQLite helper defaults are captured
    # while the API module graph is imported.
    from ledger.api.app import app as api_app

    app = FastAPI(title="Trade History local review")
    app.mount("/api", api_app)
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        candidate = (dist / path).resolve()
        if candidate.is_file() and dist in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--dist", type=Path, default=Path("frontend/dist"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5175)
    args = parser.parse_args()

    database = args.database.resolve()
    dist = args.dist.resolve()
    if not database.is_file():
        parser.error(f"database does not exist: {database}")
    if not (dist / "index.html").is_file() or not (dist / "assets").is_dir():
        parser.error(f"built frontend not found under: {dist}")

    uvicorn.run(
        create_app(database=database, dist=dist),
        host=args.host,
        port=args.port,
    )


if __name__ == "__main__":
    main()
