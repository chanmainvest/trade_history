"""`ledger` CLI."""
from __future__ import annotations

import os
from pathlib import Path

import click

from . import config
from .db import duckdb_store
from .db import sqlite as sqlite_db
from .logging_setup import get_logger
from .pdf_text import extract_pdf


@click.group()
@click.option("--profile", type=click.Choice(["real", "example"]),
              default=None,
              help="Workspace profile: 'real' (default, uses Statements/ + data/) "
                   "or 'example' (uses example_data/). "
                   "Equivalent to LEDGER_PROFILE env var; must be set before "
                   "Python loads ledger.config to take effect.")
def main(profile: str | None) -> None:
    """Ledger — multi-broker trading history & analytics."""
    if profile and os.environ.get("LEDGER_PROFILE") != profile:
        click.echo(
            f"WARNING: --profile={profile} requested after config was loaded "
            f"(active profile is '{config.PROFILE}'). Set the env var first:\n"
            f"  $env:LEDGER_PROFILE = '{profile}'   (PowerShell)\n"
            f"  export LEDGER_PROFILE={profile}     (bash)",
            err=True,
        )


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
        for folder_name, code in config.INSTITUTIONS.items():
            sqlite_db.upsert_institution(conn, code=code, display_name=folder_name)
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


# ------------------------------------------------------------------------ audit
@main.group()
def audit() -> None:
    """Read-only extraction and data-quality audits."""


@audit.command("extraction")
@click.option(
    "--statements-dir",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help="PDF or text-dump corpus root. Defaults to the active Statements directory.",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=None,
    help="JSONL report path. Defaults to logs/extraction_audit.jsonl.",
)
@click.option("--institution", default=None, help="Restrict to one immediate folder name.")
@click.option("--limit", type=click.IntRange(min=1), default=None, help="Audit at most N files.")
@click.option(
    "--fail-on-errors",
    is_flag=True,
    help="Exit non-zero for unclaimed, failed, or contract-invalid parser output.",
)
def audit_extraction_command(
    statements_dir: Path | None,
    output: Path | None,
    institution: str | None,
    limit: int | None,
    fail_on_errors: bool,
) -> None:
    """Parse PDFs/text dumps without writing SQLite and report contract failures."""
    from .ingest.audit import audit_extraction

    corpus_root = statements_dir or config.STATEMENTS_DIR
    report_path = output or (config.LOG_DIR / "extraction_audit.jsonl")
    summary = audit_extraction(
        statements_dir=corpus_root,
        output=report_path,
        institution=institution,
        limit=limit,
    )
    click.echo(
        f"Audited {summary['files']} files: {summary['parsed_files']} valid, "
        f"{summary['skipped_files']} skipped, "
        f"{summary['invalid_files']} invalid, {summary['unclaimed_files']} unclaimed, "
        f"{summary['failed_files']} failed."
    )
    click.echo(
        f"Contract issues: {summary['validation_errors']} errors, "
        f"{summary['validation_warnings']} warnings; "
        f"duplicate statement keys: {summary['duplicate_statement_keys']}."
    )
    click.echo(f"Report: {report_path}")
    if fail_on_errors and any(
        summary[name]
        for name in (
            "invalid_files",
            "unclaimed_files",
            "failed_files",
            "validation_errors",
        )
    ):
        raise click.ClickException("extraction audit found fatal issues")


# ----------------------------------------------------------------------- ingest
@main.group()
def ingest() -> None:
    """Ingest statements into SQLite."""


@ingest.command("run")
@click.option("--institution", default=None, help="Restrict to one folder name.")
@click.option("--limit", type=int, default=None, help="Stop after N PDFs.")
@click.option("--force", is_flag=True, help="Re-parse PDFs even when sha256 is unchanged.")
def ingest_run(institution: str | None, limit: int | None, force: bool) -> None:
    from .ingest.pipeline import run_ingest
    run_ingest(institution=institution, limit=limit, force=force)


@ingest.command("infer-initials")
def ingest_infer_initials() -> None:
    """Infer initial_positions / initial_cash from snapshots minus transactions.

    Run after ``ingest run`` so positions before the earliest statement are
    represented. Idempotent — safe to re-run.
    """
    from .ingest.initials import infer_initials
    out = infer_initials()
    click.echo(f"Inferred {out['positions']} initial positions, {out['cash']} cash rows.")


