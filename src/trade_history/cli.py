"""CLI entry point: `trade-history <command>`."""

from __future__ import annotations

import os
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()


@click.group()
def app() -> None:
    """Trade History CLI."""


@app.group()
def ingest() -> None:
    """Data ingestion commands."""


@ingest.command("statements")
@click.option(
    "--statements-dir",
    default=None,
    envvar="STATEMENTS_DIR",
    help="Root directory containing brokerage statement PDFs.",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--path",
    "target_path",
    default=None,
    help="Ingest only a specific PDF file or folder (recursively).",
    type=click.Path(exists=True, path_type=Path),
)
@click.option("--force", is_flag=True, help="Re-process already-ingested statements.")
@click.option("--dry-run", is_flag=True, help="Parse only; do not write to DB.")
def ingest_statements(
    statements_dir: Path | None,
    target_path: Path | None,
    force: bool,
    dry_run: bool,
) -> None:
    """Ingest PDF brokerage statements.

    By default, ingests all PDFs under --statements-dir.
    Use --path to target a specific file or folder instead.
    """
    from trade_history.ingest.pipeline import IngestPipeline

    if target_path is None and statements_dir is None:
        click.echo("Error: --statements-dir or --path required.", err=True)
        raise SystemExit(1)

    # If --path given without --statements-dir, use --path's parent as statements_dir
    if statements_dir is None:
        statements_dir = target_path.parent if target_path.is_file() else target_path

    db_dir = Path(os.environ.get("DB_PATH", "data"))
    db_path = db_dir / "trade_history.db"
    duckdb_path = db_dir / "market_data.duckdb"

    pipeline = IngestPipeline(db_path=db_path, duckdb_path=duckdb_path)
    pipeline.run(
        statements_dir=statements_dir,
        force=force,
        dry_run=dry_run,
        target_path=target_path,
    )


@app.command("serve")
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.option("--reload", is_flag=True)
def serve(host: str, port: int, reload: bool) -> None:
    """Start the FastAPI server."""
    import uvicorn

    uvicorn.run(
        "trade_history.api.main:app",
        host=host,
        port=port,
        reload=reload,
    )
