"""Ingest pipeline scaffolding. Real parsers slot in via the registry."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from .. import config
from ..db import sqlite as sqlite_db
from ..identity import canonical_statement_key, evidence_occurrence
from ..logging_setup import get_logger, jsonl_path
from ..parsers import registry  # noqa: F401  (ensures parsers register)
from ..parsers.registry import select_parser
from ..parsers.types import (
    PARSER_CONTRACT_VERSION,
    ParsedQuarantine,
    ParsedStatement,
    ParseResult,
    SourceSpan,
)
from ..parsers.validation import validate_parse_result
from ..pdf_text import PdfText, extract_pdf
from ..quantity import quantity_delta

log = get_logger("ingest")

SKIPPABLE_PARSE_STATUSES = {"ok", "partial", "skipped"}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _unchanged_source_file_id(conn, *, relpath: str, sha256: str) -> int | None:
    row = conn.execute(
        "SELECT source_file_id, parse_status FROM source_files WHERE relpath = ? AND sha256 = ?",
        (relpath, sha256),
    ).fetchone()
    if row and row["parse_status"] in SKIPPABLE_PARSE_STATUSES:
        return int(row["source_file_id"])
    return None


def _record_source_file(conn, pdf: PdfText, *, parser_name: str | None,
                        parser_version: str | None, parse_status: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO source_files
            (relpath, sha256, size_bytes, page_count, is_image_only,
             parser_name, parser_version, parsed_at, parse_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(relpath) DO UPDATE SET
            sha256        = excluded.sha256,
            size_bytes    = excluded.size_bytes,
            page_count    = excluded.page_count,
            is_image_only = excluded.is_image_only,
            parser_name   = excluded.parser_name,
            parser_version= excluded.parser_version,
            parsed_at     = excluded.parsed_at,
            parse_status  = excluded.parse_status
        RETURNING source_file_id
        """,
        (
            pdf.relpath, pdf.sha256, pdf.size_bytes, pdf.page_count,
            int(pdf.is_image_only), parser_name, parser_version,
            datetime.now(UTC).isoformat(timespec="seconds"), parse_status,
        ),
    )
    source_file_id = int(cur.fetchone()[0])
    sqlite_db.record_ingestion_run(
        conn,
        source_file_id=source_file_id,
        source_sha256=pdf.sha256,
        parser_name=parser_name,
        parser_version=parser_version,
        contract_version=PARSER_CONTRACT_VERSION,
        status=parse_status,
    )
    return source_file_id


def _write_evidence(
    conn,
    *,
    source_file_id: int,
    run_id: int,
    parser_version: str | None,
    statement_key: str,
    row_kind: str,
    row_index: int,
    raw_text: str | None,
    source_span: SourceSpan | None,
    default_rule: str,
) -> int:
    span = source_span or SourceSpan()
    return sqlite_db.upsert_source_evidence(
        conn,
        source_file_id=source_file_id,
        ingestion_run_id=run_id,
        row_kind=row_kind,
        occurrence=evidence_occurrence(statement_key, row_kind, row_index),
        raw_text=span.raw_text if span.raw_text is not None else raw_text,
        parser_version=parser_version,
        parser_rule=span.parser_rule or default_rule,
        page_number=span.page_number,
        line_number=span.line_number,
        bbox=span.bbox,
        words=span.words,
    )


def _quarantine_parts(
    item: tuple[str, str] | ParsedQuarantine,
) -> tuple[str, str, SourceSpan | None]:
    if isinstance(item, ParsedQuarantine):
        return item.raw_line, item.reason, item.source_span
    return item[0], item[1], None


