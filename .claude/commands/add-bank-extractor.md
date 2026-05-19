# /add-bank-extractor

Create a new brokerage statement extractor for a previously unsupported institution.

## What this command does

When invoked, I will:

1. **Gather information** by asking you for:
   - Institution name (e.g. "National Bank Direct")
   - Sample PDF path(s)
   - Account type(s) (margin, TFSA, RRSP, etc.)
   - Primary currency

2. **Analyse the PDF** by running docling on the sample(s) and displaying the raw text structure. I will identify:
   - Header signature line (for `can_handle`)
   - Account ID pattern (regex)
   - Statement period line format
   - Activity section headers and column layout
   - Option syntax (if any)
   - Currency section separators (if multi-currency)
   - Opening/closing balance line format

3. **Generate the extractor module** following the patterns below.

4. **Update the package** `src/trade_history/extractors/__init__.py` to import the new module.

5. **Generate tests** and run them with `uv run pytest`.

## Usage

```
/add-bank-extractor
```

Then follow the interactive prompts. Or provide arguments directly:

```
/add-bank-extractor --institution "National Bank" --pdf "/path/to/sample.pdf" --account-type margin
```

## Directory structure

Create the extractor at:

```
src/trade_history/extractors/<institution_slug>/
├── __init__.py           ← empty
└── <product_slug>.py     ← @ExtractorRegistry.register class
```

Then add the import to `src/trade_history/extractors/__init__.py`:

```python
from trade_history.extractors.<institution_slug> import <product_slug>  # noqa: F401
```

## Base classes reference

Every extractor subclasses `StatementExtractor` and yields tuples of `(RawStatement, [RawTransaction], [RawPosition])`:

```python
# src/trade_history/extractors/base.py

@dataclass
class RawStatement:
    institution: str
    account_id: str
    account_type: str           # 'margin' | 'tfsa' | 'rrsp' | 'managed'
    primary_currency: str       # 'CAD' | 'USD'
    period_start: date
    period_end: date
    source_file: Path
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None

@dataclass
class RawTransaction:
    date: date
    activity: str               # normalised in pipeline
    description: str
    amount: Decimal             # positive = credit, negative = debit
    currency: str
    raw_text: str
    settle_date: date | None = None
    symbol: str | None = None
    quantity: Decimal | None = None
    price: Decimal | None = None
    commission: Decimal = Decimal("0")

@dataclass
class RawPosition:
    description: str
    quantity: Decimal
    currency: str
    asset_type: str             # 'equity' | 'option' | 'mutual_fund' | 'etf'
    symbol: str | None = None
    book_cost: Decimal | None = None
    market_price: Decimal | None = None
    market_value: Decimal | None = None

class StatementExtractor(ABC):
    INSTITUTION: ClassVar[str]

    @classmethod
    @abstractmethod
    def can_handle(cls, pdf_path: Path, first_page_text: str) -> bool: ...

    @abstractmethod
    def extract(self, pdf_path: Path) -> Iterator[tuple[RawStatement, list[RawTransaction], list[RawPosition]]]: ...
```

## Minimal extractor template

Use this as the starting scaffold. Copy and adapt from the reference examples below.

