"""Helpers for committed synthetic statement-text fixtures."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ledger.pdf_text import PdfText

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(relpath: str) -> PdfText:
    path = FIXTURES / relpath
    text = path.read_text(encoding="utf-8")
    chunks = text.split("----- PAGE BREAK -----")
    pages: list[str] = []
    for index, chunk in enumerate(chunks):
        lines = chunk.splitlines()
        if index == 0:
            lines = [line for line in lines if not line.startswith("# ")]
        pages.append("\n".join(lines).strip())
    return PdfText(
        relpath=f"tests/fixtures/{relpath}",
        page_count=len(pages),
        pages=pages,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        size_bytes=len(text.encode("utf-8")),
    )