def _write_statement(conn, *, source_file_id: int, institution_code: str,
                      stmt: ParsedStatement) -> None:
    inst_id = sqlite_db.upsert_institution(conn, institution_code, institution_code)
    acct_id = sqlite_db.upsert_account(
        conn,
        institution_id=inst_id,
        account_number=stmt.account.account_number,
        account_type=stmt.account.account_type,
        base_currency=stmt.account.base_currency,
    )
    run_id = sqlite_db.active_ingestion_run_id(conn, source_file_id)
    source = conn.execute(
        "SELECT relpath, sha256 FROM source_files WHERE source_file_id = ?",
        (source_file_id,),
    ).fetchone()
    source_identity = source["sha256"] or source["relpath"]
    persisted_statement_key = canonical_statement_key(
        source_identity=source_identity,
        institution_code=institution_code,
        account_number=stmt.account.account_number,
        period_start=stmt.period_start,
        period_end=stmt.period_end,
        statement_type=stmt.statement_type,
    )
    cur = conn.execute(
        """
        INSERT INTO statements (
            source_file_id, ingestion_run_id, account_id, statement_key,
            period_start, period_end, statement_type
        ) VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(source_file_id, account_id, period_start, period_end, statement_type)
        DO UPDATE SET
            ingestion_run_id = excluded.ingestion_run_id,
            statement_key = excluded.statement_key
        RETURNING statement_id
        """,
        (
            source_file_id,
            run_id,
            acct_id,
            persisted_statement_key,
            stmt.period_start,
            stmt.period_end,
            stmt.statement_type,
        ),
    )
    statement_id = cur.fetchone()[0]
    parser_version = conn.execute(
        "SELECT parser_version FROM ingestion_runs WHERE ingestion_run_id = ?",
        (run_id,),
    ).fetchone()[0]

    # Replace child rows (idempotent re-ingest).
    conn.execute(
        """
        UPDATE transactions
           SET counterpart_account_id = NULL,
               counterpart_txn_id = NULL
         WHERE statement_id = ?
            OR counterpart_txn_id IN (
                SELECT transaction_id FROM transactions WHERE statement_id = ?
            )
        """,
        (statement_id, statement_id),
    )
    conn.execute("DELETE FROM transactions WHERE statement_id = ?", (statement_id,))
    conn.execute("DELETE FROM reconciliation_results WHERE statement_id = ?", (statement_id,))
    conn.execute("DELETE FROM snapshot_sets WHERE statement_id = ?", (statement_id,))
    conn.execute("DELETE FROM annual_performance_reports WHERE statement_id = ?", (statement_id,))
    conn.execute(
        "DELETE FROM quarantine_transactions "
        "WHERE statement_id = ? OR "
        "(statement_id IS NULL AND source_file_id = ? AND account_id = ?)",
        (statement_id, source_file_id, acct_id),
    )

    snapshot_sets: dict[tuple[str, str, str], int] = {}
    for set_index, parsed_set in enumerate(stmt.snapshot_sets):
        evidence_id = None
        if parsed_set.source_span is not None:
            evidence_id = _write_evidence(
                conn,
                source_file_id=source_file_id,
                run_id=run_id,
                parser_version=parser_version,
                statement_key=persisted_statement_key,
                row_kind=f"snapshot_set_{parsed_set.section_type}",
                row_index=set_index,
                raw_text=None,
                source_span=parsed_set.source_span,
                default_rule="parser:snapshot-set",
            )
        key = (parsed_set.currency, parsed_set.section_type, parsed_set.scope_key)
        snapshot_sets[key] = sqlite_db.upsert_snapshot_set(
            conn,
            statement_id=statement_id,
            account_id=acct_id,
            as_of_date=stmt.period_end,
            currency=parsed_set.currency,
            section_type=parsed_set.section_type,
            scope_key=parsed_set.scope_key,
            completeness=parsed_set.completeness,
            evidence_id=evidence_id,
            reported_total=parsed_set.reported_total,
            validation_status=parsed_set.validation_status,
        )

    actual_scopes = {
        (position.currency, "positions", position.scope_key)
        for position in stmt.positions
    } | {
        (cash.currency, "cash", cash.scope_key)
        for cash in stmt.cash_balances
    }
    for key in sorted(actual_scopes):
        if key in snapshot_sets:
            continue
        currency, section_type, scope_key = key
        snapshot_sets[key] = sqlite_db.upsert_snapshot_set(
            conn,
            statement_id=statement_id,
            account_id=acct_id,
            as_of_date=stmt.period_end,
            currency=currency,
            section_type=section_type,
            scope_key=scope_key,
            completeness="unknown",
            evidence_id=None,
            reported_total=None,
            validation_status="warning",
        )

    for row_index, t in enumerate(stmt.transactions):
        evidence_id = _write_evidence(
            conn,
            source_file_id=source_file_id,
            run_id=run_id,
            parser_version=parser_version,
            statement_key=persisted_statement_key,
            row_kind="transaction",
            row_index=row_index,
            raw_text=t.raw_line,
            source_span=t.source_span,
            default_rule="parser:transaction",
        )
        resolution_evidence_id = None
        if t.resolution_evidence is not None:
            resolution_evidence_id = _write_evidence(
                conn,
                source_file_id=source_file_id,
                run_id=run_id,
                parser_version=parser_version,
                statement_key=persisted_statement_key,
                row_kind="transaction_resolution",
                row_index=row_index,
                raw_text=t.raw_line,
                source_span=t.resolution_evidence,
                default_rule="resolver:unspecified",
            )
        instr_id = None
        if t.instrument is not None:
            i = t.instrument
            instr_id = sqlite_db.upsert_instrument(
                conn,
                asset_type=i.asset_type, symbol=i.symbol, currency=i.currency,
                exchange=i.exchange, name=i.name,
                option_root=i.option_root, option_expiry=i.option_expiry,
                option_strike=i.option_strike, option_type=i.option_type,
                option_multiplier=i.option_multiplier,
                resolution_method=t.resolution_method,
                resolution_confidence=t.resolution_confidence,
            )
        position_effect = (
            t.position_delta
            if t.position_delta is not None
            else quantity_delta(t.txn_type, t.quantity)
            if t.quantity is not None
            else None
        )
        cash_effect = t.cash_delta if t.cash_delta is not None else t.net_amount
        conn.execute(
            """INSERT INTO transactions
            (account_id, statement_id, source_file_id, ingestion_run_id,
             evidence_id, trade_date, settle_date, txn_type, instrument_id,
             quantity, position_delta, price, gross_amount, commission,
             other_fees, net_amount, cash_delta, cash_effective_date, currency,
             tax_country, tax_rate, description, raw_line, parser_confidence,
             resolution_method, resolution_confidence, resolution_evidence_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                acct_id, statement_id, source_file_id, run_id, evidence_id,
                t.trade_date, t.settle_date, t.txn_type, instr_id, t.quantity,
                position_effect, t.price, t.gross_amount, t.commission,
                t.other_fees, t.net_amount, cash_effect,
                t.cash_effective_date or t.settle_date or t.trade_date,
                t.currency, t.tax_country, t.tax_rate, t.description,
                t.raw_line, t.parser_confidence, t.resolution_method,
                t.resolution_confidence, resolution_evidence_id,
            ),
        )

    for row_index, p in enumerate(stmt.positions):
        evidence_id = _write_evidence(
            conn,
            source_file_id=source_file_id,
            run_id=run_id,
            parser_version=parser_version,
            statement_key=persisted_statement_key,
            row_kind="position",
            row_index=row_index,
            raw_text=p.raw_line,
            source_span=p.source_span,
            default_rule="parser:position",
        )
        i = p.instrument
        instr_id = sqlite_db.upsert_instrument(
            conn,
            asset_type=i.asset_type, symbol=i.symbol, currency=i.currency,
            exchange=i.exchange, name=i.name,
            option_root=i.option_root, option_expiry=i.option_expiry,
            option_strike=i.option_strike, option_type=i.option_type,
            option_multiplier=i.option_multiplier,
        )
        conn.execute(
            """INSERT INTO position_snapshots
            (statement_id, snapshot_set_id, evidence_id, account_id, as_of_date,
             instrument_id, quantity, avg_cost, book_value, market_price,
             market_value, unrealized_pnl, currency, raw_line)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(snapshot_set_id, instrument_id) DO UPDATE SET
                evidence_id   = excluded.evidence_id,
                quantity      = excluded.quantity,
                avg_cost      = excluded.avg_cost,
                book_value    = excluded.book_value,
                market_price  = excluded.market_price,
                market_value  = excluded.market_value,
                unrealized_pnl= excluded.unrealized_pnl,
                raw_line      = excluded.raw_line""",
            (
                statement_id,
                snapshot_sets[(p.currency, "positions", p.scope_key)],
                evidence_id, acct_id, stmt.period_end, instr_id, p.quantity,
                p.avg_cost, p.book_value, p.market_price, p.market_value,
                p.unrealized_pnl, p.currency, p.raw_line,
            ),
        )

    for row_index, c in enumerate(stmt.cash_balances):
        evidence_id = _write_evidence(
            conn,
            source_file_id=source_file_id,
            run_id=run_id,
            parser_version=parser_version,
            statement_key=persisted_statement_key,
            row_kind="cash",
            row_index=row_index,
            raw_text=c.raw_line,
            source_span=c.source_span,
            default_rule="parser:cash",
        )
        conn.execute(
            """INSERT INTO cash_balances
            (statement_id, snapshot_set_id, evidence_id, account_id, as_of_date,
             currency, opening_balance, closing_balance, raw_line)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(snapshot_set_id) DO UPDATE SET
                evidence_id = excluded.evidence_id,
                opening_balance = excluded.opening_balance,
                closing_balance = excluded.closing_balance,
                raw_line = excluded.raw_line""",
            (
                statement_id,
                snapshot_sets[(c.currency, "cash", c.scope_key)],
                evidence_id, acct_id, stmt.period_end, c.currency,
                c.opening_balance, c.closing_balance, c.raw_line,
            ),
        )

    for perf in stmt.annual_performance:
        conn.execute(
            """INSERT INTO annual_performance_reports
            (statement_id, account_id, currency, period_start, period_end, since_date,
             beginning_market_value, deposits_transfers_in, withdrawals_transfers_out,
             net_investment_return, ending_market_value, money_weighted_1y,
             money_weighted_3y, money_weighted_5y, money_weighted_10y,
             money_weighted_since)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(statement_id, currency) DO UPDATE SET
                period_start = excluded.period_start,
                period_end = excluded.period_end,
                since_date = excluded.since_date,
                beginning_market_value = excluded.beginning_market_value,
                deposits_transfers_in = excluded.deposits_transfers_in,
                withdrawals_transfers_out = excluded.withdrawals_transfers_out,
                net_investment_return = excluded.net_investment_return,
                ending_market_value = excluded.ending_market_value,
                money_weighted_1y = excluded.money_weighted_1y,
                money_weighted_3y = excluded.money_weighted_3y,
                money_weighted_5y = excluded.money_weighted_5y,
                money_weighted_10y = excluded.money_weighted_10y,
                money_weighted_since = excluded.money_weighted_since""",
            (
                statement_id, acct_id, perf.currency, perf.period_start,
                perf.period_end, perf.since_date, perf.beginning_market_value,
                perf.deposits_transfers_in, perf.withdrawals_transfers_out,
                perf.net_investment_return, perf.ending_market_value,
                perf.money_weighted_1y, perf.money_weighted_3y,
                perf.money_weighted_5y, perf.money_weighted_10y,
                perf.money_weighted_since,
            ),
        )

    for row_index, item in enumerate(stmt.quarantine):
        raw, reason, span = _quarantine_parts(item)
        evidence_id = _write_evidence(
            conn,
            source_file_id=source_file_id,
            run_id=run_id,
            parser_version=parser_version,
            statement_key=persisted_statement_key,
            row_kind="quarantine",
            row_index=row_index,
            raw_text=raw,
            source_span=span,
            default_rule="parser:quarantine",
        )
        conn.execute(
            """
            INSERT INTO quarantine_transactions(
                source_file_id, ingestion_run_id, statement_id, account_id,
                evidence_id, occurrence, raw_line, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_file_id,
                run_id,
                statement_id,
                acct_id,
                evidence_id,
                evidence_occurrence(persisted_statement_key, "quarantine", row_index),
                raw,
                reason,
            ),
        )


