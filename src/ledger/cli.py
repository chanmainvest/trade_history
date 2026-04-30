"""`ledger` CLI."""
from __future__ import annotations

from pathlib import Path

import click

from . import config
from .db import duckdb_store, sqlite as sqlite_db
from .logging_setup import get_logger
from .pdf_text import extract_pdf


@click.group()
def main() -> None:
    """Ledger — multi-broker trading history & analytics."""


# --------------------------------------------------------------------------- db
@main.group()
def db() -> None:
    """Database admin."""


@db.command("init")
def db_init() -> None:
    """Create SQLite + DuckDB schemas if missing."""
    log = get_logger("db_init")
    sqlite_db.init_db()
    log.info("SQLite ready: %s", config.SQLITE_PATH)
    duckdb_store.init_db()
    log.info("DuckDB ready: %s", config.DUCKDB_PATH)
    # Seed institutions row
    with sqlite_db.session() as conn:
        for code, name in config.INSTITUTIONS.items():
            sqlite_db.upsert_institution(conn, code=name, display_name=code)
    log.info("Institutions seeded.")


# -------------------------------------------------------------------------- pdf
@main.group()
def pdf() -> None:
    """PDF utilities."""


@pdf.command("dump-all")
@click.option("--institution", default=None, help="Restrict to one folder name.")
def pdf_dump_all(institution: str | None) -> None:
    """Dump page-by-page text of every PDF into data/text_dumps/."""
    log = get_logger("pdf_dump_all")
    out_root = config.TEXT_DUMP_DIR
    out_root.mkdir(parents=True, exist_ok=True)
    for folder in sorted(config.STATEMENTS_DIR.iterdir()):
        if not folder.is_dir():
            continue
        if institution and folder.name != institution:
            continue
        for p in sorted(folder.glob("*.pdf")):
            try:
                t = extract_pdf(p, repo_root=config.ROOT)
            except Exception as e:
                log.exception("Failed to read %s: %s", p, e)
                continue
            out_dir = out_root / folder.name
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / (p.stem + ".txt")
            out_path.write_text(
                f"# relpath: {t.relpath}\n# pages: {t.page_count}\n# image_only: {t.is_image_only}\n"
                + "\n\n----- PAGE BREAK -----\n\n".join(t.pages),
                encoding="utf-8",
            )
            log.info("Dumped %s (image_only=%s)", t.relpath, t.is_image_only)


@pdf.command("dump-samples")
@click.option("--per-folder", default=2, show_default=True,
              help="How many sample PDFs per institution folder to dump.")
def pdf_dump_samples(per_folder: int) -> None:
    """Dump page-by-page text of a few PDFs per institution into data/text_dumps/."""
    log = get_logger("pdf_dump_samples")
    out_root = config.TEXT_DUMP_DIR
    out_root.mkdir(parents=True, exist_ok=True)
    for folder in sorted(config.STATEMENTS_DIR.iterdir()):
        if not folder.is_dir():
            continue
        pdfs = sorted(p for p in folder.glob("*.pdf"))
        chosen: list[Path] = []
        if pdfs:
            chosen.append(pdfs[0])
        if len(pdfs) > 1 and per_folder >= 2:
            chosen.append(pdfs[len(pdfs) // 2])
        if len(pdfs) > 2 and per_folder >= 3:
            chosen.append(pdfs[-1])
        chosen = chosen[:per_folder]
        for p in chosen:
            try:
                t = extract_pdf(p, repo_root=config.ROOT)
            except Exception as e:
                log.exception("Failed to read %s: %s", p, e)
                continue
            out_dir = out_root / folder.name
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / (p.stem + ".txt")
            out_path.write_text(
                f"# relpath: {t.relpath}\n# pages: {t.page_count}\n# image_only: {t.is_image_only}\n"
                + "\n\n----- PAGE BREAK -----\n\n".join(t.pages),
                encoding="utf-8",
            )
            log.info("Dumped %s (%d pages, image_only=%s)", t.relpath, t.page_count, t.is_image_only)


# ----------------------------------------------------------------------- ingest
@main.group()
def ingest() -> None:
    """Ingest statements into SQLite."""


@ingest.command("run")
@click.option("--institution", default=None, help="Restrict to one folder name.")
@click.option("--limit", type=int, default=None, help="Stop after N PDFs.")
def ingest_run(institution: str | None, limit: int | None) -> None:
    from .ingest.pipeline import run_ingest
    run_ingest(institution=institution, limit=limit)


# ----------------------------------------------------------------------- market
@main.group()
def market() -> None:
    """Market-data scraping."""


@market.command("refresh")
@click.option("--symbol", "symbols", multiple=True,
              help="Override list of symbols. Default: all symbols held.")
@click.option("--lookback-years", type=int, default=15)
def market_refresh(symbols: tuple[str, ...], lookback_years: int) -> None:
    from .market.scrape import refresh_market_data
    refresh_market_data(symbols=list(symbols) or None, lookback_years=lookback_years)


@market.command("refresh-dividends")
def market_refresh_dividends() -> None:
    from .market.extras import refresh_dividends
    refresh_dividends()


@market.command("refresh-splits")
def market_refresh_splits() -> None:
    from .market.extras import refresh_splits
    refresh_splits()


@market.command("refresh-financials")
def market_refresh_financials() -> None:
    from .market.extras import refresh_financials
    refresh_financials()


@market.command("refresh-earnings")
def market_refresh_earnings() -> None:
    from .market.extras import refresh_earnings
    refresh_earnings()


@market.command("refresh-fx")
@click.option("--lookback-years", type=int, default=15)
def market_refresh_fx(lookback_years: int) -> None:
    from .market.extras import refresh_fx
    refresh_fx(lookback_years=lookback_years)


@market.command("refresh-all")
@click.option("--lookback-years", type=int, default=15)
def market_refresh_all(lookback_years: int) -> None:
    """Run prices + dividends + splits + financials + earnings + FX."""
    from .market.scrape import refresh_market_data
    from .market.extras import (refresh_dividends, refresh_splits,
                                refresh_financials, refresh_earnings,
                                refresh_fx)
    refresh_market_data(lookback_years=lookback_years)
    refresh_dividends()
    refresh_splits()
    refresh_financials()
    refresh_earnings()
    refresh_fx(lookback_years=lookback_years)


# ------------------------------------------------------------------------ serve
@main.command("serve")
@click.option("--host", default="127.0.0.1")
@click.option("--port", type=int, default=8000)
def serve(host: str, port: int) -> None:
    """Run the FastAPI dev server."""
    import uvicorn
    uvicorn.run("ledger.api.app:app", host=host, port=port, reload=True)
