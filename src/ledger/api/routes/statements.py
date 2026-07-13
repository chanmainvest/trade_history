"""Statement list, PDF serving, and extraction-verify (boxes) routes.

Database writes happen through the CLI (``uv run ledger ingest …``), not
through these HTTP endpoints. The endpoints here are read-only review
helpers: the statement picker for the Verify-extraction tab, the raw PDF
stream so the UI can render it, and per-page line bounding boxes annotated
with the parsed items they came from.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from ...config import ROOT, STATEMENTS_DIR
from ...db import sqlite as sqlite_db
from ...parsers import registry as _registered_parsers  # noqa: F401  (register parsers)
from ...pdf_text import extract_pdf

router = APIRouter(prefix="/statements", tags=["statements"])


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
        rows = [dict(row) for row in conn.execute(
            """
            SELECT s.statement_id, s.period_start, s.period_end, s.statement_type,
                   a.account_id, a.account_number, a.account_type, a.nickname,
                   i.code AS institution_code, i.display_name AS institution_name,
                   sf.relpath, sf.parser_name, sf.parse_status
              FROM statements s
              JOIN accounts a ON a.account_id = s.account_id
              JOIN institutions i ON i.institution_id = a.institution_id
              JOIN source_files sf ON sf.source_file_id = s.source_file_id
             ORDER BY s.period_end DESC, s.statement_id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()]
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


def _load_statement_rows(statement_id: int):
    """Load a statement's header, source file, parsed rows, and match references.

    Shared by the boxes endpoint so the left/right link mapping in the Verify
    tab matches the rows shown on its right side.
    """
    with sqlite_db.session() as conn:
        s = conn.execute(
            "SELECT statement_id, account_id, period_start, period_end, "
            "       source_file_id FROM statements WHERE statement_id = ?",
            (statement_id,),
        ).fetchone()
        if not s:
            return None

        sf = conn.execute(
            "SELECT relpath, sha256, parser_name, parser_version, parse_status "
            "  FROM source_files WHERE source_file_id = ?",
            (s["source_file_id"],),
        ).fetchone()

        txns = [dict(r) for r in conn.execute(
            "SELECT t.transaction_id, t.trade_date, t.txn_type, t.quantity, t.price, "
            "       t.net_amount, t.currency, t.description, t.raw_line, "
            "       COALESCE(inst.option_root, inst.symbol) AS symbol "
            "  FROM transactions t "
            "  LEFT JOIN instruments inst ON inst.instrument_id = t.instrument_id "
            " WHERE t.statement_id = ? ORDER BY t.trade_date, t.transaction_id",
            (statement_id,),
        ).fetchall()]

        positions = [dict(r) for r in conn.execute(
            "SELECT ps.snapshot_id, ps.as_of_date, ps.quantity, ps.market_value, "
            "       ps.currency, ps.raw_line, COALESCE(inst.option_root, inst.symbol) AS symbol "
            "  FROM position_snapshots ps "
            "  JOIN instruments inst ON inst.instrument_id = ps.instrument_id "
            " WHERE ps.statement_id = ? ORDER BY inst.symbol",
            (statement_id,),
        ).fetchall()]

        cash_balances = [dict(r) for r in conn.execute(
            "SELECT cash_balance_id, as_of_date, currency, opening_balance, closing_balance "
            "  FROM cash_balances WHERE statement_id = ? ORDER BY currency",
            (statement_id,),
        ).fetchall()]

        quarantine = [dict(r) for r in conn.execute(
            "SELECT quarantine_id, raw_line, reason "
            "  FROM quarantine_transactions "
            " WHERE source_file_id = ? LIMIT 200",
            (s["source_file_id"],),
        ).fetchall()]

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
    for row in quarantine:
        references.append({
            "kind": "quarantine",
            "id": row["quarantine_id"],
            "label": row.get("reason") or "quarantine",
            "raw_line": row.get("raw_line"),
        })

    return {
        "statement": dict(s),
        "source_file": dict(sf) if sf else None,
        "transactions": txns,
        "positions": positions,
        "cash_balances": cash_balances,
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
        "quarantine": loaded["quarantine"],
    }
