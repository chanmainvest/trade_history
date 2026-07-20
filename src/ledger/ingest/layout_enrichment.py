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
    token_words: tuple[int, ...]


@dataclass(frozen=True)
class _Match:
    status: str
    method: str | None
    confidence: float | None
    line_indexes: tuple[int, ...] = ()
    token_ranges: tuple[tuple[int, int] | None, ...] = ()


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(normalize_layout_text(value).upper()))


def _token_offset(haystack: tuple[str, ...], needle: tuple[str, ...]) -> int | None:
    if not needle or len(needle) > len(haystack):
        return None
    return next(
        (
            index
            for index in range(len(haystack) - len(needle) + 1)
            if haystack[index:index + len(needle)] == needle
        ),
        None,
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
    allowed_pages: frozenset[int] | None = None,
) -> _Match:
    parts = tuple(
        normalized
        for part in (raw_text or "").splitlines()
        if (normalized := normalize_layout_text(part))
    )
    if not parts:
        return _Match("unmatched", None, None)

    def allowed(indexes: tuple[int, ...]) -> bool:
        return allowed_pages is None or all(
            lines[index].line.page_number in allowed_pages for index in indexes
        )

    exact: list[tuple[int, ...]] = []
    for index in range(len(lines)):
        if index + len(parts) > len(lines):
            break
        indexes = tuple(range(index, index + len(parts)))
        if allowed(indexes) and tuple(lines[item].normalized for item in indexes) == parts:
            exact.append(indexes)
    if exact:
        page_hinted = [
            candidate
            for candidate in exact
            if lines[candidate[0]].line.page_number == page_hint
        ]
        line_hinted = [
            candidate
            for candidate in page_hinted
            if line_hint is not None
            and lines[candidate[0]].line.line_number == line_hint
        ]
        if len(line_hinted) == 1:
            return _Match("exact", "persisted_page_line", 1.0, line_hinted[0])
        if len(page_hinted) == 1:
            return _Match("exact", "persisted_page", 1.0, page_hinted[0])
        if len(exact) == 1:
            return _Match("exact", "exact_line_sequence", 1.0, exact[0])
        return _Match("ambiguous", "repeated_exact_text", None)

    # Semantic evidence can intentionally join non-adjacent statement lines,
    # notably an opening and closing cash balance around transaction rows.
    # Match the exact normalized fragments in order, but only accept a unique
    # sequence within the statement's physical pages.
    ordered: list[tuple[int, ...]] = []

    def extend(prefix: tuple[int, ...], part_index: int) -> None:
        if len(ordered) > 1:
            return
        if part_index == len(parts):
            ordered.append(prefix)
            return
        start = prefix[-1] + 1 if prefix else 0
        for index in range(start, len(lines)):
            if lines[index].normalized != parts[part_index] or not allowed((index,)):
                continue
            extend((*prefix, index), part_index + 1)

    if len(parts) > 1:
        extend((), 0)
    if len(ordered) == 1:
        return _Match("exact", "ordered_noncontiguous_lines", 1.0, ordered[0])
    if ordered:
        page_hinted = [
            candidate
            for candidate in ordered
            if lines[candidate[0]].line.page_number == page_hint
        ]
        if len(page_hinted) == 1:
            return _Match("exact", "ordered_noncontiguous_persisted_page", 1.0, page_hinted[0])
        return _Match("ambiguous", "repeated_ordered_text", None)

    needle = _tokens(" ".join(parts))
    token_candidates: list[tuple[tuple[int, ...], tuple[tuple[int, int] | None, ...]]] = []
    for start in range(len(lines)):
        for width in range(1, min(4, len(lines) - start) + 1):
            indexes = tuple(range(start, start + width))
            if not allowed(indexes):
                continue
            combined = tuple(
                token
                for item in indexes
                for token in lines[item].tokens
            )
            offset = _token_offset(combined, needle)
            if offset is not None:
                end_offset = offset + len(needle)
                token_ranges: list[tuple[int, int] | None] = []
                cursor = 0
                for item in indexes:
                    line = lines[item]
                    overlap_start = max(offset, cursor)
                    overlap_end = min(end_offset, cursor + len(line.tokens))
                    if overlap_start >= overlap_end or not line.token_words:
                        token_ranges.append(None)
                    else:
                        local_start = overlap_start - cursor
                        local_end = overlap_end - cursor
                        first_word = line.token_words[local_start]
                        last_word = line.token_words[local_end - 1] + 1
                        token_ranges.append((first_word, last_word))
                    cursor += len(line.tokens)
                token_candidates.append((indexes, tuple(token_ranges)))
                break
    if len(token_candidates) == 1:
        indexes, ranges = token_candidates[0]
        return _Match(
            "unique_tokens",
            "unique_contiguous_tokens",
            0.95,
            indexes,
            ranges,
        )
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
    allowed_pages_by_evidence: dict[int, frozenset[int]] | None = None,
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
            line_tokens: list[str] = []
            token_words: list[int] = []
            if line.words:
                for word_index, word in enumerate(line.words):
                    word_tokens = _tokens(word.text)
                    line_tokens.extend(word_tokens)
                    token_words.extend([word_index] * len(word_tokens))
            else:
                line_tokens.extend(_tokens(normalized))
            stored_lines.append(_StoredLine(
                source_line_id,
                line,
                normalized,
                tuple(line_tokens),
                tuple(token_words),
            ))

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
                allowed_pages=(allowed_pages_by_evidence or {}).get(evidence_id),
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
            token_range = (
                match.token_ranges[ordinal]
                if ordinal < len(match.token_ranges)
                else None
            )
            conn.execute(
                """
                INSERT INTO source_evidence_lines(
                    evidence_id, source_line_id, ordinal, token_start, token_end
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    stored_lines[index].source_line_id,
                    ordinal,
                    token_range[0] if token_range else None,
                    token_range[1] if token_range else None,
                ),
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
            evidence_ids = {int(row["evidence_id"]) for row in evidence_rows}
            allowed_pages_by_evidence: dict[int, set[int]] = {}
            if evidence_ids:
                owner_rows = conn.execute(
                    """
                    SELECT owner.evidence_id, pages.page_number
                      FROM (
                            SELECT evidence_id, statement_id FROM transactions
                            UNION SELECT evidence_id, statement_id FROM position_snapshots
                            UNION SELECT evidence_id, statement_id FROM cash_balances
                            UNION SELECT evidence_id, statement_id FROM snapshot_sets
                            UNION SELECT evidence_id, statement_id FROM quarantine_transactions
                            UNION
                            SELECT issue.evidence_id, snapshot.statement_id
                              FROM snapshot_scope_issues issue
                              JOIN snapshot_sets snapshot
                                ON snapshot.snapshot_set_id = issue.snapshot_set_id
                           ) owner
                      JOIN statement_pages pages ON pages.statement_id = owner.statement_id
                     WHERE owner.evidence_id IS NOT NULL
                    """
                ).fetchall()
                for owner in owner_rows:
                    evidence_id = int(owner["evidence_id"])
                    if evidence_id in evidence_ids:
                        allowed_pages_by_evidence.setdefault(evidence_id, set()).add(
                            int(owner["page_number"])
                        )
            conn.execute("SAVEPOINT layout_source")
            try:
                metrics = _write_source_geometry(
                    conn,
                    source_file_id=int(source["source_file_id"]),
                    ingestion_run_id=int(source["active_ingestion_run_id"]),
                    source_sha256=str(source["sha256"]),
                    pdf=pdf,
                    evidence_rows=evidence_rows,
                    allowed_pages_by_evidence={
                        evidence_id: frozenset(pages)
                        for evidence_id, pages in allowed_pages_by_evidence.items()
                    },
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
