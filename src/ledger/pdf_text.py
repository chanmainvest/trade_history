"""PDF text extraction utilities.

Primary path: pdfplumber. Fallback: pypdf. If both yield empty text the file
is flagged image-only and skipped.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pdfplumber
from pypdf import PdfReader


@dataclass
class PdfText:
    relpath: str
    page_count: int
    pages: list[str]
    sha256: str
    size_bytes: int

    @property
    def is_image_only(self) -> bool:
        joined = "".join(self.pages).strip()
        return len(joined) < 20

    @property
    def full_text(self) -> str:
        return "\n".join(self.pages)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_pdf(path: Path, *, repo_root: Path) -> PdfText:
    pages: list[str] = []
    page_count = 0
    try:
        with pdfplumber.open(str(path)) as pdf:
            page_count = len(pdf.pages)
            for p in pdf.pages:
                t = p.extract_text() or ""
                pages.append(t)
    except Exception:
        pages = []

    if not pages or all(not p.strip() for p in pages):
        try:
            reader = PdfReader(str(path))
            page_count = len(reader.pages)
            pages = [(p.extract_text() or "") for p in reader.pages]
        except Exception:
            pages = pages or []

    rel = str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    return PdfText(
        relpath=rel,
        page_count=page_count,
        pages=pages,
        sha256=sha256_of(path),
        size_bytes=path.stat().st_size,
    )