```python
"""<Institution> <Product> extractor."""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

from trade_history.extractors.base import (
    RawPosition, RawStatement, RawTransaction, StatementExtractor,
)
from trade_history.extractors.registry import ExtractorRegistry
from trade_history.extractors.utils import (
    convert_pdf_via_docling,
    is_valid_account_id, parse_amount, parse_date_flexible,
    parse_quantity, parse_short_date,
)

# --- Signature: unique string on first page that identifies this institution ---
_HANDLE_SIG = "Unique Header Text Here"

# --- Account ID regex ---
_ACCOUNT_RE = re.compile(r"\b(\d{3}-\d{5})\b")

# --- Period regex ---
_PERIOD_RE = re.compile(r"Statement period\s+(.+?)\s+to\s+(.+)")

# --- Transaction date prefix ---
_TX_DATE_RE = re.compile(r"^([A-Za-z]{3,4})\s+(\d{1,2})\s+(.+)$")

# --- Balance line ---
_BALANCE_RE = re.compile(
    r"(Opening|Closing)\s+[Bb]alance\s+\$?([\d,]+\.?\d*)",
    re.IGNORECASE,
)

# --- Lines to skip (headers, footers, page breaks) ---
_SKIP_RE = re.compile(
    r"^(DATE\b|DESCRIPTION\b|Opening\s*Balance|Closing\s*Balance|Page\s+\d)",
    re.IGNORECASE,
)

# --- Activity inference ---
_ACTIVITY_MAP = [
    ("bought", "bought"),
    ("sold", "sold"),
    ("dividend", "dividend"),
    ("interest", "interest"),
    ("fee", "fee"),
    ("transfer", "transfer_in"),
    ("deposit", "contribution"),
    ("withdrawal", "withdrawal"),
    ("withhel", "withholding_tax"),  # matches both "withholding" and "withheld"
]


@ExtractorRegistry.register
class InstitutionProduct(StatementExtractor):
    INSTITUTION = "InstitutionName"

    @classmethod
    def can_handle(cls, pdf_path: Path, first_page_text: str) -> bool:
        return _HANDLE_SIG in first_page_text

    def extract(
        self, pdf_path: Path
    ) -> Iterator[tuple[RawStatement, list[RawTransaction], list[RawPosition]]]:
        full_text, self._docling_dict = convert_pdf_via_docling(pdf_path)

        account_id = self._extract_account_id(full_text, pdf_path)
        period_start, period_end = self._extract_period(full_text)
        txs, ob, cb = self._parse_transactions(full_text, period_end)
        positions = self._parse_positions(full_text)

        stmt = RawStatement(
            institution=self.INSTITUTION,
            account_id=account_id,
            account_type="margin",
            primary_currency="CAD",
            period_start=period_start,
            period_end=period_end,
            source_file=pdf_path,
            opening_balance=ob,
            closing_balance=cb,
        )
        yield stmt, txs, positions

    def _extract_account_id(self, text: str, pdf_path: Path) -> str:
        m = _ACCOUNT_RE.search(text)
        if m and is_valid_account_id(m.group(1)):
            return m.group(1)
        return "UNKNOWN"

    def _extract_period(self, text: str) -> tuple[date, date]:
        m = _PERIOD_RE.search(text)
        if m:
            start = parse_date_flexible(m.group(1))
            end = parse_date_flexible(m.group(2))
            if start and end:
                return start, end
        return date(2000, 1, 1), date(2000, 1, 1)

    def _parse_transactions(
        self, text: str, period_end: date
    ) -> tuple[list[RawTransaction], Decimal | None, Decimal | None]:
        transactions: list[RawTransaction] = []
        opening_balance: Decimal | None = None
        closing_balance: Decimal | None = None

        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # Capture balance values from skip lines
            bm = _BALANCE_RE.search(stripped)
            if bm:
                bal_type, bal_val = bm.groups()
                bal = parse_amount(bal_val)
                if "opening" in bal_type.lower():
                    opening_balance = bal
                else:
                    closing_balance = bal

            if _SKIP_RE.match(stripped):
                continue

            m = _TX_DATE_RE.match(stripped)
            if not m:
                continue

            mon_str, day_str, rest = m.groups()

            # IMPORTANT: check skip patterns on `rest` too (after date extraction)
            # This catches "Dec31 ClosingBalance $9,907" where the date prefix
            # prevents _SKIP_RE from matching the full line
            if _SKIP_RE.match(rest.strip()):
                bm2 = _BALANCE_RE.search(rest)
                if bm2:
                    bal_type, bal_val = bm2.groups()
                    bal = parse_amount(bal_val)
                    if "opening" in bal_type.lower():
                        opening_balance = bal
                    else:
                        closing_balance = bal
                continue

            tx_date = parse_short_date(
                mon_str[:3], int(day_str),
                period_end.year, period_end.month,
            )
            if not tx_date:
                continue

            tx = self._parse_tx_line(rest, tx_date, "CAD", stripped)
            if tx:
                transactions.append(tx)

        return transactions, opening_balance, closing_balance

    def _parse_tx_line(
        self, rest: str, tx_date: date, currency: str, raw_text: str
    ) -> RawTransaction | None:
        tokens = rest.split()
        numeric_end: list[str] = []
        for tok in reversed(tokens):
            if re.match(r"^\(?\$?[\d,]+\.?\d*\)?$", tok):
                numeric_end.insert(0, tok)
            else:
                break

        if not numeric_end:
            return None

        amount = parse_amount(numeric_end[-1])
        quantity = parse_quantity(numeric_end[-3]) if len(numeric_end) >= 3 else None
        price = parse_quantity(numeric_end[-2]) if len(numeric_end) >= 2 else None
        description = " ".join(tokens[: len(tokens) - len(numeric_end)]).strip()
        if not description:
            return None

        activity = self._infer_activity(description)

        # Apply sign: debit activities = negative amount
        if activity in {"bought", "fee", "withdrawal", "withholding_tax"}:
            amount = -abs(amount)
        else:
            amount = abs(amount)

        return RawTransaction(
            date=tx_date,
            activity=activity,
            description=description,
            amount=amount,
            currency=currency,
            raw_text=raw_text,
            quantity=quantity,
            price=price,
        )

    def _infer_activity(self, description: str) -> str:
        lower = description.lower()
        for key, val in _ACTIVITY_MAP:
            if key in lower:
                return val
        return "other"

    def _parse_positions(self, text: str) -> list[RawPosition]:
        positions: list[RawPosition] = []
        # TODO: implement holdings parsing for this institution
        return positions
```

