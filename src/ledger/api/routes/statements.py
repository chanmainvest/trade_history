"""Statement upload, import, parser-draft, and explainer routes."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

import httpx
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ...config import DATA_DIR, ROOT, STATEMENTS_DIR
from ...db import sqlite as sqlite_db
from ...ingest.pipeline import _record_source_file, _write_statement
from ...ingest.reconcile import reconcile_after_ingest
from ...parsers import registry as _registered_parsers  # noqa: F401  (register parsers)
from ...parsers.registry import all_parsers, select_parser
from ...pdf_text import PdfText, extract_pdf
from . import config as config_route

router = APIRouter(prefix="/statements", tags=["statements"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PDF_MAGIC = b"%PDF-"
PARSER_DRAFT_DIR = Path(DATA_DIR) / "parser_drafts"


def _safe_upload_name(filename: str | None) -> str:
    raw_name = Path((filename or "statement.pdf").replace("\\", "/")).name
    safe_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", raw_name).strip(" .")
    if not safe_name:
        safe_name = "statement.pdf"
    if not safe_name.lower().endswith(".pdf"):
        raise HTTPException(400, "Only .pdf files are accepted.")
    return safe_name


def _upload_root() -> Path:
    root = Path(STATEMENTS_DIR) / "uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _repo_root_for(path: Path) -> Path:
    try:
        path.resolve().relative_to(Path(ROOT).resolve())
        return Path(ROOT)
    except ValueError:
        return Path(STATEMENTS_DIR).parent


def _extract_statement_pdf(path: Path) -> PdfText:
    return extract_pdf(path, repo_root=_repo_root_for(path))


def _find_upload_by_sha(sha256: str) -> Path:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256 or ""):
        raise HTTPException(400, "sha256 must be a 64-character hex digest")
    matches = sorted(_upload_root().glob(f"{sha256[:12]}_*.pdf"))
    for candidate in matches:
        if hashlib.sha256(candidate.read_bytes()).hexdigest().lower() == sha256.lower():
            return candidate
    raise HTTPException(404, "uploaded PDF not found")


def _statement_summary(result) -> list[dict]:
    statements: list[dict] = []
    for index, statement in enumerate(result.statements, start=1):
        statements.append({
            "index": index,
            "account": asdict(statement.account),
            "period_start": statement.period_start,
            "period_end": statement.period_end,
            "statement_type": statement.statement_type,
            "transactions": len(statement.transactions),
            "positions": len(statement.positions),
            "cash_balances": len(statement.cash_balances),
            "annual_performance": len(statement.annual_performance),
            "quarantine": len(statement.quarantine),
        })
    return statements


def _review_pdf(path: Path, *, institution_folder: str | None = None) -> dict:
    pdf = _extract_statement_pdf(path)
    parser = None if pdf.is_image_only else select_parser(institution_folder or "uploads", pdf)
    result = None
    if parser is not None:
        try:
            result = parser.parse(pdf)
        except Exception as exc:  # pragma: no cover - defensive API boundary
            return {
                "parser": {"name": parser.NAME, "version": parser.VERSION},
                "parse_status": "preview_failed",
                "statements": [],
                "errors": [f"parser crashed during preview: {exc}"],
            }
    if result is None:
        return {
            "parser": None,
            "parse_status": "image_only" if pdf.is_image_only else "unrecognized",
            "statements": [],
            "errors": ["PDF text is empty or image-only"] if pdf.is_image_only else [],
        }
    status = "preview_ok" if result.statements and not result.errors else (
        "preview_partial" if result.statements else "preview_failed"
    )
    return {
        "parser": {"name": parser.NAME, "version": parser.VERSION},
        "parse_status": status,
        "statements": _statement_summary(result),
        "errors": result.errors,
    }


def _institutions_payload() -> list[dict]:
    from ... import config

    return [
        {"folder": folder, "code": code}
        for folder, code in sorted(config.INSTITUTIONS.items(), key=lambda item: item[0].lower())
    ]


def _line_numbered_text(pdf: PdfText) -> str:
    chunks: list[str] = []
    for page_number, page in enumerate(pdf.pages, start=1):
        chunks.append(f"----- PAGE {page_number} -----")
        for line_number, line in enumerate(page.splitlines(), start=1):
            chunks.append(f"{line_number:04d}: {line}")
    return "\n".join(chunks)


def _build_parser_prompt(pdf: PdfText, *, institution_folder: str | None) -> str:
    schema_sql = (Path(ROOT) / "src" / "ledger" / "db" / "schema.sql").read_text(encoding="utf-8")
    types_py = (Path(ROOT) / "src" / "ledger" / "parsers" / "types.py").read_text(encoding="utf-8")
    example_parser = (Path(ROOT) / "src" / "ledger" / "parsers" / "hsbc.py").read_text(encoding="utf-8")
    prompt_template = (Path(ROOT) / "prompts" / "new-parser.md").read_text(encoding="utf-8")
    parser_names = ", ".join(sorted(parser.NAME for parser in all_parsers()))
    return "\n\n".join([
        prompt_template,
        "## Runtime inputs",
        f"folder_name: {institution_folder or 'uploads'}",
        f"existing_parsers: {parser_names}",
        "## PDF text",
        "```text\n" + _line_numbered_text(pdf)[:120_000] + "\n```",
        "## schema_sql",
        "```sql\n" + schema_sql + "\n```",
        "## types_py",
        "```python\n" + types_py + "\n```",
        "## example_parser",
        "```python\n" + example_parser[:80_000] + "\n```",
    ])


def _draft_dir_for(sha256: str) -> Path:
    draft_dir = PARSER_DRAFT_DIR / sha256[:12]
    draft_dir.mkdir(parents=True, exist_ok=True)
    return draft_dir


def _write_draft_bundle(path: Path, *, institution_folder: str | None) -> tuple[Path, dict]:
    pdf = _extract_statement_pdf(path)
    prompt = _build_parser_prompt(pdf, institution_folder=institution_folder)
    draft_dir = _draft_dir_for(pdf.sha256)
    prompt_path = draft_dir / "prompt.md"
    metadata_path = draft_dir / "metadata.json"
    prompt_path.write_text(prompt, encoding="utf-8")
    metadata = {
        "sha256": pdf.sha256,
        "relpath": pdf.relpath,
        "institution_folder": institution_folder or "uploads",
        "page_count": pdf.page_count,
        "existing_parsers": sorted(parser.NAME for parser in all_parsers()),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return prompt_path, metadata


def _relative_display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path(ROOT).resolve()).as_posix()
    except ValueError:
        return path.resolve().relative_to(Path(STATEMENTS_DIR).parent.resolve()).as_posix()


def _call_llm_provider(provider: str, prompt: str, *, model: str | None) -> str:
    cfg = config_route._read()
    keys = cfg.get("llm_keys") or {}
    api_key = (keys.get(provider) or "").strip()
    if not api_key:
        raise HTTPException(400, f"No {provider} API key is configured")

    timeout = httpx.Timeout(120.0, connect=20.0)
    with httpx.Client(timeout=timeout) as client:
        if provider == "openai":
            response = client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model or "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        if provider == "anthropic":
            response = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": model or "claude-3-5-sonnet-latest",
                    "max_tokens": 16000,
                    "temperature": 0.1,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            parts = response.json().get("content") or []
            return "\n".join(part.get("text", "") for part in parts if part.get("type") == "text")
        if provider == "google":
            chosen_model = model or "gemini-1.5-pro"
            response = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{chosen_model}:generateContent",
                params={"key": api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
            response.raise_for_status()
            candidates = response.json().get("candidates") or []
            parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
            return "\n".join(part.get("text", "") for part in parts)
    raise HTTPException(400, "provider must be openai, anthropic, or google")


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


@router.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict:  # noqa: B008  (FastAPI idiom)
    """Accept a PDF, save it, fingerprint it, and return a parse preview."""
    safe_name = _safe_upload_name(file.filename)

    body = await file.read(MAX_UPLOAD_BYTES + 1)
    if not body:
        raise HTTPException(400, "Empty file.")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "PDF upload exceeds the 25 MiB limit.")
    if not body.startswith(PDF_MAGIC):
        raise HTTPException(400, "Uploaded file is not a valid PDF.")
    sha = hashlib.sha256(body).hexdigest()

    uploads_dir = _upload_root()
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

    review = _review_pdf(dest)
    return {
        "status": "saved",
        "path": _relative_display_path(dest),
        "sha256": sha,
        "already_ingested": bool(existing),
        "parse_status": (existing["parse_status"] if existing else None),
        "review": review,
        "institutions": _institutions_payload(),
        "import_endpoint": "/api/statements/import",
        "draft_endpoint": "/api/statements/draft-parser",
    }


@router.post("/import")
def import_uploaded(payload: dict) -> dict:
    sha256 = str(payload.get("sha256") or "")
    institution_folder = (payload.get("institution_folder") or "uploads").strip()
    force = bool(payload.get("force", False))
    path = _find_upload_by_sha(sha256)
    pdf = _extract_statement_pdf(path)
    parser = select_parser(institution_folder, pdf)
    if parser is None:
        raise HTTPException(422, "No registered parser recognizes this upload; create a parser draft first")

    result = parser.parse(pdf)
    status = "ok" if result.statements and not result.errors else (
        "partial" if result.statements else "failed"
    )
    if status == "failed":
        raise HTTPException(422, {"errors": result.errors or ["parser produced no statements"]})

    with sqlite_db.session() as conn:
        existing = conn.execute(
            "SELECT source_file_id, parse_status FROM source_files WHERE sha256 = ?",
            (pdf.sha256,),
        ).fetchone()
        if existing and existing["parse_status"] in {"ok", "partial"} and not force:
            return {
                "status": "already_ingested",
                "source_file_id": existing["source_file_id"],
                "parse_status": existing["parse_status"],
            }
        source_file_id = _record_source_file(
            conn,
            pdf,
            parser_name=parser.NAME,
            parser_version=parser.VERSION,
            parse_status=status,
        )
        institution_code = dict((item["folder"], item["code"]) for item in _institutions_payload()).get(
            institution_folder,
            institution_folder,
        )
        for statement in result.statements:
            _write_statement(conn, source_file_id=source_file_id, institution_code=institution_code, stmt=statement)

    from ...ingest.repair_symbols import repair_symbols

    repair_summary = repair_symbols()
    reconcile_summary = reconcile_after_ingest()
    return {
        "status": "imported",
        "source_file_id": source_file_id,
        "parse_status": status,
        "parser": {"name": parser.NAME, "version": parser.VERSION},
        "statements": _statement_summary(result),
        "errors": result.errors,
        "repair": repair_summary,
        "reconciliation": reconcile_summary,
    }


@router.post("/draft-parser")
def draft_parser(payload: dict) -> dict:
    sha256 = str(payload.get("sha256") or "")
    provider = (payload.get("provider") or "").strip().lower()
    model = (payload.get("model") or "").strip() or None
    institution_folder = (payload.get("institution_folder") or "uploads").strip()
    send_to_provider = bool(payload.get("send_to_provider", False))
    path = _find_upload_by_sha(sha256)
    prompt_path, metadata = _write_draft_bundle(path, institution_folder=institution_folder)
    response_path: Path | None = None
    provider_status = "prompt_created"
    if send_to_provider:
        if provider not in {"openai", "anthropic", "google"}:
            raise HTTPException(400, "provider must be openai, anthropic, or google")
        prompt = prompt_path.read_text(encoding="utf-8")
        response_text = _call_llm_provider(provider, prompt, model=model)
        response_path = prompt_path.with_name(f"{provider}_response.md")
        response_path.write_text(response_text, encoding="utf-8")
        provider_status = "response_saved"
    return {
        "status": provider_status,
        "metadata": metadata,
        "prompt_path": _relative_display_path(prompt_path),
        "response_path": _relative_display_path(response_path) if response_path else None,
    }


@router.post("/reconciliation/rebuild")
def rebuild_reconciliation() -> dict:
    return reconcile_after_ingest()


@router.get("/reconciliation/summary")
def reconciliation_summary(limit: int = Query(200, ge=1, le=2000)) -> dict:
    with sqlite_db.session() as conn:
        transfer_rows = [dict(row) for row in conn.execute(
            """
            SELECT al.link_id, al.transfer_date, al.notes,
                   fa.account_number AS from_account, fi.code AS from_institution,
                   ta.account_number AS to_account, ti.code AS to_institution
              FROM account_links al
              JOIN accounts fa ON fa.account_id = al.from_account_id
              JOIN institutions fi ON fi.institution_id = fa.institution_id
              JOIN accounts ta ON ta.account_id = al.to_account_id
              JOIN institutions ti ON ti.institution_id = ta.institution_id
             ORDER BY al.transfer_date DESC, al.link_id DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()]
        position_link_count = conn.execute(
            "SELECT COUNT(*) FROM position_transaction_links"
        ).fetchone()[0]
        linked_transfer_count = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE counterpart_txn_id IS NOT NULL"
        ).fetchone()[0]
    return {
        "transfer_links": transfer_rows,
        "linked_transfer_transactions": linked_transfer_count,
        "position_transaction_links": position_link_count,
    }


