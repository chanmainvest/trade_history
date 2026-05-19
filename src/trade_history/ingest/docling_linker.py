"""Link transactions to their source docling elements via text similarity."""

from __future__ import annotations

import json
import logging
import re
import sqlite3

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z0-9.,$]+")


def _tokenize(text: str) -> set[str]:
    """Tokenize text into a set of lowercase alphanumeric tokens."""
    return {t.lower() for t in _TOKEN_RE.findall(text)} if text else set()


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _extract_docling_elements(doc_dict: dict) -> list[dict]:
    """Extract all text elements and table cells from a docling JSON dict.

    Returns list of dicts with keys: self_ref, text, page.
    """
    elements: list[dict] = []

    # Texts (body items)
    for item in doc_dict.get("texts", []):
        text = item.get("text", "")
        if not text or len(text) < 5:
            continue
        self_ref = item.get("self_ref", "")
        prov = item.get("prov", [])
        page = prov[0].get("page_no", 0) if prov else 0
        elements.append({"self_ref": self_ref, "text": text, "page": page})

    # Tables — extract cell texts
    for table in doc_dict.get("tables", []):
        table_ref = table.get("self_ref", "")
        prov = table.get("prov", [])
        table_page = prov[0].get("page_no", 0) if prov else 0

        data = table.get("data", {})
        if not isinstance(data, dict):
            continue
        grid = data.get("grid", [])
        table_cells = data.get("table_cells", [])

        for row_idx, row in enumerate(grid):
            row_texts = []
            for cell in row:
                if isinstance(cell, dict):
                    row_texts.append(cell.get("text", ""))
                elif isinstance(cell, str):
                    row_texts.append(cell)
            row_text = " ".join(t for t in row_texts if t)
            if len(row_text) < 5:
                continue

            # Collect ALL cell refs in this row
            cell_refs = []
            for ci, tc in enumerate(table_cells):
                if tc.get("start_row_offset_idx") == row_idx and tc.get("text", "").strip():
                    cell_refs.append(f"{table_ref}/cells/{ci}")

            if not cell_refs:
                cell_refs = [table_ref]

            elements.append({
                "self_ref": cell_refs[0],
                "all_refs": cell_refs,
                "text": row_text,
                "page": table_page,
            })

    return elements


def link_transactions_to_docling(
    conn: sqlite3.Connection,
    statement_id: int,
    doc_dict: dict,
    threshold: float = 0.35,
) -> int:
    """Link transactions to docling elements by matching raw_text.

    Returns the number of transactions linked.
    """
    # Get unlinked transactions for this statement
    rows = conn.execute(
        """SELECT id, raw_text FROM transactions
           WHERE statement_id = ? AND raw_text IS NOT NULL AND docling_ref IS NULL""",
        (statement_id,),
    ).fetchall()

    if not rows:
        return 0

    # Build docling element index
    elements = _extract_docling_elements(doc_dict)
    if not elements:
        return 0

    # Pre-tokenize all elements
    elem_tokens = [(e, _tokenize(e["text"])) for e in elements]

    linked = 0
    for row in rows:
        tx_id = row["id"]
        raw_text = row["raw_text"]
        if not raw_text:
            continue

        tx_tokens = _tokenize(raw_text)
        if not tx_tokens:
            continue

        # Find best matching element
        best_score = 0.0
        best_elem = None
        for elem, etokens in elem_tokens:
            score = _jaccard(tx_tokens, etokens)
            if score > best_score:
                best_score = score
                best_elem = elem

        if best_score >= threshold and best_elem:
            refs_json = json.dumps(best_elem["all_refs"])
            conn.execute(
                "UPDATE transactions SET docling_ref = ?, docling_page = ? WHERE id = ?",
                (refs_json, best_elem["page"], tx_id),
            )
            linked += 1

    conn.commit()
    log.info(
        "Linked %d/%d transactions to docling elements for statement %d",
        linked, len(rows), statement_id,
    )
    return linked