## Reference: existing extractors

### CIBC Imperial Service (simplest — mutual funds only)

File: `src/trade_history/extractors/cibc/imperial_service.py`

Key patterns:
- Signature: `"Imperial Investor Service"`
- Period: `"August 1-August 31, 2021"` → regex `([A-Za-z]+)\s+(\d{1,2})\s*[-–]\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})`
- Transactions: only captures reinvestments and fees
- Positions: mutual fund regex `([A-Z][A-Za-z &]+(?:Fund|Portfolio)...)\s+units\s+price\s+value`

### CIBC Investors Edge (margin + TFSA, equities + options)

File: `src/trade_history/extractors/cibc/investors_edge.py`

Key patterns:
- Signature: `"Investors Edge"` or `"Investor's Edge"`
- Account regex: `\b(\d{3}-\d{5})\b`
- Transactions: date prefix `"Aug 25 BOUGHT ..."`, trailing numerics = `[qty, price, amount]`
- Options: `parse_cibc_option()` — format `"PUT AG JAN 19 2024 6"`
- Activity map: keyword-based inference from description
- Balance: `"Opening cash balance $X"` / `"Closing cash balance $X"`

### HSBC InvestDirect (CAD + USD sub-accounts)

File: `src/trade_history/extractors/hsbc/investdirect.py`

Key patterns:
- Signature: `"InvestDirect"` and `"HSBC"`
- Yields TWO tuples — one for CAD section, one for USD section
- Date format: pdfplumber `"Dec31"` (no space) vs docling `"Dec 31"` (with space) — regex uses `\s*` between month and day
- **Critical bug fix**: balance lines like `"Dec31 ClosingBalance $9,907"` start with a date prefix, so `_SKIP_RE` doesn't match the full line. Must also check `_SKIP_RE.match(rest.strip())` after extracting the date.
- Options: TD-style compact format `"PUT -100 TECK.B'23 SP@40"` → `parse_td_option()` first, then `parse_hsbc_option()` legacy fallback
- Option-only lines (expiry/buyback with just quantity, no price/amount): single trailing numeric treated as quantity, amount=0
- Activity map includes: expire, eps (electronic payment), non-res tax, internal tfr, assigned
- Strips trailing reference numbers (13+ digit codes like `"2023061620003662"`)

### RBC Direct Investing (two-line period header)

File: `src/trade_history/extractors/rbc/direct_investing.py`

Key patterns:
- Signature: `"RBC Direct Investing"`
- Period: TWO-LINE header — `"Order Execution Only AUG. 31"` + `"Cdn. Dollar Statement 2021"`
- Month abbreviations: uses `"SEPT"` (4 chars) not just `"SEP"` — regex needs `[A-Z]{3,4}`
- PDFs concatenate words without spaces: `"CAMECOCORP"`, `"ClosingBalance"`
- Currency sections: `"CDN. DOLLAR"` / `"U.S. DOLLAR"` split
- Options: `parse_rbc_option()` — format `"CALL SHOP 01/20/23 700"`

### TD WebBroker (format changes across years)

File: `src/trade_history/extractors/td/webbroker.py`

Key patterns:
- Signature: `"TD Direct Investing"` or `"TD Waterhouse"`
- Period formats vary by year:
  - 2016: `"Statement for Jan 1 to Jan 31, 2016"`
  - 2017+: `"Sep 1, 2017 to Sep 30, 2017"`
  - 2023+: `"For the period ending Jan 31, 2023"`
- Options: `parse_td_option()` — format `"CALL-100 CNQ'25 JA@50"`
- TD market suffix: `"PAAS'27-US JA@80"` — regex handles `-US` after year

## Available utility functions

From `src/trade_history/extractors/utils.py`:

