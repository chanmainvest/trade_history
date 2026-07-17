"""Statement list, PDF serving, and extraction-verify (boxes) routes.

Database writes happen through the CLI (``uv run ledger ingest …``), not
through these HTTP endpoints. The endpoints here are read-only review
helpers: the statement picker for the Verify-extraction tab, the raw PDF
stream so the UI can render it, and per-page line bounding boxes annotated
with the parsed items they came from.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ...config import ROOT, STATEMENTS_DIR
from ...db import sqlite as sqlite_db
from ...parsers import registry as _registered_parsers  # noqa: F401  (register parsers)
from ...pdf_text import extract_pdf

router = APIRouter(prefix="/statements", tags=["statements"])

def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return columns without assuming the active ledger has already reached v6."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if exists is None:
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _source_file_fields(conn: sqlite3.Connection, alias: str = "sf") -> tuple[str, str]:
    """Select parser/run metadata while still reading a legacy ledger safely."""
    columns = _table_columns(conn, "source_files")
    fields = [
        f"{alias}.relpath AS relpath",
        f"{alias}.sha256 AS sha256",
        f"{alias}.parser_name AS parser_name" if "parser_name" in columns else "NULL AS parser_name",
        f"{alias}.parser_version AS parser_version"
        if "parser_version" in columns
        else "NULL AS parser_version",
        f"{alias}.parse_status AS parse_status" if "parse_status" in columns else "NULL AS parse_status",
    ]
    if "active_ingestion_run_id" in columns and _table_columns(conn, "ingestion_runs"):
        fields[2] = f"COALESCE({alias}.parser_name, ir.parser_name) AS parser_name"
        fields[3] = f"COALESCE({alias}.parser_version, ir.parser_version) AS parser_version"
        fields.extend(
            [
                f"{alias}.active_ingestion_run_id AS active_ingestion_run_id",
                "ir.status AS active_run_status",
                "ir.contract_version AS contract_version",
                "ir.schema_version AS run_schema_version",
            ]
        )
        return ", ".join(fields), f"LEFT JOIN ingestion_runs ir ON ir.ingestion_run_id = {alias}.active_ingestion_run_id"
    fields.extend(
        [
            "NULL AS active_ingestion_run_id",
            "NULL AS active_run_status",
            "NULL AS contract_version",
            "NULL AS run_schema_version",
        ]
    )
    return ", ".join(fields), ""


def _empty_quality() -> dict[str, int | list[str]]:
    return {
        "scope_count": 0,
        "complete_scope_count": 0,
        "incomplete_scope_count": 0,
        "unresolved_identity_count": 0,
        "quarantine_count": 0,
        "reconciliation_result_count": 0,
        "unreconciled_count": 0,
        "incomplete_reconciliation_count": 0,
        "quality_flags": [],
    }


def _statement_id_batches(statement_ids: list[int], size: int = 900) -> Iterable[list[int]]:
    """Keep list quality queries below conservative SQLite bind-variable limits."""
    for start in range(0, len(statement_ids), size):
        yield statement_ids[start : start + size]


def _quality_flags(quality: dict[str, int | list[str]]) -> list[str]:
    flags: list[str] = []
    if int(quality["unresolved_identity_count"]) or int(quality["quarantine_count"]):
        flags.append("unresolved")
    if int(quality["incomplete_scope_count"]) or int(quality["incomplete_reconciliation_count"]):
        flags.append("incomplete")
    if int(quality["unreconciled_count"]):
        flags.append("unreconciled")
    return flags


def _statement_quality(conn: sqlite3.Connection, statement_ids: Iterable[int]) -> dict[int, dict]:
    """Summarize read-only extraction/reconciliation quality per statement.

    The compatibility branches intentionally report no v6-only facts for an
    old ledger instead of mutating it or calling unknown data complete.
    """
    ids = sorted({int(statement_id) for statement_id in statement_ids})
    quality = {statement_id: _empty_quality() for statement_id in ids}
    if not ids:
        return quality

    snapshot_columns = _table_columns(conn, "snapshot_sets")
    if {"statement_id", "completeness"}.issubset(snapshot_columns):
        for batch in _statement_id_batches(ids):
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"""
                SELECT statement_id,
                       COUNT(*) AS scope_count,
                       SUM(CASE WHEN completeness = 'complete' THEN 1 ELSE 0 END) AS complete_scope_count,
                       SUM(CASE WHEN completeness <> 'complete' THEN 1 ELSE 0 END) AS incomplete_scope_count
                  FROM snapshot_sets
                 WHERE statement_id IN ({placeholders})
                 GROUP BY statement_id
                """,
                batch,
            ).fetchall()
            for row in rows:
                entry = quality[int(row["statement_id"])]
                for key in ("scope_count", "complete_scope_count", "incomplete_scope_count"):
                    entry[key] = int(row[key] or 0)

    transaction_columns = _table_columns(conn, "transactions")
    if {"statement_id", "resolution_method"}.issubset(transaction_columns):
        for batch in _statement_id_batches(ids):
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"""
                SELECT statement_id,
                       SUM(CASE WHEN resolution_method = 'unresolved_printed_identity' THEN 1 ELSE 0 END)
                         AS unresolved_identity_count
                  FROM transactions
                 WHERE statement_id IN ({placeholders})
                 GROUP BY statement_id
                """,
                batch,
            ).fetchall()
            for row in rows:
                quality[int(row["statement_id"])]["unresolved_identity_count"] = int(
                    row["unresolved_identity_count"] or 0
                )

    quarantine_columns = _table_columns(conn, "quarantine_transactions")
    if "statement_id" in quarantine_columns:
        for batch in _statement_id_batches(ids):
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"""
                SELECT statement_id, COUNT(*) AS quarantine_count
                  FROM quarantine_transactions
                 WHERE statement_id IN ({placeholders})
                 GROUP BY statement_id
                """,
                batch,
            ).fetchall()
            for row in rows:
                if row["statement_id"] is not None:
                    quality[int(row["statement_id"])]["quarantine_count"] = int(row["quarantine_count"])

    reconciliation_columns = _table_columns(conn, "reconciliation_results")
    if {"statement_id", "status"}.issubset(reconciliation_columns):
        for batch in _statement_id_batches(ids):
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"""
                SELECT statement_id,
                       COUNT(*) AS reconciliation_result_count,
                       SUM(CASE WHEN status = 'unexplained_residual' THEN 1 ELSE 0 END)
                         AS unreconciled_count,
                       SUM(CASE WHEN status IN ('incomplete_input', 'missing_prior_checkpoint', 'ambiguous_transfer')
                                THEN 1 ELSE 0 END) AS incomplete_reconciliation_count
                  FROM reconciliation_results
                 WHERE statement_id IN ({placeholders})
                 GROUP BY statement_id
                """,
                batch,
            ).fetchall()
            for row in rows:
                if row["statement_id"] is None:
                    continue
                entry = quality[int(row["statement_id"])]
                for key in (
                    "reconciliation_result_count",
                    "unreconciled_count",
                    "incomplete_reconciliation_count",
                ):
                    entry[key] = int(row[key] or 0)

    for entry in quality.values():
        entry["quality_flags"] = _quality_flags(entry)
    return quality


def _list_statement_rows(conn: sqlite3.Connection, limit: int) -> list[dict]:
    source_fields, source_join = _source_file_fields(conn)
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT s.statement_id, s.period_start, s.period_end, s.statement_type,
                   a.account_id, a.account_number, a.account_type, a.nickname,
                   i.code AS institution_code, i.display_name AS institution_name,
                   {source_fields}
              FROM statements s
              JOIN accounts a ON a.account_id = s.account_id
              JOIN institutions i ON i.institution_id = a.institution_id
              JOIN source_files sf ON sf.source_file_id = s.source_file_id
              {source_join}
             ORDER BY s.period_end DESC, s.statement_id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    ]
    quality = _statement_quality(conn, (row["statement_id"] for row in rows))
    for row in rows:
        row.update(quality[int(row["statement_id"])])
    return rows


def _repo_root_for(path: Path) -> Path:
    try:
        path.resolve().relative_to(Path(ROOT).resolve())
        return Path(ROOT)
    except ValueError:
        return Path(STATEMENTS_DIR).parent


def _extract_statement_pdf(path: Path):
    return extract_pdf(path, repo_root=_repo_root_for(path))


@router.get("")
def list_statements(limit: int = Query(200, ge=1, le=2000)) -> dict:
    with sqlite_db.session() as conn:
        rows = _list_statement_rows(conn, limit)
    return {"rows": rows}


def _source_path(relpath: str | None) -> Path | None:
    if not relpath:
        return None
    candidates = [Path(ROOT) / relpath, Path(STATEMENTS_DIR).parent / relpath]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _norm_line(value: str | None) -> str:
    import re

    return re.sub(r"\s+", " ", (value or "").strip()).upper()


def _annotated_boxes(pdf_path: Path, references: list[dict]) -> list[dict] | None:
    """Return per-page line bounding boxes annotated with matched references.

    Uses pdfplumber's ``extract_text_lines()`` (real PDF user-space, top-left
    origin). Line/reference matching is identical to the old explainer so a
    parsed item highlights the same PDF line(s) it was extracted from.

    Returns ``None`` if the PDF cannot be opened (e.g. image-only / encrypted),
    so the caller can report a clean 422 instead of crashing.
    """
    import pdfplumber

    normalized_refs = [
        {**reference, "_normalized": _norm_line(reference.get("raw_line"))}
        for reference in references
        if _norm_line(reference.get("raw_line"))
    ]
    pages: list[dict] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                lines: list[dict] = []
                for raw_line in page.extract_text_lines():
                    text = raw_line.get("text", "")
                    normalized = _norm_line(text)
                    refs: list[dict] = []
                    if normalized:
                        for reference in normalized_refs:
                            raw = reference["_normalized"]
                            if raw == normalized or raw in normalized or (len(normalized) > 12 and normalized in raw):
                                refs.append({key: value for key, value in reference.items() if not key.startswith("_")})
                    x0 = raw_line.get("x0", 0.0)
                    top = raw_line.get("top", 0.0)
                    x1 = raw_line.get("x1", 0.0)
                    bottom = raw_line.get("bottom", 0.0)
                    lines.append({
                        "bbox": [round(x0, 2), round(top, 2), round(x1, 2), round(bottom, 2)],
                        "text": text,
                        "refs": refs,
                    })
                pages.append({
                    "page_number": page_number,
                    "width": page.width,
                    "height": page.height,
                    "lines": lines,
                })
    except Exception:
        return None
    return pages


def _evidence_projection(
    conn: sqlite3.Connection,
    *,
    table: str,
    table_alias: str,
    evidence_alias: str,
) -> tuple[str, str]:
    """Return a source-evidence join and raw-text expression when available."""
    columns = _table_columns(conn, table)
    raw_text = f"{table_alias}.raw_line" if "raw_line" in columns else "NULL"
    evidence_columns = _table_columns(conn, "source_evidence")
    if "evidence_id" not in columns or "raw_text" not in evidence_columns:
        return "", raw_text
    return (
        f" LEFT JOIN source_evidence {evidence_alias} "
        f"ON {evidence_alias}.evidence_id = {table_alias}.evidence_id ",
        f"COALESCE({raw_text}, {evidence_alias}.raw_text)",
    )


def _load_statement_rows(statement_id: int, *, path: Path | str | None = None):
    """Load a statement's header, source file, parsed rows, and match references.

    Shared by the boxes endpoint so the left/right link mapping in the Verify
    tab matches the rows shown on its right side.
    """
    db_path = path if path is not None else sqlite_db.SQLITE_PATH
    with sqlite_db.session(db_path) as conn:
        s = conn.execute(
            "SELECT statement_id, account_id, period_start, period_end, "
            "       source_file_id FROM statements WHERE statement_id = ?",
            (statement_id,),
        ).fetchone()
        if not s:
            return None

        source_fields, source_join = _source_file_fields(conn)
        sf = conn.execute(
            f"SELECT {source_fields} FROM source_files sf {source_join} WHERE sf.source_file_id = ?",
            (s["source_file_id"],),
        ).fetchone()

        transaction_join, transaction_raw_line = _evidence_projection(
            conn,
            table="transactions",
            table_alias="t",
            evidence_alias="te",
        )
        txns = [dict(r) for r in conn.execute(
            f"""
            SELECT t.transaction_id, t.trade_date, t.txn_type, t.quantity, t.price,
                   t.net_amount, t.currency, t.description, {transaction_raw_line} AS raw_line,
                   COALESCE(inst.option_root, inst.symbol) AS symbol
              FROM transactions t
              LEFT JOIN instruments inst ON inst.instrument_id = t.instrument_id
              {transaction_join}
             WHERE t.statement_id = ? ORDER BY t.trade_date, t.transaction_id
            """,
            (statement_id,),
        ).fetchall()]

        position_join, position_raw_line = _evidence_projection(
            conn,
            table="position_snapshots",
            table_alias="ps",
            evidence_alias="pe",
        )
        positions = [dict(r) for r in conn.execute(
            f"""
            SELECT ps.snapshot_id, ps.as_of_date, ps.quantity, ps.market_value,
                   ps.currency, {position_raw_line} AS raw_line,
                   COALESCE(inst.option_root, inst.symbol) AS symbol
              FROM position_snapshots ps
              JOIN instruments inst ON inst.instrument_id = ps.instrument_id
              {position_join}
             WHERE ps.statement_id = ? ORDER BY inst.symbol
            """,
            (statement_id,),
        ).fetchall()]

        cash_join, cash_raw_line = _evidence_projection(
            conn,
            table="cash_balances",
            table_alias="cb",
            evidence_alias="ce",
        )
        cash_balances = [dict(r) for r in conn.execute(
            f"""
            SELECT cb.cash_balance_id, cb.as_of_date, cb.currency, cb.opening_balance,
                   cb.closing_balance, {cash_raw_line} AS raw_line
              FROM cash_balances cb
              {cash_join}
             WHERE cb.statement_id = ? ORDER BY cb.currency
            """,
            (statement_id,),
        ).fetchall()]

        quarantine_join, quarantine_raw_line = _evidence_projection(
            conn,
            table="quarantine_transactions",
            table_alias="q",
            evidence_alias="qe",
        )
        quarantine = [dict(r) for r in conn.execute(
            f"""
            SELECT q.quarantine_id, {quarantine_raw_line} AS raw_line, q.reason
              FROM quarantine_transactions q
              {quarantine_join}
             WHERE q.source_file_id = ? LIMIT 200
            """,
            (s["source_file_id"],),
        ).fetchall()]

        scopes: list[dict] = []
        summary_totals: list[dict] = []
        snapshot_columns = _table_columns(conn, "snapshot_sets")
        has_snapshot_scopes = {"statement_id", "currency", "section_type", "scope_key", "completeness"}.issubset(
            snapshot_columns
        )
        if has_snapshot_scopes:
            summary_join, summary_raw_line = _evidence_projection(
                conn,
                table="snapshot_sets",
                table_alias="ss",
                evidence_alias="se",
            )
            scopes = [dict(r) for r in conn.execute(
                f"""
                SELECT ss.snapshot_set_id, ss.currency, ss.section_type, ss.scope_key,
                       ss.completeness, ss.validation_status, ss.reported_total,
                       {summary_raw_line} AS raw_line
                  FROM snapshot_sets ss
                  {summary_join}
                 WHERE ss.statement_id = ?
                 ORDER BY ss.currency, ss.section_type, ss.scope_key
                """,
                (statement_id,),
            ).fetchall()]
            summary_totals = [
                row for row in scopes
                if row["section_type"] == "summary" and row["reported_total"] is not None
            ]

        reconciliation_results: list[dict] = []
        reconciliation_columns = _table_columns(conn, "reconciliation_results")
        required_reconciliation_columns = {
            "statement_id",
            "kind",
            "status",
            "currency",
            "reason",
            "residual",
            "tolerance",
            "opening_value",
            "summed_deltas",
            "expected_close",
            "reported_close",
            "snapshot_set_id",
            "prior_snapshot_set_id",
        }
        if required_reconciliation_columns.issubset(reconciliation_columns):
            snapshot_join = (
                "LEFT JOIN snapshot_sets ss ON ss.snapshot_set_id = r.snapshot_set_id"
                if has_snapshot_scopes
                else ""
            )
            snapshot_fields = "ss.section_type, ss.scope_key" if has_snapshot_scopes else "NULL AS section_type, NULL AS scope_key"
            reconciliation_results = [dict(r) for r in conn.execute(
                f"""
                SELECT r.reconciliation_id, r.kind, r.currency, r.status, r.reason,
                       r.residual, r.tolerance, r.opening_value, r.summed_deltas,
                       r.expected_close, r.reported_close, r.snapshot_set_id,
                       r.prior_snapshot_set_id,
                       {snapshot_fields}
                  FROM reconciliation_results r
                  {snapshot_join}
                 WHERE r.statement_id = ?
                 ORDER BY r.kind, r.currency, r.reconciliation_id
                """,
                (statement_id,),
            ).fetchall()]

        quality = _statement_quality(conn, [statement_id]).get(statement_id, _empty_quality())

    references: list[dict] = []
    for row in txns:
        references.append({
            "kind": "transaction",
            "id": row["transaction_id"],
            "label": f"{row['txn_type']} {row.get('symbol') or ''}".strip(),
            "raw_line": row.get("raw_line"),
        })
    for row in positions:
        references.append({
            "kind": "position",
            "id": row["snapshot_id"],
            "label": f"position {row.get('symbol') or ''}".strip(),
            "raw_line": row.get("raw_line"),
        })
    for row in cash_balances:
        references.append({
            "kind": "cash",
            "id": row["cash_balance_id"],
            "label": f"cash {row.get('currency') or ''}".strip(),
            "raw_line": row.get("raw_line"),
        })
    for row in summary_totals:
        references.append({
            "kind": "summary",
            "id": row["snapshot_set_id"],
            "label": f"summary {row.get('currency') or ''}".strip(),
            "raw_line": row.get("raw_line"),
        })
    for row in quarantine:
        references.append({
            "kind": "quarantine",
            "id": row["quarantine_id"],
            "label": row.get("reason") or "quarantine",
            "raw_line": row.get("raw_line"),
        })

    return {
        "statement": {**dict(s), "quality": quality},
        "source_file": dict(sf) if sf else None,
        "transactions": txns,
        "positions": positions,
        "cash_balances": cash_balances,
        "summary_totals": summary_totals,
        "scopes": scopes,
        "reconciliation_results": reconciliation_results,
        "quarantine": quarantine,
        "references": references,
    }


@router.get("/{statement_id}/pdf")
def statement_pdf(statement_id: int):
    """Serve the raw PDF for a statement so the UI can render it.

    Statements are read-only inputs (AGENTS.md cardinal rule 2). This endpoint
    only reads the file already recorded in ``source_files.relpath``; the path
    comes from the DB, but we still confirm the resolved path is contained
    under the statements dir / repo root before serving it.
    """
    with sqlite_db.session() as conn:
        row = conn.execute(
            "SELECT sf.relpath FROM statements s "
            " JOIN source_files sf ON sf.source_file_id = s.source_file_id "
            " WHERE s.statement_id = ?",
            (statement_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "statement not found")

    path = _source_path(row["relpath"])
    if path is None:
        raise HTTPException(404, "source PDF is not available on disk")

    # Path-traversal guard: the relpath must resolve inside the repo root or
    # the statements dir (mirrors the containment stance of _repo_root_for).
    resolved = path.resolve()
    allowed_roots = [Path(ROOT).resolve(), Path(STATEMENTS_DIR).parent.resolve()]
    if not any(resolved.is_relative_to(root) for root in allowed_roots):
        raise HTTPException(404, "source PDF is not available on disk")

    return FileResponse(
        str(path),
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@router.get("/{statement_id}/boxes")
def statement_boxes(statement_id: int) -> dict:
    """Return per-page line bounding boxes annotated with matched references.

    Used by the Verify-extraction tab: the UI renders the raw PDF (via
    ``/statements/{id}/pdf``) and overlays these boxes so a user can see where
    each parsed transaction / position / quarantined row came from. Boxes carry
    the matched ``refs`` so clicks link a box to its right-side item.
    """
    loaded = _load_statement_rows(statement_id)
    if loaded is None:
        raise HTTPException(404, "statement not found")

    relpath = loaded["source_file"]["relpath"] if loaded["source_file"] else None
    source_path = _source_path(relpath)
    pages: list[dict] = []
    if source_path is not None:
        boxes = _annotated_boxes(source_path, loaded["references"])
        if boxes is None:
            raise HTTPException(422, "PDF could not be opened (image-only or encrypted)")
        pages = boxes

    return {
        "statement": loaded["statement"],
        "source_file": loaded["source_file"],
        "pages": pages,
        "transactions": loaded["transactions"],
        "positions": loaded["positions"],
        "cash_balances": loaded["cash_balances"],
        "summary_totals": loaded["summary_totals"],
        "scopes": loaded["scopes"],
        "reconciliation_results": loaded["reconciliation_results"],
        "quarantine": loaded["quarantine"],
    }
