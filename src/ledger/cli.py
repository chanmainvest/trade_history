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


# ----------------------------------------------------------------------- shadow
@main.group("shadow")
def shadow() -> None:
    """Build, review, and explicitly cut over a shadow ledger."""


@shadow.command("build")
@click.option(
    "--source-db",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help="Read-only source ledger. Defaults to the active profile ledger.",
)
@click.option(
    "--target-db",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Fresh shadow ledger path. Defaults to data/ledger.vnext.sqlite.",
)
@click.option(
    "--statements-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    default=None,
    help="PDF tree to rebuild. Defaults to the active profile statements directory.",
)
@click.option(
    "--report",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Redacted comparison report path. Defaults beside the shadow database.",
)
@click.option(
    "--replace",
    is_flag=True,
    help="Retain an existing shadow as a timestamped backup before publishing a new one.",
)
@click.option(
    "--verify-reproducible/--no-verify-reproducible",
    default=True,
    show_default=True,
    help="Require two clean shadow builds to have the same content fingerprint.",
)
def shadow_build(
    source_db: Path | None,
    target_db: Path | None,
    statements_dir: Path | None,
    report: Path | None,
    replace: bool,
    verify_reproducible: bool,
) -> None:
    """Rebuild a new ledger without modifying the live source database."""
    from .shadow import build_shadow

    try:
        result = build_shadow(
            source_db=source_db or config.SQLITE_PATH,
            target_db=target_db,
            statements_dir=statements_dir,
            report_path=report,
            replace=replace,
            verify_reproducible=verify_reproducible,
        )
    except (FileExistsError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    target_name = result["target_db_name"]
    reproducibility = result["reproducibility"]["status"]
    click.echo(f"Shadow ledger ready: {target_name}")
    click.echo(f"Reproducibility: {reproducibility}")
    click.echo(f"Comparison report: {result['report_path']}")
    click.echo("No cutover was performed. Complete manual review, then use `ledger shadow sign-off`.")


@shadow.command("sign-off")
@click.option(
    "--report",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help="Shadow comparison report. Defaults beside data/ledger.vnext.sqlite.",
)
@click.option("--reviewer", required=True, help="Human reviewer identity recorded in the local report.")
@click.option("--confirmation", required=True, help="Human review confirmation recorded in the local report.")
@click.option(
    "--acknowledge-unmapped",
    is_flag=True,
    help="Required when the report has curated annotations that could not be mapped safely.",
)
def shadow_sign_off(
    report: Path | None,
    reviewer: str,
    confirmation: str,
    acknowledge_unmapped: bool,
) -> None:
    """Record human review approval; this never modifies a live database."""
    from .shadow import sign_off_report

    report_path = report or (config.DATA_DIR / "ledger.vnext.report.json")
    try:
        sign_off_report(
            report_path,
            reviewer=reviewer,
            confirmation=confirmation,
            acknowledge_unmapped=acknowledge_unmapped,
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Manual review sign-off recorded in {report_path}.")
    click.echo("Cutover remains a separate, explicit command.")


@shadow.command("cutover")
@click.option(
    "--source-db",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help="Current live ledger. Defaults to the active profile ledger.",
)
@click.option(
    "--shadow-db",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help="Signed-off shadow ledger. Defaults to data/ledger.vnext.sqlite.",
)
@click.option(
    "--report",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help="Signed-off comparison report. Defaults beside the shadow database.",
)
@click.option("--backend-stopped", is_flag=True, help="Acknowledge that no backend is using the live database.")
@click.option("--confirm-live-db", required=True, help="Must exactly match the live database filename.")
def shadow_cutover(
    source_db: Path | None,
    shadow_db: Path | None,
    report: Path | None,
    backend_stopped: bool,
    confirm_live_db: str,
) -> None:
    """Create a timestamped backup and atomically switch to a signed-off shadow."""
    from .shadow import cutover_shadow

    live = source_db or config.SQLITE_PATH
    shadow_path = shadow_db or (config.DATA_DIR / "ledger.vnext.sqlite")
    report_path = report or (config.DATA_DIR / "ledger.vnext.report.json")
    try:
        result = cutover_shadow(
            source_db=live,
            shadow_db=shadow_path,
            report_path=report_path,
            backend_stopped=backend_stopped,
            confirm_live_db=confirm_live_db,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Cutover complete. Backup retained at {result['backup_db']}")


@shadow.command("rollback")
@click.option(
    "--live-db",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help="Current live ledger. Defaults to the active profile ledger.",
)
@click.option("--backup-db", type=click.Path(path_type=Path, exists=True, dir_okay=False), required=True)
@click.option("--backend-stopped", is_flag=True, help="Acknowledge that no backend is using the live database.")
@click.option("--confirm-live-db", required=True, help="Must exactly match the live database filename.")
def shadow_rollback(
    live_db: Path | None,
    backup_db: Path,
    backend_stopped: bool,
    confirm_live_db: str,
) -> None:
    """Restore a retained backup without deleting it."""
    from .shadow import rollback_shadow

    live = live_db or config.SQLITE_PATH
    try:
        result = rollback_shadow(
            live_db=live,
            backup_db=backup_db,
            backend_stopped=backend_stopped,
            confirm_live_db=confirm_live_db,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Rollback complete from {result['restored_from']}")


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


@ingest.command("enrich-layout")
@click.option("--source-file-id", type=int, default=None, help="Restrict to one source ID.")
def ingest_enrich_layout(source_file_id: int | None) -> None:
    """Rebuild PDF geometry links without changing semantic ledger rows."""
    from .ingest.layout_enrichment import enrich_layout

    out = enrich_layout(source_file_id=source_file_id)
    click.echo(
        "Layout enrichment: "
        + ", ".join(f"{key}={value}" for key, value in sorted(out.items()))
    )


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


@ingest.command("resolve-instruments")
@click.option(
    "--verify-yahoo",
    is_flag=True,
    help="Send public security names/symbols to Yahoo and require price history.",
)
@click.option(
    "--llm/--no-llm",
    "use_llm",
    default=None,
    help=(
        "LLM fallback for ambiguous candidates (default: on when ZAI_API_KEY "
        "is set). Uses Z.ai GLM-5.3 unless LEDGER_LLM_BASE_URL/LEDGER_LLM_MODEL "
        "override. The model only picks among Yahoo results; deterministic "
        "currency/type/history checks still gate every mapping."
    ),
)
def ingest_resolve_instruments(verify_yahoo: bool, use_llm: bool | None) -> None:
    """Resolve catalog listing names and report Yahoo mapping status."""
    from .ingest.instrument_resolution import sync_catalog_identities

    out = sync_catalog_identities()
    click.echo(
        "Instrument resolution: "
        + ", ".join(f"{key}={value}" for key, value in sorted(out.items()))
    )
    if verify_yahoo:
        from .ingest.llm_resolution import LlmResolver, llm_api_key
        from .ingest.yahoo_resolution import verify_yahoo_identities

        llm = None
        api_key = llm_api_key()
        if use_llm is True and not api_key:
            raise click.ClickException(
                "LLM fallback requested but ZAI_API_KEY is not set "
                "(or set ZHIPUAI_API_KEY / LEDGER_LLM_BASE_URL for another "
                "OpenAI-compatible endpoint)."
            )
        if use_llm is not False and api_key:
            llm = LlmResolver(api_key)
            click.echo(f"LLM fallback enabled: {llm.describe()}")
        verified = verify_yahoo_identities(llm=llm)
        click.echo(
            "Yahoo verification: "
            + ", ".join(f"{key}={value}" for key, value in sorted(verified.items()))
        )


@ingest.command("resolve-fund-lookup")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="JSON file with reviewed fund identities (see data/fund_lookups.json).",
)
def ingest_resolve_fund_lookup(file_path: str) -> None:
    """Apply reviewed fund identities from a JSON file to the lookup table.

    The shared manual-review path for humans and agents: the JSON carries one
    entry per printed fund name/class (normalized_name, currency,
    institutions, resolved_symbol, resolved_name, evidence_url, notes), and
    every symbol comes from that reviewed record's own evidence. Statements
    never print CIBC fund codes, so a resolution is always an external,
    human-approved fact recorded here. Re-run ingest afterwards so the
    staged resolver picks the identities up.
    """
    import json

    from .db import sqlite as sqlite_db
    from .ingest.fund_lookup import apply_reviewed_lookups

    with open(file_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    entries = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise click.ClickException("JSON must be an object with an 'entries' list")
    try:
        with sqlite_db.session() as conn:
            out = apply_reviewed_lookups(conn, entries)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Applied {out['entries']} reviewed fund entries "
        f"({out['rows_updated']} rows updated, {out['rows_inserted']} inserted). "
        "Lookup status: "
        + ", ".join(f"{key}={value}" for key, value in sorted(out["status_counts"].items()))
    )


@ingest.command("apply-ticker-changes")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="JSON file with reviewed ticker changes (see data/ticker_changes.json).",
)
def ingest_apply_ticker_changes(file_path: str) -> None:
    """Apply reviewed, dated ticker changes from a JSON file.

    The shared manual-review path for humans and agents: each entry carries
    both printed symbols, asset_type/currency, the effective date, and the
    evidence that justifies the relationship (never a name match). The
    record is curated ``reviewed`` state — ingest never deletes it — and
    ``ingest reconcile`` rolls positions across it. Re-run reconcile after
    applying.
    """
    import json

    from .db import sqlite as sqlite_db
    from .ingest.ticker_change_lookup import (
        apply_reviewed_ticker_changes,
        load_ticker_change_entries,
    )

    try:
        entries = load_ticker_change_entries(file_path)
        with sqlite_db.session() as conn:
            out = apply_reviewed_ticker_changes(conn, entries)
    except (ValueError, json.JSONDecodeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Applied {out['entries']} reviewed ticker changes "
        f"({out['inserted']} inserted, {out['updated']} updated). "
        "Run 'ingest reconcile' to roll positions across them."
    )


@ingest.command("apply-symbol-normalizations")
@click.option(
    "--file",
    "file_path",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="JSON file with reviewed symbol normalizations.",
)
def ingest_apply_symbol_normalizations(file_path: str) -> None:
    """Apply reviewed printed-symbol → canonical-symbol rules.

    Each rule teaches extraction that a broker prints one instrument under
    a different adjusted symbol (e.g. TD's OCC adjusted option roots);
    future ingests resolve the printed symbol to the canonical instrument,
    and applying merges any ledger rows already extracted under the
    printed form. The printed symbol stays on record in the rule and in
    the statement raw lines. Run 'ingest reconcile' afterwards.
    """
    import json

    from .db import sqlite as sqlite_db
    from .ingest.symbol_normalizations import (
        apply_symbol_normalizations,
        load_symbol_normalizations,
    )

    try:
        entries = load_symbol_normalizations(file_path)
        with sqlite_db.session() as conn:
            out = apply_symbol_normalizations(conn, entries)
    except (ValueError, json.JSONDecodeError) as exc:
        raise click.ClickException(str(exc)) from exc
    remapped = ", ".join(
        f"{table}={count}" for table, count in sorted(out["remapped"].items())
    )
    click.echo(
        f"Applied {out['rules']} reviewed symbol normalizations "
        f"({out['rules_inserted']} inserted, {out['rules_updated']} updated; "
        f"{out['instruments_merged']} instruments merged, "
        f"{out['instruments_created']} created, {out['already_merged']} already merged, "
        f"{out['pending_extraction']} pending extraction; remapped: {remapped}). "
        "Run 'ingest reconcile' to refresh reconciliation results."
    )


@ingest.command("reconcile")
def ingest_reconcile() -> None:
    """Rebuild transfer links, movement attribution, and reconciliation results."""
    from .ingest.reconcile import reconcile_after_ingest

    out = reconcile_after_ingest()
    instrument_names = out["instrument_names"]
    transfers = out["transfers"]
    positions = out["positions"]
    results = out["results"]
    pairs = out["pairs"]
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
        f"Linked {pairs['pairs']} corporate-action leg pairs "
        f"({pairs['legs_linked']} legs); skipped {pairs['ambiguous_dates']} legs on "
        f"ambiguous dates, {pairs['unpaired_legs']} unpaired."
    )
    click.echo(
        f"Resolved {instrument_names['resolved']} name-only buy/sell transactions "
        f"from observed holdings ({instrument_names.get('ambiguous', 0)} ambiguous, "
        f"{instrument_names.get('unmatched', 0)} unmatched)."
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


@ingest.command("pair-corporate-actions")
def ingest_pair_corporate_actions() -> None:
    """Link printed corporate-action leg pairs and record exchange ratios.

    Runs automatically inside ``ingest reconcile``; this command re-runs the
    pass on its own after targeted repairs. Both legs must print quantities
    on the same date; the ratio derived from them lands in
    ``instrument_journal_pairs`` and the rollforward resolves both legs.
    Idempotent — already-linked legs are skipped.
    """
    from .db import sqlite as sqlite_db
    from .ingest.reconcile import pair_corporate_action_legs

    with sqlite_db.session() as conn:
        out = pair_corporate_action_legs(conn)
    click.echo(
        f"Linked {out['pairs']} corporate-action leg pairs "
        f"({out['legs_linked']} legs); skipped {out['ambiguous_dates']} legs on "
        f"ambiguous dates and {out['unpaired_legs']} unpaired legs."
    )


@ingest.command("audit-splits")
def ingest_audit_splits() -> None:
    """Report quantity jumps between complete checkpoints that txns don't explain.

    Read-only. Each row shows the holding, the unexplained jump, the implied
    ratio, and — when the market-data pipeline knows a split ratio near the
    checkpoint — that candidate ratio for human review.
    """
    from .db import sqlite as sqlite_db
    from .db.duckdb_store import connect as duckdb_connect
    from .ingest.reconcile import audit_split_discontinuities

    with sqlite_db.session() as conn:
        rows = audit_split_discontinuities(conn)
    splits_by_symbol: dict[str, list[tuple[str, float]]] = {}
    if rows:
        try:
            con = duckdb_connect()
            try:
                for symbol, split_date, ratio in con.execute(
                    "SELECT symbol, split_date, ratio FROM splits ORDER BY split_date"
                ).fetchall():
                    splits_by_symbol.setdefault(str(symbol).upper(), []).append(
                        (str(split_date), float(ratio))
                    )
            finally:
                con.close()
        except Exception as exc:  # market DB is optional context for the report
            click.echo(f"(market splits unavailable: {exc})")
    if not rows:
        click.echo("No unexplained checkpoint quantity jumps.")
        return
    for row in rows:
        symbol = row["instrument_key"].split("|")[-2] if "|" in row["instrument_key"] else row["instrument_key"]
        implied = row["implied_ratio"]
        candidates = ""
        known = splits_by_symbol.get(symbol.upper(), [])
        if implied and known:
            near = [
                (date, ratio)
                for date, ratio in known
                if abs(ratio - abs(implied)) <= 0.01 * abs(ratio)
            ]
            if near:
                candidates = f" candidate split {near[0][0]} 1:{near[0][1]:g}"
        implied_text = f"{implied:g}" if implied else "n/a (new holding)"
        click.echo(
            f"acct {row['account_id']} {row['currency']} {row['instrument_key']}: "
            f"{row['prior_date']} {row['prior_qty']:g} -> {row['current_date']} "
            f"{row['current_qty']:g} (explained {row['explained_delta']:g}, "
            f"implied ratio {implied_text}){candidates}"
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
@click.option("--symbol", "symbols", multiple=True,
              help="Provider symbols to (re)profile. Default: all held symbols.")
def market_refresh_profiles(symbols: tuple[str, ...]) -> None:
    from .market.extras import refresh_profiles
    refresh_profiles(symbols=list(symbols) or None)


@market.command("refresh-iv")
@click.option("--symbol", "symbols", multiple=True,
              help="Provider symbols to snapshot. Default: all held symbols.")
def market_refresh_iv(symbols: tuple[str, ...]) -> None:
    """Snapshot ATM option implied volatility into option_implied_vol."""
    from .market.extras import refresh_iv
    refresh_iv(symbols=list(symbols) or None)


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
        refresh_iv,
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
    refresh_iv()
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