| Function | Purpose |
|---|---|
| `convert_pdf_via_docling(pdf_path)` | Convert PDF via docling → `(plain_text, docling_dict)`; uses cached JSON from DB if available; falls back to pdfplumber |
| `cache_docling_json(pdf_path, doc_dict)` | Pre-load a docling JSON dict so `convert_pdf_via_docling()` skips re-running docling |
| `text_from_docling_dict(doc_dict)` | Reconstruct plain text from a stored docling JSON dict |
| `get_first_page_text(pdf_path)` | Extract text from first page via pdfplumber (for `can_handle()`) |
| `parse_amount("1,234.56")` | Parse dollar amount → `Decimal` |
| `parse_quantity("100")` | Parse share quantity → `Decimal` |
| `parse_short_date("Aug", 25, 2021, 8)` | Month abbrev + day → `date` (handles year boundary) |
| `parse_date_flexible("August 25, 2021")` | Parse various date formats → `date` |
| `is_valid_account_id("588-93738")` | Validate account ID (>= 6 alphanumeric chars) |
| `parse_cibc_option(desc)` | CIBC option format → `OptionInfo` |
| `parse_rbc_option(desc)` | RBC option format → `OptionInfo` |
| `parse_td_option(desc)` | TD/HSBC option format → `OptionInfo` |
| `strip_markdown(md_text)` | Strip markdown formatting from docling output to plain text |
| `get_text_via_ocr(pdf_path)` | Docling OCR fallback for image-based PDFs |
| `is_image_pdf(pdf_path)` | Detect image-based PDFs (empty pdfplumber text) |

## Gotchas and lessons learned

1. **Balance lines that start with dates**: Some PDFs have `"Dec31 ClosingBalance $9,907"` — the date prefix means `_SKIP_RE` won't match the full line. Always check `_SKIP_RE.match(rest)` AFTER extracting the date.

2. **Keyword matching**: `"withhold"` does NOT match `"withheld"` — use `"withhel"` to match both forms.

3. **Month abbreviations**: Some institutions use 4-char months (e.g. `"SEPT"` not `"SEP"`). Use `[A-Z]{3,4}` in regexes.

4. **Concatenated words**: RBC/HSBC PDFs often concatenate words without spaces: `"CAMECOCORP"`, `"Statementperiod"`. Handle this in regexes.

5. **SQLite UNIQUE with NULLs**: `NULL != NULL` in SQL, so `UNIQUE(a, b, c)` won't prevent duplicates when b/c are NULL. The normalizer handles this with SELECT-before-INSERT for equity instruments.

6. **Account ID validation**: Short numeric tokens like `"1234"` can match street addresses. Always use `is_valid_account_id()` which requires >= 6 alphanumeric chars.

7. **Multi-currency**: If the statement has CAD + USD sections, yield one tuple per currency. See HSBC extractor for reference.

8. **Unresolvable rows**: Send to `quarantine_transactions`, never force-fit into main tables.

9. **Docling text differs from pdfplumber**: Docling adds proper spacing where pdfplumber concatenates (e.g. `"Jun 2"` vs `"Jun2"`). Date and section regexes should use `\s*` or `\s+` between tokens to handle both formats. Docling also renders tables with `|` separators which `strip_markdown()` converts to space-separated values.

10. **Docling JSON caching**: The pipeline pre-loads stored docling JSON from the database on `--force` re-ingest so docling doesn't re-run. Your extractor doesn't need to handle this — `convert_pdf_via_docling()` manages the cache transparently.

## Test template

```python
"""Tests for <Institution> <Product> extractor."""

import pytest
from trade_history.extractors.<slug>.<product> import <ClassName>

class Test<ClassName>:
    def test_can_handle_positive(self):
        assert <ClassName>.can_handle(Path("dummy.pdf"), "... Unique Header Text ...")

    def test_can_handle_negative(self):
        assert not <ClassName>.can_handle(Path("dummy.pdf"), "Some Other Bank")

    def test_parse_period(self):
        ext = <ClassName>()
        start, end = ext._extract_period("Statement period Jan 1 to Jan 31, 2024")
        assert start == date(2024, 1, 1)
        assert end == date(2024, 1, 31)

    def test_parse_transaction_line(self):
        ext = <ClassName>()
        tx = ext._parse_tx_line(
            "BOUGHT 100 AAPL 150.00 15,000.00",
            date(2024, 1, 15), "CAD", "Jan 15 BOUGHT 100 AAPL 150.00 15,000.00"
        )
        assert tx is not None
        assert tx.activity == "bought"
        assert tx.amount < 0  # debit
```

## Notes

- Account IDs must pass `is_valid_account_id()` (>= 6 alphanumeric chars)
- Use `convert_pdf_via_docling()` for extraction — it returns plain text + docling JSON dict; the JSON is stored in `statement_registry.docling_json` and `data/docling_json/` by the pipeline. On re-ingest, cached JSON is pre-loaded from the DB to skip re-running docling.
- `can_handle()` still uses pdfplumber via `get_first_page_text()` for fast signature matching
- Options must be stored with `asset_type='option'` and full option fields
- Unresolvable rows go to `quarantine_transactions`, never force-fit into main tables
- Multi-currency accounts: yield one `(RawStatement, txs, positions)` tuple per currency section
- Always extract opening/closing balance for statement_registry validation