def run_ingest(*, institution: str | None = None, limit: int | None = None, force: bool = False) -> None:
    sqlite_db.init_db()
    seen = 0
    skipped_log = config.LOG_DIR / "skipped_pdfs.log"
    quarantine_jsonl = jsonl_path("quarantine")

    with skipped_log.open("a", encoding="utf-8") as skipf, \
         quarantine_jsonl.open("a", encoding="utf-8") as qf, \
         sqlite_db.session() as conn:

        for folder in sorted(config.STATEMENTS_DIR.iterdir()):
            if not folder.is_dir():
                continue
            if institution and folder.name != institution:
                continue
            inst_code = config.INSTITUTIONS.get(folder.name, folder.name)

            for p in sorted(folder.glob("*.pdf")):
                if limit is not None and seen >= limit:
                    return
                seen += 1
                relpath = p.relative_to(config.ROOT).as_posix()
                if not force:
                    sha = _sha256_file(p)
                    if _unchanged_source_file_id(conn, relpath=relpath, sha256=sha) is not None:
                        log.info("Skipping unchanged %s/%s", folder.name, p.name)
                        continue
                log.info("Reading %s/%s", folder.name, p.name)
                try:
                    pdf = extract_pdf(p, repo_root=config.ROOT)
                except Exception as e:
                    log.exception("extract failed: %s -> %s", p, e)
                    continue

                if pdf.is_image_only:
                    skipf.write(f"{pdf.relpath}\n")
                    _record_source_file(conn, pdf, parser_name=None,
                                        parser_version=None, parse_status="skipped")
                    continue

                parser = select_parser(folder.name, pdf)
                if parser is None:
                    log.warning("no parser claimed %s", pdf.relpath)
                    _record_source_file(conn, pdf, parser_name=None,
                                        parser_version=None, parse_status="failed")
                    continue

                try:
                    result: ParseResult = parser.parse(pdf)
                except Exception as e:
                    log.exception("parser %s crashed on %s: %s", parser.NAME, p, e)
                    _record_source_file(conn, pdf, parser_name=parser.NAME,
                                        parser_version=parser.VERSION,
                                        parse_status="failed")
                    continue

                validation = validate_parse_result(result)
                if not validation.is_valid:
                    _record_source_file(
                        conn,
                        pdf,
                        parser_name=parser.NAME,
                        parser_version=parser.VERSION,
                        parse_status="failed",
                    )
                    log.error(
                        "parser output validation failed for %s: %d error(s), %d warning(s)",
                        pdf.relpath,
                        len(validation.errors),
                        len(validation.warnings),
                    )
                    for issue in validation.errors:
                        log.error("%s: %s", issue.code, issue.message)
                    continue
                if validation.warnings:
                    log.warning(
                        "parser output for %s has %d contract warning(s)",
                        pdf.relpath,
                        len(validation.warnings),
                    )

                status = "ok" if result.statements and not result.errors else (
                    "partial" if result.statements else "failed"
                )
                sf_id = _record_source_file(conn, pdf,
                                            parser_name=parser.NAME,
                                            parser_version=parser.VERSION,
                                            parse_status=status)
                for stmt in result.statements:
                    _write_statement(conn, source_file_id=sf_id,
                                     institution_code=inst_code, stmt=stmt)
                    for raw, reason in stmt.quarantine:
                        qf.write(json.dumps({
                            "relpath": pdf.relpath,
                            "account": stmt.account.account_number,
                            "raw": raw, "reason": reason,
                        }) + "\n")
                for err in result.errors:
                    log.warning("%s: %s", pdf.relpath, err)
    from .repair_symbols import repair_symbols

    repair_summary = repair_symbols()
    log.info("Symbol repair after ingest: %s", repair_summary)
    from .reconcile import reconcile_after_ingest

    reconcile_summary = reconcile_after_ingest()
    log.info("Reconciliation after ingest: %s", reconcile_summary)
    log.info("Ingest finished. %d PDFs scanned.", seen)
