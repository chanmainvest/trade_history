# Extraction Corner Cases

This note records parser and repair cases that are easy to get wrong when statement text omits a clean ticker or prints signs in broker-specific ways.

## Symbol Resolution

- Prefer explicit statement symbols and option contract fields when printed.
- Use `parsers/name_resolver.py` only for conservative security-name aliases that are defensible from statement text and public ticker conventions.
- When a transaction has a synthetic or missing symbol, `ledger ingest repair-symbols` first tries the known-name resolver, then matches the row to same-statement holdings by description words, quantity hints, currency, and canonical holding symbols.
- Generic activity labels such as `DIVIDEND`, `DISTRIBUTION`, and `DISTRIB.` are placeholders, not instruments. Repair should resolve them from the underlying description or holding row.
- Tax withholding rows often print only `NON-RES TAX WITHHELD`; they should inherit the nearest same-day dividend/distribution/return-of-capital instrument for the same account, statement, and currency.

## Mutual Fund Codes

CIBC mutual fund activity and holding rows often print fund names and class labels, but not fund codes. Do not hardcode those codes in parser mappings.

Use `ingest.fund_lookup.lookup_fund_code()` / `lookup_fund_instrument_id()` instead:

- The first unresolved fund name is recorded in `instrument_identifier_lookups` with `status = 'pending'`.
- A reviewed lookup can be marked `resolved` with `resolved_symbol`, optional `resolved_exchange`, `resolved_name`, `evidence_url`, and notes.
- After a row is resolved, `ledger ingest repair-symbols` rewrites matching mutual-fund transactions and snapshots to the reviewed code.
- Until reviewed, keep the printed fund-name instrument so the row stays auditable and no fabricated fund code enters the ledger.

## Transfers And Journals

- CIBC same-account currency transfers may print quantity signs but no amount, for example `GLOBAL X US DLR CURRENCY -11,850 -- --`. The negative quantity is the outbound leg.
- RBC transfer rows may use a trailing negative such as `5,000-` or text like `TRANSFER TO C$`; both indicate `transfer_out`.
- `TRANSFER FROM ...` with a positive amount is inbound and should remain `transfer_in`.
- For DLR/DLR.U currency journaling, the CAD ticker (`DLR`) can transfer out while the USD ticker (`DLR.U`) transfers in; the later sale of `DLR.U` is the actual USD-side exit.

## Options

- Option transactions must keep option instruments, not be repaired to the underlying equity just because the description also contains the underlying company name.
- Display layers can show the underlying via `COALESCE(option_root, symbol)`, but the database row should retain expiry, strike, type, multiplier, and option root.

## Footer Contamination

Statement page footers and continuation text can append unrelated holdings to an activity description. Resolver matches should prefer the leading transaction description and direct known-name matches before using broad same-statement holding correlation.

## Layout Evidence and Numeric Gaps

- `PdfText` preserves the raw page text and, when `pdfplumber` supplies it,
  page-local word coordinates and reconstructed visual lines. Matching may
  normalize whitespace/dash artifacts, but stored evidence keeps the original
  source text and never invents a box.
- Text-only fixtures and the `pypdf` fallback use deterministic page/line
  evidence with no bounding box or word list. This is weaker evidence, not a
  synthetic coordinate.
- A failed quantity, amount, or closing-cash parse is not a zero. Keep it
  absent and quarantine the candidate source row with its parser rule/span.
- A parser may declare a scope complete only after it recognized the full
  relevant section; a valid closing balance is required for a complete cash
  scope.

## Legacy Bundled Statements

- TD WebBroker bundles use both 2016–2017 `Statement for <month> ...` headers
  and 2018–2022-style full period headers. Split every period before splitting
  CDN/US subaccounts, then aggregate repeated page fragments for the same
  period/account/currency. Later months must not overwrite the first month
  during ingest.
- RBC annual investment performance reports are annual statements with no holdings table. Parse their money-weighted return summaries into `annual_performance_reports`; do not fabricate monthly transactions or position snapshots from those summaries.

## Current Verified Examples

- `AIRBNB INC CL-A` resolves to `ABNB`.
- `PDD HOLDINGS INC ADR` resolves to `PDD`.
- `DISTRIB. TEXAS PACIFIC LAND CORPORATION` resolves to `TPL`.
- `OSISKO GOLD ROYALTIES LTD` resolves to `OR`.
- `SANDSTORM GOLD LTD` resolves to `SAND` in USD and `SSL` in CAD.
- CIBC fund names remain pending fund-code lookups unless reviewed in `instrument_identifier_lookups`.
