"""Shared extraction utilities: PDF text, option parsing, amount parsing."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)

# ── Markdown stripping ────────────────────────────────────────────────────────

# Table separator: |---|---|---| or | :--- | :---: |
_TABLE_SEP_RE = re.compile(r"^\|?[\s:]*[-]+[\s:]*(\|[\s:]*[-]+[\s:]*)+\|?\s*$")


def strip_markdown(md_text: str) -> str:
    """Strip markdown formatting to produce plain text similar to pdfplumber output.

    Removes: headers (#), bold/italic (*), table pipes (|), horizontal rules (---),
    links [text](url), and bullet markers. Preserves all content text.
    """
    lines: list[str] = []
    for line in md_text.splitlines():
        # Skip horizontal rules
        if re.match(r"^[-=]{3,}\s*$", line):
            continue
        # Skip table separator rows
        if _TABLE_SEP_RE.match(line):
            continue
        # Strip header markers
        line = re.sub(r"^#{1,6}\s+", "", line)
        # Strip bold/italic
        line = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", line)
        # Strip markdown links
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        # Handle table rows: strip pipes, keep cell contents
        if "|" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
            line = "  ".join(cells)
        # Strip bullet markers
        line = re.sub(r"^[\s]*[-*+]\s+", "", line)
        lines.append(line)
    return "\n".join(lines)


# ── PDF text extraction ────────────────────────────────────────────────────────

@lru_cache(maxsize=64)
def get_first_page_text(pdf_path: Path) -> str:
    """Return plain text of the first page (cached).

    Uses pdfplumber for fast can_handle() resolution.
    """
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        if pdf.pages:
            return pdf.pages[0].extract_text() or ""
    return ""


def get_all_text(pdf_path: Path) -> str:
    """Return plain text of all pages concatenated (pdfplumber fallback)."""
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        return "\n".join(
            (page.extract_text() or "") for page in pdf.pages
        )


def iter_pages(pdf_path: Path) -> Iterator[tuple[int, str]]:
    """Yield (page_num, text) for all pages."""
    import pdfplumber

    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            yield i, (page.extract_text() or "")


_docling_converter = None
_docling_ocr_converter = None

# Per-file cache: pipeline stores a docling dict here before calling extract()
# so convert_pdf_via_docling() can skip re-running docling.
_docling_json_cache: dict[str, dict] = {}


def cache_docling_json(pdf_path: str, doc_dict: dict) -> None:
    """Pre-load a docling JSON dict so convert_pdf_via_docling() can reuse it."""
    _docling_json_cache[pdf_path] = doc_dict


def _get_accelerator_options():
    """Return AcceleratorOptions with CUDA if available, else CPU."""
    try:
        import torch
        from docling.datamodel.pipeline_options import AcceleratorOptions
        if torch.cuda.is_available():
            log.info("CUDA available — using GPU acceleration for docling")
            return AcceleratorOptions(device="cuda")
        log.info("CUDA not available — using CPU for docling")
        return AcceleratorOptions(device="cpu")
    except ImportError:
        log.debug("torch or AcceleratorOptions not available — using docling defaults")
        return None


def _get_docling_converter(*, with_ocr: bool = False):
    """Return a cached DocumentConverter singleton.

    Args:
        with_ocr: If True, return a converter with OCR enabled (for image PDFs).
                  If False, return a fast converter without OCR/table structure.
    """
    global _docling_converter, _docling_ocr_converter
    if with_ocr:
        if _docling_ocr_converter is None:
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            accel = _get_accelerator_options()
            ocr_opts = PdfPipelineOptions(
                do_ocr=True,
                do_table_structure=True,
                **({"accelerator_options": accel} if accel else {}),
            )
            _docling_ocr_converter = DocumentConverter(
                format_options={"pdf": PdfFormatOption(pipeline_options=ocr_opts)},
            )
        return _docling_ocr_converter

    if _docling_converter is None:
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        accel = _get_accelerator_options()
        pipeline_opts = PdfPipelineOptions(
            do_ocr=False,
            do_table_structure=True,
            **({"accelerator_options": accel} if accel else {}),
        )
        _docling_converter = DocumentConverter(
            format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_opts)},
        )
    return _docling_converter


def convert_pdf_via_docling(pdf_path: Path) -> tuple[str, dict | None]:
    """Convert a PDF via docling. Returns (plain_text, docling_dict).

    If a cached docling dict was pre-loaded via cache_docling_json(),
    the cached version is used instead of re-running docling.

    Uses docling as the primary extraction engine. The returned text is
    markdown-stripped to produce plain text similar to pdfplumber output,
    keeping existing regex parsers compatible.

    Falls back to pdfplumber if docling is unavailable or fails.
    """
    # Check per-file cache (populated by pipeline from database)
    cache_key = str(pdf_path)
    cached = _docling_json_cache.pop(cache_key, None)
    if cached is not None:
        plain_text = text_from_docling_dict(cached)
        if plain_text.strip():
            log.info(
                "Using cached docling JSON for %s (%d chars)",
                pdf_path.name, len(plain_text),
            )
            return plain_text, cached

    try:
        converter = _get_docling_converter(with_ocr=False)
        result = converter.convert(str(pdf_path))
        md_text = result.document.export_to_markdown()
        doc_dict = result.document.export_to_dict()
        plain_text = strip_markdown(md_text)
        if plain_text.strip():
            log.info(
                "docling extracted %d chars from %s", len(plain_text), pdf_path.name
            )
            return plain_text, doc_dict
    except ImportError:
        log.warning("docling not installed; falling back to pdfplumber for %s", pdf_path.name)
    except Exception as exc:
        log.warning("docling failed for %s: %s; falling back to pdfplumber", pdf_path.name, exc)

    # Fallback to pdfplumber
    return get_all_text(pdf_path), None


def text_from_docling_dict(doc_dict: dict) -> str:
    """Reconstruct plain text from a stored docling JSON dict.

    Uses DoclingDocument.model_validate() to rebuild the document, then
    exports to markdown and strips formatting for regex parser compatibility.
    """
    from docling_core.types.doc import DoclingDocument

    doc = DoclingDocument.model_validate(doc_dict)
    md_text = doc.export_to_markdown()
    return strip_markdown(md_text)


def get_text_via_ocr(pdf_path: Path) -> str:
    """Extract text from an image-based PDF using docling with CUDA acceleration.

    Returns the full document text (markdown-stripped), or empty string on failure.
    """
    try:
        converter = _get_docling_converter(with_ocr=True)
        result = converter.convert(str(pdf_path))
        text = result.document.export_to_markdown()
        if text and text.strip():
            stripped = strip_markdown(text)
            log.info("OCR extracted %d chars from %s", len(stripped), pdf_path.name)
            return stripped
    except ImportError:
        log.warning("docling not installed — cannot OCR %s", pdf_path.name)
    except Exception as exc:
        log.warning("OCR failed for %s: %s", pdf_path.name, exc)
    return ""


def is_image_pdf(pdf_path: Path) -> bool:
    """Check if a PDF is image-based (pdfplumber returns empty/corrupt text)."""
    text = get_first_page_text(pdf_path)
    # Image-based PDFs return empty text or just "(cid:N)" character references
    if not text:
        return True
    stripped = text.strip()
    if not stripped or stripped.startswith("(cid:"):
        return True
    return False


# ── Amount parsing ─────────────────────────────────────────────────────────────

_AMOUNT_STRIP_RE = re.compile(r"[\$,\s]")


def parse_amount(raw: str) -> Decimal:
    """
    Parse a formatted dollar amount string to Decimal.
    Handles: '$1,234.56', '(1,234.56)', '-1,234.56', '1234.56'
    Parentheses indicate negative (HSBC convention).
    Trailing '-' also indicates negative (RBC short positions).
    """
    raw = raw.strip()
    if not raw or raw in ("•", "–", "-", "N/D", "N/A"):
        return Decimal("0")
    # Trailing minus (RBC short quantities like "2,000-")
    trailing_neg = raw.endswith("-") and not raw.startswith("-")
    # Parentheses negative (HSBC)
    paren_neg = raw.startswith("(") and raw.endswith(")")
    cleaned = _AMOUNT_STRIP_RE.sub("", raw).strip("()").rstrip("-")
    if not cleaned:
        return Decimal("0")
    try:
        value = Decimal(cleaned)
        return -value if (paren_neg or trailing_neg) else value
    except InvalidOperation:
        return Decimal("0")


def parse_quantity(raw: str) -> Decimal | None:
    """Parse a quantity string (commas, trailing minus, parentheses for short positions)."""
    raw = raw.strip()
    if not raw or raw in ("•", "–", "-", "N/D", "N/A", "SEG"):
        return None
    # Parentheses = negative (HSBC short positions: "(10)")
    paren_neg = raw.startswith("(") and raw.endswith(")")
    trailing_neg = raw.endswith("-") and not raw.startswith("-")
    cleaned = raw.replace(",", "").strip("()").rstrip("-").strip()
    if not cleaned:
        return None
    try:
        val = Decimal(cleaned)
        return -val if (paren_neg or trailing_neg) else val
    except InvalidOperation:
        return None


# ── Description normalization ─────────────────────────────────────────────────

_ACTIVITY_WORDS = {
    "BOUGHT", "SOLD", "BUY", "SELL", "PURCHASE", "PURCHASED",
    "DIVIDEND", "DIV", "REINVESTMENT", "REINVEST", "REINVESTED", "DIVREIN",
    "TRANSFER", "TRANSFERRED", "EXERCISE", "EXERCISED",
    "ASSIGNMENT", "ASSIGNED", "EXPIRED", "EXPIRY", "EXPIRATION",
    "CONTRIBUTION", "DEPOSIT", "WITHDRAWAL", "FEE", "COMMISSION",
    "INTEREST", "WITHHOLDING", "TAX", "JOURNALLED",
    "TO", "FROM", "IN", "OUT", "OF", "THE", "AND", "FOR", "AS",
    "CDN", "USD", "CAD", "US", "ELECTRONIC", "FUNDS",
    "UNSOLICITED", "OPTION",
    # Month abbreviations — appear as date qualifiers (e.g. "AS OF JAN") but not company names
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "SEPT", "OCT", "NOV", "DEC",
}

_TRAILING_NUMERIC_RE = re.compile(r"^[\d,.$()%+-]+$")
# TD broker appends lot-tracking codes like "EZ-640338" or "BT-725813" to stock names
_TRAILING_LOT_CODE_RE = re.compile(r"^[A-Z]{1,3}-\d{5,7}$")


def normalize_description(description: str) -> str | None:
    """
    Strip activity verbs and trailing numeric/lot-code tokens from a transaction
    description to produce a stable fallback symbol for instrument upsert.

    Returns None if nothing meaningful remains.
    """
    if not description:
        return None

    tokens = description.split()

    # Strip trailing numeric tokens and lot codes (quantities, prices, "EZ-640338", etc.)
    while tokens and (
        _TRAILING_NUMERIC_RE.match(tokens[-1])
        or _TRAILING_LOT_CODE_RE.match(tokens[-1])
    ):
        tokens.pop()

    # Strip activity words and qualifiers
    cleaned = [t for t in tokens if t.upper().strip(".,():") not in _ACTIVITY_WORDS]

    # Strip any lot codes now exposed at the tail after activity-word removal
    while cleaned and _TRAILING_LOT_CODE_RE.match(cleaned[-1]):
        cleaned.pop()

    if not cleaned:
        return None

    result = " ".join(cleaned).strip()
    if len(result) > 40:
        result = result[:40].rstrip()

    return result if result else None


# ── Date parsing ───────────────────────────────────────────────────────────────

_MONTH_MAP: dict[str, int] = {
    # Full names
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    # 3-letter abbreviations (standard)
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    # TD 2-letter month codes (some statements use "fb" for February)
    "ja": 1, "fe": 2, "fb": 2, "mr": 3, "ap": 4, "my": 5, "jn": 6,
    "jl": 7, "au": 8, "sp": 9, "oc": 10, "nv": 11, "dc": 12,
}

# Short date pattern used by CIBC, TD, HSBC: "Aug 25", "Jan 1", "Dec 31"
_SHORT_DATE_RE = re.compile(r"^([A-Za-z]{3,})\s+(\d{1,2})$")

# RBC date pattern: "AUG.10" or "APR. 02" or "AUG. 31"
_RBC_DATE_RE = re.compile(r"^([A-Z]{3})\.?\s*(\d{1,2})$")


def parse_date_flexible(raw: str) -> date | None:
    """Try several common date formats; return None on failure."""
    raw = raw.strip()
    formats = [
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%d-%b-%Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%b. %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
    ]
    from datetime import datetime

    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_short_date(month_str: str, day: int, period_year: int, period_month: int) -> date | None:
    """
    Parse a 'Mon D' date (no year) by inferring year from the statement period.
    Handles year rollover: a Dec date in a Jan statement = prior year, and vice versa.
    """
    month = _MONTH_MAP.get(month_str.lower())
    if not month:
        return None
    year = period_year
    # Year rollover at boundaries
    if period_month <= 2 and month >= 11:
        year -= 1
    elif period_month >= 11 and month <= 2:
        year += 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


# ── Option parsing ─────────────────────────────────────────────────────────────

class OptionInfo(NamedTuple):
    put_call: str       # 'call' | 'put'
    root: str           # underlying ticker
    expiry: date
    strike: Decimal
    multiplier: int


# CIBC activity format: "CALL .BCE JAN 19 2024 50" or "PUT AG JAN 19 2024 6"
# (type, optional-dot, root, month, day, year, strike)
_CIBC_OPT_RE = re.compile(
    r"^(CALL|PUT)\s+\.?([A-Z]+)\s+([A-Z]{3})\s+(\d{1,2})\s+(\d{4})\s+([\d.]+)",
    re.IGNORECASE,
)
# Same pattern without ^ — used to find CIBC option details embedded in raw_text
# (e.g. "Apr 24 Sold PUT AG JAN 19 2024 6 10 0.200 ...")
_CIBC_OPT_SEARCH_RE = re.compile(
    r"(CALL|PUT)\s+\.?([A-Z0-9]+)\s+([A-Z]{3,4})\s+(\d{1,2})\s+(\d{4})\s+([\d.]+)",
    re.IGNORECASE,
)

# HSBC format: "PUT -100 BCE'23 SP@58"
_HSBC_OPT_RE = re.compile(
    r"^(CALL|PUT)\s+[-\d]+\s+(\w+)'(\d{2})\s+([A-Z]{2})@([\d.]+)",
    re.IGNORECASE,
)

# RBC holdings format: "PUT .BCE 09/20/24 50" (dot = Canadian underlying)
# Also handles long/short indicator after strike: "PUT .BCE 09/20/24 50 20-"
_RBC_OPT_RE = re.compile(
    r"^(CALL|PUT)\s+\.?([A-Z]+)\s+(\d{2}/\d{2}/\d{2,4})\s+([\d.]+)",
    re.IGNORECASE,
)
# Same without ^ anchor — used to find RBC option details embedded in raw_text
# (e.g. "NOV. 12 BOUGHT CALL .BCE 03/21/25 40 30 1.04 3,157.50")
_RBC_OPT_SEARCH_RE = re.compile(
    r"(CALL|PUT)\s+\.?([A-Z]+)\s+(\d{2}/\d{2}/\d{2,4})\s+([\d.]+)",
    re.IGNORECASE,
)

# CIBC no-strike format: "PUT BHP DEC 17 2021 -20 1.000 ..." (negative qty follows year)
# In older CIBC statements the strike price is absent; use Decimal("0") as placeholder.
_CIBC_OPT_NOSTRIKE_RE = re.compile(
    r"(CALL|PUT)\s+\.?([A-Z0-9]+)\s+([A-Z]{3,4})\s+(\d{1,2})\s+(\d{4})\s+-\d",
    re.IGNORECASE,
)

# TD format: "CALL-100 INTC'24 19JA@30" or "PUT -100 NVDA'23 24FB@160"
# Day prefix before month code is optional: "PUT -100 TSM'23 AP@75"
# TD also prepends action words: "Expiration PUT ...", "Exercise Option CALL ...", "Option CALL ..."
# "-?" handles both "CALL-100" (hyphen already in [-\s]) and "PUT -100" (space + explicit minus)
# \s* (not \s+) allows no space between multiplier and root: "PUT -100INTC'25 17JA@17.5"
# [A-Z0-9+$.]+ allows adjusted-option symbols with special chars: "BABA+$"
# (?:-[A-Z]+)? after year handles market suffix: "PAAS'27-US JA@80"
_TD_OPT_RE = re.compile(
    r"^(CALL|PUT)[-\s]-?(\d+)\s*([A-Z0-9+$.]+)'(\d{2})(?:-[A-Z]+)?\s*(?:(\d{1,2})?([A-Z]{2}))@([\d.]+)",
    re.IGNORECASE,
)
_TD_OPT_PREFIX_RE = re.compile(
    r"^(?:Expiration|Expire|Exercise\s+Option|Exercise|Option|Sold|Bought|Sell|Buy|Purchase|Opening|Closing)\s+",
    re.IGNORECASE,
)


def parse_cibc_option(text: str) -> OptionInfo | None:
    m = _CIBC_OPT_RE.match(text.strip()) or _CIBC_OPT_SEARCH_RE.search(text)
    if not m:
        # Older CIBC format with no strike: "PUT BHP DEC 17 2021 -20 1.000 ..."
        m2 = _CIBC_OPT_NOSTRIKE_RE.search(text)
        if m2:
            pc, root, mon, day, yr = m2.groups()
            month = _MONTH_MAP.get(mon.lower())
            if month:
                try:
                    exp = date(int(yr), month, int(day))
                    return OptionInfo(pc.lower(), root.upper(), exp, Decimal("0"), 100)
                except (ValueError, InvalidOperation):
                    pass
        return None
    pc, root, mon, day, yr, strike = m.groups()
    month = _MONTH_MAP.get(mon.lower())
    if not month:
        return None
    try:
        exp = date(int(yr), month, int(day))
        return OptionInfo(pc.lower(), root.upper(), exp, Decimal(strike), 100)
    except (ValueError, InvalidOperation):
        return None


def parse_hsbc_option(text: str) -> OptionInfo | None:
    m = _HSBC_OPT_RE.match(text.strip())
    if not m:
        return None
    pc, root, yr2, mon2, strike = m.groups()
    month = _MONTH_MAP.get(mon2.lower())
    if not month:
        return None
    year = 2000 + int(yr2)
    import calendar

    _, last = calendar.monthrange(year, month)
    exp = date(year, month, last)
    return OptionInfo(pc.lower(), root.upper(), exp, Decimal(strike), 100)


def parse_rbc_option(text: str) -> OptionInfo | None:
    m = _RBC_OPT_RE.match(text.strip()) or _RBC_OPT_SEARCH_RE.search(text)
    if not m:
        return None
    pc, root, exp_str, strike = m.groups()
    exp = parse_date_flexible(exp_str)
    if not exp:
        return None
    try:
        return OptionInfo(pc.lower(), root.upper(), exp, Decimal(strike), 100)
    except InvalidOperation:
        return None


def parse_td_option(text: str) -> OptionInfo | None:
    # Strip TD action prefixes: "Expiration PUT ...", "Exercise Option CALL ...", "Option CALL ..."
    text = _TD_OPT_PREFIX_RE.sub("", text.strip())
    m = _TD_OPT_RE.match(text)
    if not m:
        return None
    pc, mult, root, yr2, day_str, mon2, strike = m.groups()
    month = _MONTH_MAP.get(mon2.lower())
    if not month:
        return None
    year = 2000 + int(yr2)
    day = int(day_str) if day_str else 1
    import calendar

    # Use stated day if available, else last day of month
    if not day_str:
        _, day = calendar.monthrange(year, month)
    try:
        exp = date(year, month, day)
        return OptionInfo(pc.lower(), root.upper(), exp, Decimal(strike), int(mult))
    except (ValueError, InvalidOperation):
        return None


# ── Account-ID validation ──────────────────────────────────────────────────────

def is_valid_account_id(token: str) -> bool:
    """
    Accept tokens with ≥6 alphanumeric chars (hyphens allowed).
    Reject short numeric tokens that could be street addresses.
    """
    stripped = token.replace("-", "")
    if len(stripped) < 6:
        return False
    if stripped.isdigit() and len(stripped) < 7:
        return False  # e.g. "12345" = house number
    return True
