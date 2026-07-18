"""PDF text extraction utilities.

Primary path: pdfplumber. Fallback: pypdf. If both yield empty text the file
is flagged image-only and skipped.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from pypdf import PdfReader


@dataclass(frozen=True)
class PdfWord:
    """One word extracted from a PDF page with its original coordinates."""

    text: str
    x0: float
    top: float
    x1: float
    bottom: float

    def as_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "x0": self.x0,
            "top": self.top,
            "x1": self.x1,
            "bottom": self.bottom,
        }


@dataclass(frozen=True)
class PdfLine:
    """A stable page-local line assembled from extracted words."""

    page_number: int
    line_number: int
    text: str
    x0: float | None = None
    top: float | None = None
    x1: float | None = None
    bottom: float | None = None
    words: tuple[PdfWord, ...] = ()

    @property
    def bbox(self) -> tuple[float, float, float, float] | None:
        if None in (self.x0, self.top, self.x1, self.bottom):
            return None
        return (float(self.x0), float(self.top), float(self.x1), float(self.bottom))

    @property
    def word_dicts(self) -> list[dict[str, object]] | None:
        return [word.as_dict() for word in self.words] if self.words else None


@dataclass
class PdfText:
    relpath: str
    page_count: int
    pages: list[str]
    sha256: str
    size_bytes: int
    page_words: list[list[PdfWord]] = field(default_factory=list)
    page_lines: list[list[PdfLine]] = field(default_factory=list)
    page_sizes: list[tuple[float, float] | None] = field(default_factory=list)

    @property
    def is_image_only(self) -> bool:
        joined = "".join(self.pages).strip()
        return len(joined) < 20

    @property
    def full_text(self) -> str:
        return "\n".join(self.pages)

    @property
    def layout_lines(self) -> list[PdfLine]:
        """Return coordinate-bearing lines, with deterministic text fallback.

        Fixtures and pypdf fallback extraction do not have word coordinates.
        They still receive page/line provenance so parsers can use one source
        span contract without fabricating coordinates.
        """
        lines: list[PdfLine] = []
        for page_index, page in enumerate(self.pages, start=1):
            extracted = (
                self.page_lines[page_index - 1]
                if page_index <= len(self.page_lines)
                else []
            )
            if extracted:
                lines.extend(extracted)
                continue
            lines.extend(
                PdfLine(
                    page_number=page_index,
                    line_number=line_index,
                    text=line,
                )
                for line_index, line in enumerate(page.splitlines(), start=1)
                if line.strip()
            )
        return lines


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _page_layout(page, page_number: int) -> tuple[list[PdfWord], list[PdfLine]]:
    """Extract words and reconstruct visual lines without altering raw text."""
    try:
        extracted_words = page.extract_words(
            use_text_flow=True,
            keep_blank_chars=False,
        ) or []
    except Exception:
        return [], []

    words: list[PdfWord] = []
    for raw in extracted_words:
        text = str(raw.get("text", "")).strip()
        if not text:
            continue
        try:
            words.append(
                PdfWord(
                    text=text,
                    x0=float(raw["x0"]),
                    top=float(raw["top"]),
                    x1=float(raw["x1"]),
                    bottom=float(raw["bottom"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not words:
        return [], []

    # pdfplumber's `top` can differ by a fraction of a point across words in
    # the same visual line. Two points leaves normal font variation intact but
    # does not collapse adjacent table rows.
    rows: list[list[PdfWord]] = []
    for word in sorted(words, key=lambda item: (item.top, item.x0, item.bottom)):
        if not rows or abs(word.top - rows[-1][0].top) > 2.0:
            rows.append([word])
        else:
            rows[-1].append(word)

    lines: list[PdfLine] = []
    for line_number, row in enumerate(rows, start=1):
        ordered = tuple(sorted(row, key=lambda item: item.x0))
        lines.append(
            PdfLine(
                page_number=page_number,
                line_number=line_number,
                text=" ".join(word.text for word in ordered),
                x0=min(word.x0 for word in ordered),
                top=min(word.top for word in ordered),
                x1=max(word.x1 for word in ordered),
                bottom=max(word.bottom for word in ordered),
                words=ordered,
            )
        )
    return words, lines


def extract_pdf(
    path: Path,
    *,
    repo_root: Path,
    include_layout: bool = False,
) -> PdfText:
    pages: list[str] = []
    page_words: list[list[PdfWord]] = []
    page_lines: list[list[PdfLine]] = []
    page_sizes: list[tuple[float, float] | None] = []
    page_count = 0
    try:
        with pdfplumber.open(str(path)) as pdf:
            page_count = len(pdf.pages)
            for page_number, p in enumerate(pdf.pages, start=1):
                t = p.extract_text() or ""
                words, lines = _page_layout(p, page_number) if include_layout else ([], [])
                pages.append(t or "\n".join(line.text for line in lines))
                page_words.append(words)
                page_lines.append(lines)
                page_sizes.append((float(p.width), float(p.height)))
    except Exception:
        pages = []
        page_words = []
        page_lines = []
        page_sizes = []

    if not pages or all(not p.strip() for p in pages):
        try:
            reader = PdfReader(str(path))
            page_count = len(reader.pages)
            pages = [(p.extract_text() or "") for p in reader.pages]
            page_words = [[] for _ in pages]
            page_lines = [[] for _ in pages]
            page_sizes = [
                (float(page.mediabox.width), float(page.mediabox.height))
                for page in reader.pages
            ]
        except Exception:
            pages = pages or []

    rel = str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    return PdfText(
        relpath=rel,
        page_count=page_count,
        pages=pages,
        sha256=sha256_of(path),
        size_bytes=path.stat().st_size,
        page_words=page_words,
        page_lines=page_lines,
        page_sizes=page_sizes,
    )
