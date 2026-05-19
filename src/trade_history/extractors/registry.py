"""Extractor registry: maps PDF files to the correct StatementExtractor."""

from __future__ import annotations

import logging
from pathlib import Path

from trade_history.extractors.base import StatementExtractor, UnknownStatementError
from trade_history.extractors.utils import get_first_page_text, get_text_via_ocr, is_image_pdf

log = logging.getLogger(__name__)


class ExtractorRegistry:
    _extractors: list[type[StatementExtractor]] = []

    @classmethod
    def register(cls, extractor_cls: type[StatementExtractor]) -> type[StatementExtractor]:
        """Decorator: register an extractor class."""
        cls._extractors.append(extractor_cls)
        return extractor_cls

    @classmethod
    def resolve(cls, pdf_path: Path) -> StatementExtractor:
        """Return an instantiated extractor for the given PDF, or raise.

        Falls back to docling OCR for image-based PDFs.
        """
        first_page = get_first_page_text(pdf_path)

        # Try text-based matching first
        for ext_cls in cls._extractors:
            if ext_cls.can_handle(pdf_path, first_page):
                return ext_cls()

        # If pdfplumber returned empty/corrupt text, try OCR
        if is_image_pdf(pdf_path):
            log.info("Attempting OCR for image-based PDF: %s", pdf_path.name)
            ocr_text = get_text_via_ocr(pdf_path)
            if ocr_text:
                for ext_cls in cls._extractors:
                    if ext_cls.can_handle(pdf_path, ocr_text):
                        return ext_cls()

        raise UnknownStatementError(
            f"No extractor found for: {pdf_path.name}\n"
            f"First-page snippet: {first_page[:300]!r}"
        )

    @classmethod
    def list_extractors(cls) -> list[str]:
        return [f"{e.__module__}.{e.__name__}" for e in cls._extractors]
