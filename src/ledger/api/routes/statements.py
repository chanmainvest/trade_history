"""POST /statements/upload — receive a PDF, fingerprint, try to parse.

STATUS: stub. The file is saved to <STATEMENTS_DIR>/uploads/ and a
fingerprint is returned. LLM-assisted parser creation for unrecognized
statement types is NOT implemented (see AGENTS.md §8).
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ...config import STATEMENTS_DIR
from ...db import sqlite as sqlite_db

router = APIRouter(prefix="/statements", tags=["statements"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PDF_MAGIC = b"%PDF-"


def _safe_upload_name(filename: str | None) -> str:
    raw_name = Path((filename or "statement.pdf").replace("\\", "/")).name
    safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", raw_name).strip(" .")
    if not safe_name:
        safe_name = "statement.pdf"
    if not safe_name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only .pdf files are accepted.")
    return safe_name


@router.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict:  # noqa: B008  (FastAPI idiom)
    """Accept a PDF, save it to STATEMENTS_DIR/uploads/, fingerprint it."""
    safe_name = _safe_upload_name(file.filename)

    body = await file.read(MAX_UPLOAD_BYTES + 1)
    if not body:
        raise HTTPException(400, "Empty file.")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "PDF upload exceeds the 25 MiB limit.")
    if not body.startswith(PDF_MAGIC):
        raise HTTPException(400, "Uploaded file is not a valid PDF.")
    sha = hashlib.sha256(body).hexdigest()

    uploads_dir: Path = STATEMENTS_DIR / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = (uploads_dir / f"{sha[:12]}_{safe_name}").resolve()
    uploads_root = uploads_dir.resolve()
    if not dest.is_relative_to(uploads_root):
        raise HTTPException(400, "Invalid upload filename.")
    dest.write_bytes(body)

    # Is this PDF already known?
    with sqlite_db.session() as conn:
        existing = conn.execute(
            "SELECT source_file_id, parse_status FROM source_files WHERE sha256 = ?",
            (sha,),
        ).fetchone()

    return {
        "status": "saved",
        "path": str(dest.relative_to(STATEMENTS_DIR.parent)),
        "sha256": sha,
        "already_ingested": bool(existing),
        "parse_status": (existing["parse_status"] if existing else None),
        # Next step (not implemented): run an upload review/import workflow.
        "note": (
            "PDF upload accepted and saved. Upload review/import and "
            "LLM-assisted new-parser drafting are not implemented yet. "
            "Move the PDF into a recognized institution folder or run "
            "`uv run ledger ingest run --force` after adding parser support."
        ),
    }


@router.get("/explain/{statement_id}")
def explain(statement_id: int) -> dict:
    """Return a side-by-side view of (PDF text, parsed rows) for review.

    STATUS: stub. Returns just the parsed rows for now; PDF text overlay
    and per-line provenance is deferred.
    """
    with sqlite_db.session() as conn:
        s = conn.execute(
            "SELECT statement_id, account_id, period_start, period_end, "
            "       source_file_id "
            "  FROM statements WHERE statement_id = ?",
            (statement_id,),
        ).fetchone()
        if not s:
            raise HTTPException(404, "statement not found")

        sf = conn.execute(
            "SELECT relpath, sha256, parser_name, parser_version, parse_status "
            "  FROM source_files WHERE source_file_id = ?",
            (s["source_file_id"],),
        ).fetchone()

        txns = [dict(r) for r in conn.execute(
            "SELECT trade_date, txn_type, quantity, price, net_amount, "
            "       currency, description, raw_line "
            "  FROM transactions WHERE statement_id = ? ORDER BY trade_date",
            (statement_id,),
        ).fetchall()]

        quarantine = [dict(r) for r in conn.execute(
            "SELECT raw_line, reason "
            "  FROM quarantine_transactions "
            " WHERE source_file_id = ? LIMIT 200",
            (s["source_file_id"],),
        ).fetchall()]

    return {
        "statement": dict(s),
        "source_file": dict(sf) if sf else None,
        "transactions": txns,
        "quarantine": quarantine,
        "note": "PDF + text-dump overlay is not implemented yet (deferred).",
    }
