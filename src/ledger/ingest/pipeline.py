"""Ingest pipeline scaffolding. Real parsers slot in via the registry."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .. import config
from ..db import sqlite as sqlite_db
from ..identity import canonical_statement_key, evidence_occurrence
from ..logging_setup import get_logger
from ..parsers import registry  # noqa: F401  (ensures parsers register)
from ..parsers.registry import all_parsers, select_parser
from ..parsers.types import (
    PARSER_CONTRACT_VERSION,
    ParsedQuarantine,
    ParsedStatement,
    ParseResult,
    SourceSpan,
)
from ..parsers.validation import validate_parse_result
from ..pdf_text import PdfText, extract_pdf
from ..quantity import normalized_position_delta
from .identity_resolution import resolve_parse_result, resolver_cache_version

log = get_logger("ingest")

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _current_parser_version(parser_name: str | None) -> str | None:
    if parser_name is None:
        return None
    matches = [parser.VERSION for parser in all_parsers() if parser.NAME == parser_name]
    return matches[0] if len(matches) == 1 else None


def _unchanged_source_file_id(conn, *, relpath: str, sha256: str) -> int | None:
    """Return a source only when its *active* extraction contract is current.

    A source path alone is not a cache key.  A parser/contract/schema/resolver
    change must cause a reparse without requiring an operator to remember
    ``--force``.
    """
    resolver_version = resolver_cache_version(conn)
    active = conn.execute(
        """
        SELECT sf.source_file_id, ir.parser_name, ir.parser_version,
               ir.contract_version, ir.schema_version, ir.resolver_version
          FROM source_files sf
          JOIN ingestion_runs ir ON ir.ingestion_run_id = sf.active_ingestion_run_id
         WHERE sf.relpath = ?
           AND ir.source_sha256 = ?
           AND ir.status = 'active'
        """,
        (relpath, sha256),
    ).fetchone()
    if active is not None:
        if (
            _current_parser_version(active["parser_name"]) == active["parser_version"]
            and active["contract_version"] == PARSER_CONTRACT_VERSION
            and active["schema_version"] == sqlite_db.SCHEMA_VERSION
            and active["resolver_version"] == resolver_version
        ):
            return int(active["source_file_id"])
        return None

    # Image-only inputs have no active ledger data.  Cache only an unchanged
    # terminal skip; failures intentionally retry so an extractor/parser fix
    # can recover them automatically.
    skipped = conn.execute(
        """
        SELECT sf.source_file_id, ir.contract_version, ir.schema_version,
               ir.resolver_version
          FROM source_files sf
          JOIN ingestion_runs ir ON ir.ingestion_run_id = (
              SELECT newer.ingestion_run_id
                FROM ingestion_runs newer
               WHERE newer.source_file_id = sf.source_file_id
               ORDER BY newer.ingestion_run_id DESC
               LIMIT 1
          )
         WHERE sf.relpath = ?
           AND sf.active_ingestion_run_id IS NULL
           AND ir.source_sha256 = ?
           AND ir.status = 'skipped'
           AND ir.parser_name IS NULL
        """,
        (relpath, sha256),
    ).fetchone()
    if skipped is not None and (
        skipped["contract_version"] == PARSER_CONTRACT_VERSION
        and skipped["schema_version"] == sqlite_db.SCHEMA_VERSION
        and skipped["resolver_version"] == resolver_version
    ):
        return int(skipped["source_file_id"])
    return None


def _ensure_source_file(conn, pdf: PdfText) -> int:
    conn.execute(
        """
        INSERT INTO source_files(relpath, parse_status)
        VALUES (?, 'pending')
        ON CONFLICT(relpath) DO NOTHING
        """,
        (pdf.relpath,),
    )
    row = conn.execute(
        "SELECT source_file_id FROM source_files WHERE relpath = ?",
        (pdf.relpath,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"could not create source record for {pdf.relpath}")
    return int(row["source_file_id"])


def _update_source_metadata(
    conn,
    *,
    source_file_id: int,
    pdf: PdfText,
    parser_name: str | None,
    parser_version: str | None,
    parse_status: str,
) -> None:
    conn.execute(
        """
        UPDATE source_files
           SET sha256 = ?, size_bytes = ?, page_count = ?, is_image_only = ?,
               parser_name = ?, parser_version = ?, parsed_at = ?, parse_status = ?
         WHERE source_file_id = ?
        """,
        (
            pdf.sha256,
            pdf.size_bytes,
            pdf.page_count,
            int(pdf.is_image_only),
            parser_name,
            parser_version,
            datetime.now(UTC).isoformat(timespec="seconds"),
            parse_status,
            source_file_id,
        ),
    )


def _record_source_file(
    conn,
    pdf: PdfText,
    *,
    parser_name: str | None,
    parser_version: str | None,
    parse_status: str,
    error_summary: str | None = None,
) -> int:
    """Record a non-staged attempt, preserving an existing active source.

    This remains a compatibility helper for direct writer tests and for failed
    or skipped attempts.  Successful production ingestion uses
    :func:`activate_source_result` instead.
    """
    source_file_id = _ensure_source_file(conn, pdf)
    active = conn.execute(
        "SELECT active_ingestion_run_id FROM source_files WHERE source_file_id = ?",
        (source_file_id,),
    ).fetchone()["active_ingestion_run_id"]
    if parse_status in {"ok", "partial"} or active is None:
        _update_source_metadata(
            conn,
            source_file_id=source_file_id,
            pdf=pdf,
            parser_name=parser_name,
            parser_version=parser_version,
            parse_status=parse_status,
        )
    sqlite_db.record_ingestion_run(
        conn,
        source_file_id=source_file_id,
        source_sha256=pdf.sha256,
        parser_name=parser_name,
        parser_version=parser_version,
        contract_version=PARSER_CONTRACT_VERSION,
        status=parse_status,
        error_summary=error_summary,
        resolver_version=resolver_cache_version(conn),
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


def _write_statement(
    conn,
    *,
    source_file_id: int,
    institution_code: str,
    stmt: ParsedStatement,
    ingestion_run_id: int | None = None,
) -> None:
    inst_id = sqlite_db.upsert_institution(conn, institution_code, institution_code)
    acct_id = sqlite_db.upsert_account(
        conn,
        institution_id=inst_id,
        account_number=stmt.account.account_number,
        account_type=stmt.account.account_type,
        base_currency=stmt.account.base_currency,
    )
    run_id = (
        ingestion_run_id
        if ingestion_run_id is not None
        else sqlite_db.active_ingestion_run_id(conn, source_file_id)
    )
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
                resolution_method=t.resolution_method or i.resolution_method,
                resolution_confidence=(
                    t.resolution_confidence
                    if t.resolution_confidence is not None
                    else i.resolution_confidence
                ),
            )
        position_effect = (
            t.position_delta
            if t.position_delta is not None
            else normalized_position_delta(t.txn_type, t.quantity)
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
            resolution_method=i.resolution_method,
            resolution_confidence=i.resolution_confidence,
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


def _content_counts(result: ParseResult) -> dict[str, int]:
    return {
        "annual_performance": sum(len(stmt.annual_performance) for stmt in result.statements),
        "cash_balances": sum(len(stmt.cash_balances) for stmt in result.statements),
        "positions": sum(len(stmt.positions) for stmt in result.statements),
        "quarantine": sum(len(stmt.quarantine) for stmt in result.statements),
        "snapshot_sets": sum(len(stmt.snapshot_sets) for stmt in result.statements),
        "statements": len(result.statements),
        "transactions": sum(len(stmt.transactions) for stmt in result.statements),
    }


def _content_hash(result: ParseResult) -> str:
    """Hash resolved parser output without database IDs or wall-clock values."""
    payload = json.dumps(
        asdict(result),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assert_unique_stage_keys(
    result: ParseResult,
    *,
    source_identity: str,
    institution_code: str,
) -> None:
    """Reject duplicate statement/source-row identities before any DB writes."""
    statement_keys: set[str] = set()
    source_row_keys: set[tuple[str, str, int]] = set()
    for statement in result.statements:
        statement_key = canonical_statement_key(
            source_identity=source_identity,
            institution_code=institution_code,
            account_number=statement.account.account_number,
            period_start=statement.period_start,
            period_end=statement.period_end,
            statement_type=statement.statement_type,
        )
        if statement_key in statement_keys:
            raise ValueError(f"duplicate staged statement key: {statement_key}")
        statement_keys.add(statement_key)

        row_indexes = [
            ("transaction", index) for index in range(len(statement.transactions))
        ] + [
            ("position", index) for index in range(len(statement.positions))
        ] + [
            ("cash", index) for index in range(len(statement.cash_balances))
        ] + [
            ("quarantine", index) for index in range(len(statement.quarantine))
        ] + [
            (f"snapshot_set_{snapshot.section_type}", index)
            for index, snapshot in enumerate(statement.snapshot_sets)
        ]
        for row_kind, row_index in row_indexes:
            key = (statement_key, row_kind, row_index)
            if key in source_row_keys:
                raise ValueError(f"duplicate staged source row key: {key}")
            source_row_keys.add(key)


def activate_source_result(
    conn,
    *,
    pdf: PdfText,
    institution_code: str,
    parser_name: str,
    parser_version: str,
    result: ParseResult,
) -> dict[str, object]:
    """Stage, activate, and replace one validated source in one savepoint.

    SQLite's v6 statement/evidence identities cannot coexist for an old and a
    replacement extraction.  The old derived run is therefore removed only
    inside this uncommitted savepoint, immediately before the new rows are
    written.  Readers see either the previous committed extraction or the new
    fully activated extraction; an exception rolls every operation back.
    """
    if result.status != "parsed":
        raise ValueError("cannot activate a skipped parser result")
    validation = validate_parse_result(result)
    if not validation.is_valid:
        messages = "; ".join(issue.message for issue in validation.errors[:3])
        raise ValueError(f"cannot activate invalid parser result: {messages}")
    if result.parser_name != parser_name or result.parser_version != parser_version:
        raise ValueError(
            "cannot activate parser result whose declared name/version does not match "
            "the selected parser"
        )
    if not result.statements:
        raise ValueError("cannot activate a parser result with no statements")
    _assert_unique_stage_keys(
        result,
        source_identity=pdf.sha256 or pdf.relpath,
        institution_code=institution_code,
    )

    conn.execute("SAVEPOINT source_activation")
    try:
        source_file_id = _ensure_source_file(conn, pdf)
        resolver_version = resolver_cache_version(conn)
        run_id = sqlite_db.begin_ingestion_run(
            conn,
            source_file_id=source_file_id,
            source_sha256=pdf.sha256,
            parser_name=parser_name,
            parser_version=parser_version,
            contract_version=PARSER_CONTRACT_VERSION,
            resolver_version=resolver_version,
            status="validated",
        )
        resolution_counts = resolve_parse_result(
            conn,
            institution_code=institution_code,
            result=result,
        )
        counts = _content_counts(result)
        content_hash = _content_hash(result)

        prior_run = conn.execute(
            "SELECT active_ingestion_run_id FROM source_files WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()["active_ingestion_run_id"]
        if prior_run is not None:
            sqlite_db.discard_derived_ingestion_run(conn, int(prior_run))

        # The source metadata is the active-output mirror.  This update happens
        # only after all in-memory validation/resolution succeeds and remains
        # inside the same savepoint as child writes.
        _update_source_metadata(
            conn,
            source_file_id=source_file_id,
            pdf=pdf,
            parser_name=parser_name,
            parser_version=parser_version,
            parse_status="pending",
        )
        for statement in result.statements:
            _write_statement(
                conn,
                source_file_id=source_file_id,
                institution_code=institution_code,
                stmt=statement,
                ingestion_run_id=run_id,
            )

        sqlite_db.activate_ingestion_run(
            conn,
            source_file_id=source_file_id,
            ingestion_run_id=run_id,
            content_counts=counts,
            content_hash=content_hash,
        )
        conn.execute(
            "UPDATE source_files SET parse_status = 'ok' WHERE source_file_id = ?",
            (source_file_id,),
        )
        conn.execute("RELEASE SAVEPOINT source_activation")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT source_activation")
        conn.execute("RELEASE SAVEPOINT source_activation")
        raise

    return {
        "source_file_id": source_file_id,
        "ingestion_run_id": run_id,
        "content_counts": counts,
        "content_hash": content_hash,
        "resolution_counts": resolution_counts,
    }


def export_active_ingestion_logs(
    *,
    path: Path | str | None = None,
    log_dir: Path | None = None,
) -> dict[str, int]:
    """Regenerate non-sensitive active-ingestion audit indexes deterministically."""
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    output_dir = log_dir or config.LOG_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    sqlite_db.init_db(db_path)
    with sqlite_db.session(db_path) as conn:
        quarantine_rows = conn.execute(
            """
            SELECT sf.relpath, q.source_file_id, q.ingestion_run_id,
                   q.statement_id, q.account_id, q.evidence_id, q.occurrence,
                   q.reason
              FROM quarantine_transactions q
              JOIN source_files sf ON sf.source_file_id = q.source_file_id
             WHERE sf.active_ingestion_run_id = q.ingestion_run_id
             ORDER BY sf.relpath, q.statement_id, q.occurrence, q.quarantine_id
            """
        ).fetchall()
        skipped_rows = conn.execute(
            """
            SELECT sf.relpath, ir.source_file_id, ir.ingestion_run_id,
                   ir.source_sha256
              FROM ingestion_runs ir
              JOIN source_files sf ON sf.source_file_id = ir.source_file_id
             WHERE ir.ingestion_run_id = (
                    SELECT newest.ingestion_run_id
                      FROM ingestion_runs newest
                     WHERE newest.source_file_id = ir.source_file_id
                     ORDER BY newest.ingestion_run_id DESC
                     LIMIT 1
                   )
               AND ir.status = 'skipped'
             ORDER BY sf.relpath
            """
        ).fetchall()
        attempt_rows = conn.execute(
            """
            SELECT sf.relpath, ir.source_file_id, ir.ingestion_run_id,
                   ir.source_sha256, ir.parser_name, ir.parser_version,
                   ir.contract_version, ir.schema_version, ir.resolver_version,
                   ir.status, ir.started_at, ir.finished_at, ir.activated_at,
                   ir.content_counts_json, ir.content_hash
              FROM ingestion_runs ir
              JOIN source_files sf ON sf.source_file_id = ir.source_file_id
             ORDER BY sf.relpath, ir.ingestion_run_id
            """
        ).fetchall()

    quarantine_path = output_dir / "quarantine.jsonl"
    quarantine_lines = [
        json.dumps(dict(row), sort_keys=True, separators=(",", ":"))
        for row in quarantine_rows
    ]
    quarantine_path.write_text(
        "\n".join(quarantine_lines) + ("\n" if quarantine_lines else ""),
        encoding="utf-8",
    )
    skipped_path = output_dir / "skipped_pdfs.log"
    skipped_lines = [
        json.dumps(dict(row), sort_keys=True, separators=(",", ":"))
        for row in skipped_rows
    ]
    skipped_path.write_text(
        "\n".join(skipped_lines) + ("\n" if skipped_lines else ""),
        encoding="utf-8",
    )
    attempts_path = output_dir / "ingestion_attempts.jsonl"
    attempt_lines = [
        json.dumps(dict(row), sort_keys=True, separators=(",", ":"))
        for row in attempt_rows
    ]
    attempts_path.write_text(
        "\n".join(attempt_lines) + ("\n" if attempt_lines else ""),
        encoding="utf-8",
    )
    return {
        "attempts": len(attempt_rows),
        "quarantine": len(quarantine_rows),
        "skipped": len(skipped_rows),
    }


def _record_attempt(
    pdf: PdfText,
    *,
    parser_name: str | None,
    parser_version: str | None,
    status: str,
    error_summary: str | None = None,
) -> None:
    """Persist a failed/skipped attempt without touching an active extraction."""
    with sqlite_db.session() as conn:
        _record_source_file(
            conn,
            pdf,
            parser_name=parser_name,
            parser_version=parser_version,
            parse_status=status,
            error_summary=error_summary,
        )


def run_ingest(*, institution: str | None = None, limit: int | None = None, force: bool = False) -> None:
    sqlite_db.init_db()
    seen = 0
    activated = 0
    stopped = False

    for folder in sorted(config.STATEMENTS_DIR.iterdir()):
        if not folder.is_dir():
            continue
        if institution and folder.name != institution:
            continue
        inst_code = config.INSTITUTIONS.get(folder.name, folder.name)

        for path in sorted(folder.glob("*.pdf")):
            if limit is not None and seen >= limit:
                stopped = True
                break
            seen += 1
            relpath = path.relative_to(config.ROOT).as_posix()
            sha = _sha256_file(path)
            if not force:
                with sqlite_db.session() as conn:
                    cached = _unchanged_source_file_id(conn, relpath=relpath, sha256=sha)
                if cached is not None:
                    log.info("Skipping current active extraction %s/%s", folder.name, path.name)
                    continue

            log.info("Reading %s/%s", folder.name, path.name)
            try:
                pdf = extract_pdf(path, repo_root=config.ROOT)
            except Exception as exc:
                # Hashing succeeded above, so this is a true extraction attempt
                # rather than an unknown input.  Keep the last good run active.
                pdf = PdfText(
                    relpath=relpath,
                    page_count=0,
                    pages=[],
                    sha256=sha,
                    size_bytes=path.stat().st_size,
                )
                log.exception("extract failed: %s -> %s", path, exc)
                _record_attempt(
                    pdf,
                    parser_name=None,
                    parser_version=None,
                    status="failed",
                    error_summary=f"extract failed: {type(exc).__name__}: {exc}"[:1000],
                )
                continue

            if pdf.is_image_only:
                _record_attempt(
                    pdf,
                    parser_name=None,
                    parser_version=None,
                    status="skipped",
                    error_summary="image-only source; OCR is not implemented",
                )
                continue

            parser = select_parser(folder.name, pdf)
            if parser is None:
                log.warning("no parser claimed %s", pdf.relpath)
                _record_attempt(
                    pdf,
                    parser_name=None,
                    parser_version=None,
                    status="failed",
                    error_summary="no registered parser claimed source",
                )
                continue

            try:
                result: ParseResult = parser.parse(pdf)
            except Exception as exc:
                log.exception("parser %s crashed on %s: %s", parser.NAME, path, exc)
                _record_attempt(
                    pdf,
                    parser_name=parser.NAME,
                    parser_version=parser.VERSION,
                    status="failed",
                    error_summary=f"parser crash: {type(exc).__name__}: {exc}"[:1000],
                )
                continue

            if result.status == "skipped":
                _record_attempt(
                    pdf,
                    parser_name=parser.NAME,
                    parser_version=parser.VERSION,
                    status="skipped",
                    error_summary=result.skip_reason,
                )
                log.info("Skipped %s: %s", pdf.relpath, result.skip_reason)
                continue

            validation = validate_parse_result(result)
            if not validation.is_valid:
                summary = "; ".join(
                    f"{issue.code}: {issue.message}" for issue in validation.errors[:3]
                )
                _record_attempt(
                    pdf,
                    parser_name=parser.NAME,
                    parser_version=parser.VERSION,
                    status="failed",
                    error_summary=summary[:1000],
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

            try:
                with sqlite_db.session() as conn:
                    activation = activate_source_result(
                        conn,
                        pdf=pdf,
                        institution_code=inst_code,
                        parser_name=parser.NAME,
                        parser_version=parser.VERSION,
                        result=result,
                    )
            except Exception as exc:
                log.exception("activation failed for %s: %s", pdf.relpath, exc)
                _record_attempt(
                    pdf,
                    parser_name=parser.NAME,
                    parser_version=parser.VERSION,
                    status="failed",
                    error_summary=f"activation failed: {type(exc).__name__}: {exc}"[:1000],
                )
                continue
            activated += 1
            log.info(
                "Activated %s run=%s hash=%s resolutions=%s",
                pdf.relpath,
                activation["ingestion_run_id"],
                str(activation["content_hash"])[:12],
                activation["resolution_counts"],
            )
        if stopped:
            break

    audit_log_summary = export_active_ingestion_logs()
    from .reconcile import reconcile_after_ingest

    reconcile_summary = reconcile_after_ingest()
    log.info("Reconciliation after ingest: %s", reconcile_summary)
    log.info(
        "Ingest finished. %d PDFs scanned, %d sources activated, %s audit rows exported%s.",
        seen,
        activated,
        audit_log_summary,
        " (limit reached)" if stopped else "",
    )
