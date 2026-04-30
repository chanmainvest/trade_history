"""Fallback 'generic' parser used when no institution-specific parser claims a PDF.

It produces an empty ParseResult and logs a quarantine entry — enough to
keep ingestion moving while real parsers are implemented.
"""
from __future__ import annotations

from ..pdf_text import PdfText
from .registry import register
from .types import ParseResult


class GenericParser:
    NAME = "generic"
    VERSION = "0.0.1"

    def can_handle(self, folder_name: str, first_page_text: str) -> bool:  # noqa: ARG002
        return False  # never auto-claim; only used as last-resort manual fallback

    def parse(self, pdf: PdfText) -> ParseResult:  # noqa: ARG002
        return ParseResult(
            parser_name=self.NAME,
            parser_version=self.VERSION,
            errors=["no parser registered for this PDF"],
        )


register(GenericParser())