def _source_path(relpath: str | None) -> Path | None:
    if not relpath:
        return None
    candidates = [Path(ROOT) / relpath, Path(STATEMENTS_DIR).parent / relpath]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _norm_line(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).upper()


def _annotated_pages(pdf: PdfText, references: list[dict]) -> list[dict]:
    normalized_refs = [
        {**reference, "_normalized": _norm_line(reference.get("raw_line"))}
        for reference in references
        if _norm_line(reference.get("raw_line"))
    ]
    pages: list[dict] = []
    for page_number, page_text in enumerate(pdf.pages, start=1):
        lines: list[dict] = []
        for line_number, text in enumerate(page_text.splitlines(), start=1):
            normalized = _norm_line(text)
            refs = []
            if normalized:
                for reference in normalized_refs:
                    raw = reference["_normalized"]
                    if raw == normalized or raw in normalized or (len(normalized) > 12 and normalized in raw):
                        refs.append({key: value for key, value in reference.items() if not key.startswith("_")})
            lines.append({"line_number": line_number, "text": text, "refs": refs})
        pages.append({"page_number": page_number, "lines": lines})
    return pages


def _annotated_boxes(pdf_path: Path, references: list[dict]) -> list[dict] | None:
    """Return per-page line bounding boxes annotated with matched references.

    Mirrors `_annotated_pages`, but draws coordinates from pdfplumber's
    `extract_text_lines()` (real PDF user-space, top-left origin) instead of
    plain text. The line/reference matching is identical to the existing
    explainer so a parsed item highlights the same PDF line(s) in both views.

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


@router.get("/explain/{statement_id}")
def explain(statement_id: int) -> dict:
    """Return PDF text lines annotated with parsed and quarantined rows."""
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

        annual_performance = [dict(r) for r in conn.execute(
            "SELECT * FROM annual_performance_reports WHERE statement_id = ? ORDER BY currency",
            (statement_id,),
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

    pages: list[dict] = []
    source_path = _source_path(sf["relpath"] if sf else None)
    if source_path is not None:
        pdf = _extract_statement_pdf(source_path)
        pages = _annotated_pages(pdf, references)

    return {
        "statement": dict(s),
        "source_file": dict(sf) if sf else None,
        "pages": pages,
        "transactions": txns,
        "positions": positions,
        "cash_balances": cash_balances,
        "annual_performance": annual_performance,
        "quarantine": quarantine,
    }


def _load_statement_rows(statement_id: int):
    """Load a statement's header, source file, parsed rows, and match references.

    Shared by the explainer and the boxes endpoints so the left/right link
    mapping stays identical between the two views.
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
    the matched ``refs`` so clicks link a box to its right-side item(s).
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