@ingest.command("repair-symbols")
def ingest_repair_symbols() -> None:
    """Legacy/manual repair for already-derived synthetic instruments."""
    from .ingest.repair_symbols import repair_symbols

    out = repair_symbols()
    leading = out["leading_verbs"]
    options = out["options"]
    option_transactions = out["option_transactions"]
    positions = out["positions"]
    transactions = out["transactions"]
    direct_names = out["direct_names"]
    taxes = out["tax_withholding"]
    fund_lookups = out["fund_lookups"]
    transfers = out["transfers"]
    click.echo(
        f"Repaired {leading['repaired']} leading-verb symbols; "
        f"skipped {leading['skipped']} unresolved rows."
    )
    for ex in leading["examples"]:
        click.echo(f"  txn {ex['transaction_id']}: {ex['old_symbol']} -> {ex['new_symbol']}")
    click.echo(
        f"Backfilled {options['repaired']} option roots; "
        f"skipped {options['skipped']} unresolved option instruments."
    )
    for ex in options["examples"]:
        click.echo(f"  instrument {ex['instrument_id']}: {ex['old_symbol']} -> {ex['new_root']}")
    click.echo(
        f"Repaired {option_transactions['repaired']} option transaction instruments; "
        f"skipped {option_transactions['skipped']} unresolved option transactions."
    )
    for ex in option_transactions["examples"]:
        click.echo(f"  txn {ex['transaction_id']}: {ex['old_symbol']} -> {ex['new_symbol']}")
    click.echo(
        f"Repaired {positions['repaired']} holding snapshot symbols; "
        f"skipped {positions['skipped']} unresolved snapshots."
    )
    for ex in positions["examples"]:
        click.echo(f"  snapshot {ex['snapshot_id']}: {ex['old_symbol']} -> {ex['new_symbol']}")
    click.echo(
        f"Repaired {transactions['repaired']} transaction symbols from names/holdings; "
        f"skipped {transactions['skipped']} unresolved rows."
    )
    for ex in transactions["examples"]:
        click.echo(f"  txn {ex['transaction_id']}: {ex['old_symbol']} -> {ex['new_symbol']}")
    click.echo(
        f"Repaired {direct_names['repaired']} canonical transaction symbols from direct names; "
        f"skipped {direct_names['skipped']} unchanged rows."
    )
    for ex in direct_names["examples"]:
        click.echo(f"  txn {ex['transaction_id']}: {ex['old_symbol']} -> {ex['new_symbol']}")
    click.echo(
        f"Repaired {taxes['repaired']} tax-withholding symbols from nearby dividends; "
        f"skipped {taxes['skipped']} unresolved tax rows."
    )
    for ex in taxes["examples"]:
        click.echo(f"  txn {ex['transaction_id']}: {ex['old_symbol']} -> {ex['new_symbol']}")
    click.echo(
        f"Resolved {fund_lookups['snapshot_repaired']} fund snapshots and "
        f"{fund_lookups['transaction_repaired']} fund transactions from reviewed lookups; "
        f"pending fund-code lookups: {fund_lookups['pending_after']} "
        f"(was {fund_lookups['pending_before']})."
    )
    for ex in fund_lookups["examples"]:
        click.echo(f"  {ex['kind']} {ex['id']}: {ex['old_symbol']} -> {ex['new_symbol']}")
    click.echo(f"Repaired {transfers['repaired']} transfer directions.")
    for ex in transfers["examples"]:
        click.echo(f"  txn {ex['transaction_id']}: {ex['old_type']} -> {ex['new_type']}")


@ingest.command("reconcile")
def ingest_reconcile() -> None:
    """Rebuild transfer links, movement attribution, and reconciliation results."""
    from .ingest.reconcile import reconcile_after_ingest

    out = reconcile_after_ingest()
    transfers = out["transfers"]
    positions = out["positions"]
    results = out["results"]
    result_sections = (
        results["positions"],
        results["cash"],
        results["statement_totals"],
    )
    result_count = sum(section.get("results", 0) for section in result_sections)
    unresolved = sum(
        section.get("unexplained_residual", 0) for section in result_sections
    )
    incomplete = sum(
        section.get("incomplete_input", 0) for section in result_sections
    )
    click.echo(
        f"Linked {transfers['matched']} transfer pairs "
        f"({transfers['ambiguous']} ambiguous skipped)."
    )
    click.echo(
        f"Rebuilt {positions['links']} position-to-transaction links "
        f"across {positions['snapshots']} snapshots."
    )
    click.echo(
        f"Rebuilt {result_count} reconciliation results "
        f"({unresolved} unexplained residuals, {incomplete} incomplete inputs)."
    )


# -------------------------------------------------------------------------- mcp
@main.group("mcp")
def mcp_group() -> None:
    """Model Context Protocol server for AI-agent control."""


@mcp_group.command("serve")
def mcp_serve() -> None:
    """Run the Ledger MCP server over stdio."""
    try:
        from .mcp_server import serve as serve_mcp
        serve_mcp()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


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


@market.command("refresh-profiles")
def market_refresh_profiles() -> None:
    from .market.extras import refresh_profiles
    refresh_profiles()


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


@market.command("refresh-benchmarks")
@click.option("--symbol", "symbols", multiple=True,
              help="Benchmark symbols. Default: SPY QQQ DIA IWM TLT GLD VTI ACWI.")
@click.option("--lookback-years", type=int, default=15)
def market_refresh_benchmarks(symbols: tuple[str, ...], lookback_years: int) -> None:
    """Scrape benchmark indices/ETFs (not in our holdings) for RRG, charts, etc."""
    from .market.scrape import refresh_market_data
    bms = list(symbols) or ["SPY", "QQQ", "DIA", "IWM", "TLT", "GLD", "VTI", "ACWI"]
    refresh_market_data(symbols=bms, lookback_years=lookback_years)


@market.command("refresh-all")
@click.option("--lookback-years", type=int, default=15)
def market_refresh_all(lookback_years: int) -> None:
    """Run prices + dividends + splits + financials + earnings + FX."""
    from .market.extras import (
        refresh_dividends,
        refresh_earnings,
        refresh_financials,
        refresh_fx,
        refresh_profiles,
        refresh_splits,
    )
    from .market.scrape import refresh_market_data
    refresh_market_data(lookback_years=lookback_years)
    refresh_profiles()
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


if __name__ == "__main__":
    main()
