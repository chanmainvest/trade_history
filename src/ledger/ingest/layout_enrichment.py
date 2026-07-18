"""Rebuildable geometry enrichment for already-validated semantic evidence."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..config import ROOT, SQLITE_PATH, STATEMENTS_DIR
from ..db import sqlite as sqlite_db
from ..parsers.layout import normalize_layout_text
from ..pdf_text import PdfLine, extract_pdf

GEOMETRY_EXTRACTOR_VERSION = "layout-v1"
_TOKEN_RE = re.compile(r"[A-Z0-9]+(?:[.\-][A-Z0-9]+)*")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _StoredLine:
    source_line_id: int
    line: PdfLine
    normalized: str
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class _Match:
    status: str
    method: str | None
    confidence: float | None
    line_indexes: tuple[int, ...] = ()


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(normalize_layout_text(value).upper()))


def _contains_tokens(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    return any(
        haystack[index:index + len(needle)] == needle
        for index in range(len(haystack) - len(needle) + 1)
    )


def _source_path(relpath: str) -> Path | None:
    roots = (Path(ROOT).resolve(), Path(STATEMENTS_DIR).parent.resolve())
    for root in roots:
        candidate = (root / relpath).resolve()
        if candidate.is_relative_to(root) and candidate.is_file():
            return candidate
    return None


def _match_evidence(
    raw_text: str | None,
    lines: list[_StoredLine],
    *,
    page_hint: int | None,
    line_hint: int | None,
) -> _Match:
    parts = tuple(
        normalized
        for part in (raw_text or "").splitlines()
        if (normalized := normalize_layout_text(part))
    )
    if not parts:
        return _Match("unmatched", None, None)

    exact: list[tuple[int, ...]] = []
    for index in range(len(lines)):
        if index + len(parts) > len(lines):
            break
        indexes = tuple(range(index, index + len(parts)))
        if tuple(lines[item].normalized for item in indexes) == parts:
            exact.append(indexes)
    if exact:
        hinted = [
            candidate
            for candidate in exact
            if lines[candidate[0]].line.page_number == page_hint
            and (line_hint is None or lines[candidate[0]].line.line_number == line_hint)
        ]
        if len(hinted) == 1:
            return _Match("exact", "persisted_page_line", 1.0, hinted[0])
        if len(exact) == 1:
            return _Match("exact", "exact_line_sequence", 1.0, exact[0])
        return _Match("ambiguous", "repeated_exact_text", None)

    needle = _tokens(" ".join(parts))
    token_candidates: list[tuple[int, ...]] = []
    for start in range(len(lines)):
        for width in range(1, min(4, len(lines) - start) + 1):
            indexes = tuple(range(start, start + width))
            combined = tuple(
                token
                for item in indexes
                for token in lines[item].tokens
            )
            if _contains_tokens(combined, needle):
                token_candidates.append(indexes)
                break
    if len(token_candidates) == 1:
        return _Match("unique_tokens", "unique_contiguous_tokens", 0.95, token_candidates[0])
    if token_candidates:
        return _Match("ambiguous", "repeated_token_sequence", None)
    return _Match("unmatched", "no_unique_text_alignment", None)


def _write_source_geometry(
    conn: sqlite3.Connection,
    *,
    source_file_id: int,
    ingestion_run_id: int,
    source_sha256: str,
    pdf,
    evidence_rows: list[sqlite3.Row],
) -> Counter[str]:
    conn.execute(
        "DELETE FROM source_pages WHERE ingestion_run_id = ?",
        (ingestion_run_id,),
    )
    stored_lines: list[_StoredLine] = []
    for page_number in range(1, pdf.page_count + 1):
        size = pdf.page_sizes[page_number - 1] if page_number <= len(pdf.page_sizes) else None
        if size is None:
            continue
        source_page_id = int(
            conn.execute(
                """
                INSERT INTO source_pages(
                    source_file_id, ingestion_run_id, extractor_version,
                    page_number, width, height
                ) VALUES (?, ?, ?, ?, ?, ?)
                RETURNING source_page_id
                """,
                (
                    source_file_id,
                    ingestion_run_id,
                    GEOMETRY_EXTRACTOR_VERSION,
                    page_number,
                    size[0],
                    size[1],
                ),
            ).fetchone()[0]
        )
        page_lines = pdf.page_lines[page_number - 1] if page_number <= len(pdf.page_lines) else []
        for line in page_lines:
            if line.bbox is None:
                continue
            normalized = normalize_layout_text(line.text)
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            source_line_id = int(
                conn.execute(
                    """
                    INSERT INTO source_lines(
                        source_page_id, line_number, raw_text,
                        normalized_text_hash, x0, top, x1, bottom, words_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    RETURNING source_line_id
                    """,
                    (
                        source_page_id,
                        line.line_number,
                        line.text,
                        digest,
                        *line.bbox,
                        json.dumps(line.word_dicts, sort_keys=True)
                        if line.word_dicts is not None
                        else None,
                    ),
                ).fetchone()[0]
            )
            stored_lines.append(
                _StoredLine(source_line_id, line, normalized, _tokens(normalized))
            )

    metrics: Counter[str] = Counter()
    for evidence in evidence_rows:
        evidence_id = int(evidence["evidence_id"])
        conn.execute("DELETE FROM source_evidence_lines WHERE evidence_id = ?", (evidence_id,))
        if not stored_lines:
            match = _Match("no_coordinates", "pdf_has_no_coordinate_lines", None)
        else:
            match = _match_evidence(
                evidence["raw_text"],
                stored_lines,
                page_hint=evidence["page_number"],
                line_hint=evidence["line_number"],
            )
        conn.execute(
            """
            INSERT INTO source_evidence_geometry(
                evidence_id, extractor_version, source_sha256, status,
                match_method, confidence, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))
            ON CONFLICT(evidence_id) DO UPDATE SET
                extractor_version = excluded.extractor_version,
                source_sha256 = excluded.source_sha256,
                status = excluded.status,
                match_method = excluded.match_method,
                confidence = excluded.confidence,
                updated_at = excluded.updated_at
            """,
            (
                evidence_id,
                GEOMETRY_EXTRACTOR_VERSION,
                source_sha256,
                match.status,
                match.method,
                match.confidence,
            ),
        )
        for ordinal, index in enumerate(match.line_indexes):
            conn.execute(
                """
                INSERT INTO source_evidence_lines(evidence_id, source_line_id, ordinal)
                VALUES (?, ?, ?)
                """,
                (evidence_id, stored_lines[index].source_line_id, ordinal),
            )
        metrics[match.status] += 1
    metrics["pages"] = pdf.page_count
    metrics["lines"] = len(stored_lines)
    return metrics


def enrich_layout(
    path: Path | str = SQLITE_PATH,
    *,
    source_file_id: int | None = None,
) -> dict[str, int]:
    """Enrich active semantic evidence with replaceable PDF coordinates."""
    sqlite_db.init_db(path)
    totals: Counter[str] = Counter()
    with sqlite_db.session(path) as conn:
        params: tuple[object, ...] = ()
        predicate = ""
        if source_file_id is not None:
            predicate = "AND sf.source_file_id = ?"
            params = (source_file_id,)
        sources = conn.execute(
            f"""
            SELECT sf.source_file_id, sf.relpath, sf.sha256,
                   sf.active_ingestion_run_id
              FROM source_files sf
             WHERE sf.active_ingestion_run_id IS NOT NULL {predicate}
             ORDER BY sf.source_file_id
            """,
            params,
        ).fetchall()
        for source in sources:
            source_path = _source_path(str(source["relpath"]))
            if source_path is None:
                totals["missing_pdf"] += 1
                continue
            workspace_root = Path(ROOT).resolve()
            statements_root = Path(STATEMENTS_DIR).parent.resolve()
            repo_root = (
                workspace_root
                if source_path.resolve().is_relative_to(workspace_root)
                else statements_root
            )
            pdf = extract_pdf(source_path, repo_root=repo_root, include_layout=True)
            if not source["sha256"] or pdf.sha256 != source["sha256"]:
                totals["hash_mismatch"] += 1
                continue
            evidence_rows = conn.execute(
                """
                SELECT evidence_id, row_kind, raw_text, page_number, line_number
                  FROM source_evidence
                 WHERE source_file_id = ? AND ingestion_run_id = ?
                 ORDER BY evidence_id
                """,
                (source["source_file_id"], source["active_ingestion_run_id"]),
            ).fetchall()
            conn.execute("SAVEPOINT layout_source")
            try:
                metrics = _write_source_geometry(
                    conn,
                    source_file_id=int(source["source_file_id"]),
                    ingestion_run_id=int(source["active_ingestion_run_id"]),
                    source_sha256=str(source["sha256"]),
                    pdf=pdf,
                    evidence_rows=evidence_rows,
                )
                conn.execute("RELEASE SAVEPOINT layout_source")
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT layout_source")
                conn.execute("RELEASE SAVEPOINT layout_source")
                log.exception(
                    "Layout enrichment failed for source_file_id=%s",
                    source["source_file_id"],
                )
                totals["failed_source"] += 1
                continue
            totals.update(metrics)
            totals["sources"] += 1
    return dict(sorted(totals.items()))
