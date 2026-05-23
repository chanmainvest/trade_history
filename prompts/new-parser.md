# Prompt skill: draft a new institution parser

> **Status:** Runtime-supported draft workflow. `POST
> /statements/draft-parser` writes a complete prompt bundle under
> `data/parser_drafts/<sha>/` and, when explicitly requested, sends it to
> the configured LLM provider and saves the response for review.

When a statement type is encountered that no existing parser handles,
this prompt is what the Settings upload workflow sends to the configured
LLM (OpenAI / Anthropic / Google — chosen in the Settings tab) after the
user explicitly enables provider sending. Without provider sending, the
same endpoint creates a local prompt bundle only.

## Inputs the runtime should provide

* `folder_name` — the on-disk folder the PDF is in
  (e.g. `Questrade RRSP`).
* `pdf_text` — full text from `ledger.pdf_text.extract_pdf`, page by
  page, with line numbers preserved.
* `existing_parsers` — list of parser names already registered, so the
  LLM doesn't duplicate names.
* `schema_sql` — contents of `src/ledger/db/schema.sql` (the column
  shapes the parser must produce).
* `types_py` — contents of `src/ledger/parsers/types.py` (the
  dataclasses to emit).
* `example_parser` — the smallest existing parser (currently
  `hsbc.py`) as a worked example.

## The prompt

```
You are writing a Python module that parses one Canadian retail
broker's monthly PDF statement. You will produce a single file at
`src/ledger/parsers/<name>.py`.

Hard requirements:
- Implement the Parser protocol: class attributes NAME, VERSION;
  methods can_handle(folder_name, first_page_text) -> bool and
  parse(pdf: PdfText) -> ParseResult.
- Return list[ParsedStatement] inside ParseResult — multi-account and
  multi-period PDFs are common.
- Emit only the dataclasses defined in parsers/types.py. Use only the
  literals in TxnType.
- Store all money in native currency. Set the currency field on every
  transaction, position, and cash balance.
- Never fabricate a number. Lines you can't confidently parse go into
  ParsedStatement.quarantine with (raw_line, reason).
- The parser must be deterministic and side-effect free (no DB writes,
  no network).

Style:
- Use `re` for line patterns. Keep regex small and readable.
- Helper utilities from parsers/helpers.py: parse_money, parse_date,
  parse_option_expiry, _MONTH_ABBR.
- For unknown security names, call parsers.name_resolver.resolve_ticker
  first; if it returns None fall back to a synthetic symbol via
  synthetic_symbol(desc).

You have the following inputs:
- PDF text (line-numbered, multi-page).
- The existing schema.sql.
- The existing types.py.
- An example parser file (hsbc.py).

Produce:
1. The new parser file content.
2. A pytest fixture file under tests/fixtures/<name>/ that contains a
   minimal text-dump representative of the PDF.
3. A tests/test_<name>.py covering at least one buy, sell, dividend,
   option event, and a cash-balance row.

If the PDF text appears to be from an institution that already has a
parser, DO NOT produce a new one — return the existing parser's name
instead, in a comment at the top of the response.
```

## Where reviewed output should be installed

* `src/ledger/parsers/<name>.py`
* `tests/fixtures/<name>/<file>.txt`
* `tests/test_<name>.py`

After a human reviews the LLM response and installs the files, the follow-up
change must:

1. Add the institution to `config.INSTITUTIONS`.
2. Register the parser in `parsers/registry.py`.
3. Re-run `uv run pytest -q` and surface any failures back to the user
   for review *before* committing.
