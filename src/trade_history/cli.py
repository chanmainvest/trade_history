from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from trade_history.config import settings
from trade_history.db.duck import init_db as init_duckdb
from trade_history.db.sqlite import init_db as init_sqlite
from trade_history.services.jobs import rebuild_views, run_fx_ingest, run_price_ingest, run_statement_ingest


app = typer.Typer(help="Trading history ingestion and analytics CLI")
ingest_app = typer.Typer(help="Data ingestion commands")
app.add_typer(ingest_app, name="ingest")


INSTITUTION_ALIASES: dict[str, list[str]] = {
    "cibc": ["cibc invest direct", "cibc imperial service", "cibc tsfa"],
    "hsbc": ["hsbc direct invest"],
    "rbc": ["rbc invest direct"],
    "td": ["td webbroker"],
    "cibc invest direct": ["cibc invest direct"],
    "cibc imperial service": ["cibc imperial service"],
    "cibc tsfa": ["cibc tsfa"],
    "hsbc direct invest": ["hsbc direct invest"],
    "rbc invest direct": ["rbc invest direct"],
    "td webbroker": ["td webbroker"],
}


def _expand_institutions(values: list[str]) -> list[str]:
    if not values:
        return []
    expanded: list[str] = []
    for item in values:
        key = item.strip().lower()
        expanded.extend(INSTITUTION_ALIASES.get(key, [key]))
    deduped = []
    seen = set()
    for v in expanded:
        if v in seen:
            continue
        seen.add(v)
        deduped.append(v)
    return deduped


@app.command("init-db")
def init_db() -> None:
    """Initialize SQLite and DuckDB schemas."""
    init_sqlite()
    init_duckdb()
    typer.echo(f"Initialized: {settings.sqlite_path} and {settings.duckdb_path}")


@ingest_app.command("statements")
def ingest_statements_cmd(
    root: Annotated[Path, typer.Option(help="Root folder containing institution statement folders")] = settings.statements_root,
    institution: Annotated[list[str], typer.Option(help="Institution(s): cibc, hsbc, rbc, td or exact folder name")] = [],
) -> None:
    """Ingest statement PDFs into SQLite."""
    expanded = _expand_institutions(institution)
    result = run_statement_ingest(root=root, institutions=expanded or None)
    typer.echo(result)


@ingest_app.command("prices")
def ingest_prices_cmd(
    sources: Annotated[str, typer.Option(help="Comma-separated sources: stooq,yahoo")] = "stooq,yahoo",
    refresh_sector_metadata: Annotated[
        bool,
        typer.Option(help="Refresh sector metadata from provider search endpoint"),
    ] = True,
) -> None:
    """Ingest historical prices into DuckDB."""
    items = {part.strip().lower() for part in sources.split(",") if part.strip()}
    use_stooq = "stooq" in items
    use_yahoo = "yahoo" in items
    result = run_price_ingest(
        use_stooq=use_stooq,
        use_yahoo=use_yahoo,
        refresh_sector_metadata=refresh_sector_metadata,
    )
    typer.echo(result)


@ingest_app.command("fx")
def ingest_fx_cmd() -> None:
    """Ingest BoC FX rates."""
    result = run_fx_ingest()
    typer.echo(result)


@app.command("rebuild-views")
def rebuild_views_cmd() -> None:
    """Rebuild transfer links, closed lots, and position state."""
    result = rebuild_views()
    typer.echo(result)


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option(help="Host address")] = settings.api_host,
    port: Annotated[int, typer.Option(help="Port")] = settings.api_port,
    reload: Annotated[bool, typer.Option(help="Enable auto-reload")] = False,
) -> None:
    """Run FastAPI server."""
    uvicorn.run("trade_history.api.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
