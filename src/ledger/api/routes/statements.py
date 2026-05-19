"""POST /statements/upload — receive a PDF, fingerprint, try to parse.

STATUS: stub. The file is saved to <STATEMENTS_DIR>/uploads/ and a
fingerprint is returned. LLM-assisted parser creation for unrecognized
statement types is NOT implemented (see AGENTS.md §8).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ...config import STATEMENTS_DIR
from ...db import sqlite as sqlite_db

router = APIRouter(prefix="/statements", tags=["statements"])


@router.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict:  # noqa: B008  (FastAPI idiom)
    """Accept a PDF, save it to STATEMENTS_DIR/uploads/, fingerprint it."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only .pdf files are accepted.")

    body = await file.read()
    if not body:
        raise HTTPException(400, "Empty file.")
    sha = hashlib.sha256(body).hexdigest()

    uploads_dir: Path = STATEMENTS_DIR / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = uploads_dir / f"{sha[:12]}_{file.filename}"
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
        # Next step (not implemented): pick a parser, run it, return the
        # provisional transactions for the user to review.
        "note": (
            "PDF upload accepted. Auto-parse + LLM-assisted new-parser "
            "drafting is not implemented yet. Run `uv run ledger ingest run` "
            "to ingest, or configure an LLM API key in Settings and click "
            "'Generate parser' (also not yet implemented)."
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
