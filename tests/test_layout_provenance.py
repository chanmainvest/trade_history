"""Shared PDF layout/provenance model regression tests."""
from __future__ import annotations

from ledger.parsers.layout import SourceLocator, normalize_layout_text
from ledger.pdf_text import PdfLine, PdfText, PdfWord


def test_source_locator_preserves_coordinate_bearing_word_evidence():
    words = (
        PdfWord("Buy", 10, 20, 25, 30),
        PdfWord("AAA", 30, 20, 50, 30),
    )
    pdf = PdfText(
        relpath="synthetic.pdf",
        page_count=1,
        pages=["Buy AAA"],
        sha256="synthetic",
        size_bytes=7,
        page_words=[list(words)],
        page_lines=[[
            PdfLine(
                page_number=1,
                line_number=1,
                text="Buy AAA",
                x0=10,
                top=20,
                x1=50,
                bottom=30,
                words=words,
            )
        ]],
    )

    span = SourceLocator(pdf).span_for("Buy AAA", parser_rule="test:row")

    assert span is not None
    assert span.page_number == 1
    assert span.line_number == 1
    assert span.bbox == (10.0, 20.0, 50.0, 30.0)
    assert span.words == [
        {"text": "Buy", "x0": 10, "top": 20, "x1": 25, "bottom": 30},
        {"text": "AAA", "x0": 30, "top": 20, "x1": 50, "bottom": 30},
    ]


def test_layout_normalization_handles_unicode_without_mutating_evidence():
    assert normalize_layout_text("A\u00a0B\u2014C\u2212D") == "A B-C-D"


def test_layout_lines_keeps_page_line_fallback_when_only_some_pages_have_words():
    pdf = PdfText(
        relpath="synthetic.pdf",
        page_count=2,
        pages=["Coordinate row", "Fallback row"],
        sha256="synthetic",
        size_bytes=25,
        page_lines=[[PdfLine(page_number=1, line_number=1, text="Coordinate row")], []],
    )

    assert [(line.page_number, line.line_number, line.text) for line in pdf.layout_lines] == [
        (1, 1, "Coordinate row"),
        (2, 1, "Fallback row"),
    ]
