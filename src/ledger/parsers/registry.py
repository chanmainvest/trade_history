"""Parser registry: chooses the right institution parser per PDF folder.

Each parser exposes:
    NAME: str
    VERSION: str
    can_handle(folder_name: str, first_page_text: str) -> bool
    parse(pdf: PdfText) -> ParseResult
"""
from __future__ import annotations

from typing import Callable, Iterable, Protocol

from ..pdf_text import PdfText
from .types import ParseResult


class Parser(Protocol):
    NAME: str
    VERSION: str

    def can_handle(self, folder_name: str, first_page_text: str) -> bool: ...
    def parse(self, pdf: PdfText) -> ParseResult: ...


_REGISTRY: list[Parser] = []


def register(parser: Parser) -> Parser:
    _REGISTRY.append(parser)
    return parser


def all_parsers() -> Iterable[Parser]:
    return list(_REGISTRY)


def select_parser(folder_name: str, pdf: PdfText) -> Parser | None:
    head = pdf.pages[0] if pdf.pages else ""
    for p in _REGISTRY:
        try:
            if p.can_handle(folder_name, head):
                return p
        except Exception:
            continue
    return None
