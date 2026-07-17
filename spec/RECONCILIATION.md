# Reconciliation and holdings

This document owns movement rules, checkpoint reconciliation, and holdings at
a date. `ledger ingest reconcile` now writes deterministic, source-linked
checkpoint equations; it never creates a balancing transaction or changes a
reported balance.

## Persisted checkpoint equations

Each generated result belongs to a current `snapshot_set`, its statement, and
the statement's ingestion run. Position and cash components point to the exact
transaction rows used in the equation; those rows in turn point to source
evidence. A result can therefore be traced back to a source statement without
copying statement contents into logs.

### Positions

For each chronological `(account, currency, position scope)` checkpoint pair,
the engine compares the union of canonical `instrument_key` values in the
prior and current scopes:

```text
expected_close_quantity = prior_reported_quantity
                        + SUM(position_delta after prior checkpoint through current)
position_residual        = current_reported_quantity - expected_close_quantity
```

An instrument missing from a **complete** current scope has a reported close of
zero. A newly reported instrument starts from zero. The engine only evaluates
the equation when both scopes are complete. A first checkpoint is
`missing_prior_checkpoint`; an incomplete current or immediate prior scope is
`incomplete_input`. Transactions which could affect a position but lack an
instrument or a usable quantity/delta also make the interval incomplete rather
than silently contributing zero.

For an empty scope, one scope-level result is stored. It records the same
missing-prior or incomplete condition, or `not_applicable` when both complete
checkpoints are known empty and no unresolved movement is present.

### Cash

The direct cash equation is evaluated for each cash scope with a printed
opening and closing balance:

```text
expected_close_cash = reported_opening_cash + SUM(cash_delta)
cash_residual       = reported_closing_cash - expected_close_cash
```

Cash movements are selected by account, currency, and the statement's calendar
period using `cash_effective_date` (falling back to `trade_date` for legacy
rows), not merely by the statement that contains the trade. This preserves the
broker-specific settlement contract: a trade shown in one statement can be a
cash component of the next statement if it settles there. A missing cash effect
or printed opening makes the direct result `incomplete_input`.
Known non-cash corporate-action row types are excluded unless their cash effect
is represented as a separate cash transaction; they are not treated as a zero
balancing adjustment.

A second, independent cash result compares the prior scoped closing balance to
the current printed opening balance. Its expected close is the prior reported
close and its summed delta is zero. That continuity check is
`missing_prior_checkpoint` for the first scope and `incomplete_input` whenever
one of the adjacent scopes or balances is unavailable.

### Statement totals

Where `snapshot_sets.reported_total` is present, the engine stores a
`statement_total` result:

```text
reported securities total ~= SUM(position market values in that scope)
reported cash total       ~= cash closing balance in that scope
reported portfolio total  ~= matching complete position scope + cash scope
```

Portfolio-summary totals only use matching statement/currency/scope-key
components. Missing, incomplete, or unpriced components remain
`incomplete_input`; the engine does not guess that a partial section is a full
portfolio.

## Result status and tolerance

`reconciliation_results` uses these statuses:

- `reconciled` — absolute residual at most `1e-9`;
- `within_rounding` — non-zero residual within the documented tolerance;
- `unexplained_residual` — a calculable residual outside tolerance;
- `incomplete_input` — a scope, required balance, or required movement is
  unavailable or untrusted;
- `missing_prior_checkpoint` — the first comparison has no prior scope;
- `ambiguous_transfer` — reserved for conservative transfer-pairing outcomes;
  and
- `not_applicable` — a known-empty scope or total has no applicable equation.

Position tolerance is `1e-8`; cash and statement-total tolerance is one cent.
These are rounding tolerances, not a mechanism for absorbing missing rows.
`unexplained_residual` rows retain a reason with the residual and tolerance;
incomplete rows retain the missing-input reason.

## `ingest reconcile` and rebuild behavior

The CLI command performs three separate derived-data passes:

1. pair unambiguous transfer counterparts;
2. rebuild `position_transaction_links` for complete position scopes; and
3. replace generated `recon:v1:*` result rows and their components with the
   position, cash, and total equations described above.

The generated-key prefix preserves any future reviewed/manual result rows with
another key. Rebuilding is deterministic and idempotent: it deletes only the
previous generated result set before writing the same equations again. Normal
`ledger ingest run` invokes these passes after an active source scan; the
standalone command is useful after manual database maintenance.

Transfer pairing remains separate from checkpoint reconciliation. Candidates
must have opposite direction, different accounts, equal absolute cash amount or
the same canonical instrument/quantity/currency, and dates within seven days.
Equally near candidates are skipped as ambiguous. A journal such as DLR/DLR.U
requires a reviewed relation; reconciliation never treats different canonical
keys as identical just to make an equation balance.

## Quantity movement rules

`quantity.quantity_delta()` converts transaction type plus parsed quantity:

- buys, buy-to-cover, transfer-in, reinvestment, split credit, and option buys
  use `abs(quantity)`;
- sells, shorts, transfer-out, split debit, and option sells use
  `-abs(quantity)`;
- assignment, exercise, and expiration use `-quantity`;
- journals preserve their printed signed quantity; and
- all other types and missing quantity contribute zero in the legacy helper.

New writes store `transactions.position_delta` and preserve the reported
quantity separately. A generic split, name change, spinoff, or merger with no
explicit normalized effect remains absent rather than receiving the legacy
zero. The reconciliation engine treats a missing or underdetermined
position-affecting effect as incomplete.

## Canonical holdings service

`ledger.holdings.holdings_at()` is the read-only source of truth for Monthly,
Performance, and all Visualisation holdings/symbol selection. It returns one
row per `(account_id, instrument_key, currency)` with a stable `holding_key`.
`/monthly/diff` and the React table use that key, so a CAD and USD position with
the same display symbol cannot overwrite one another.

For securities, the service chooses the latest **complete** position scope per
`(account, currency, scope_key)` on or before the requested day and adds later
normalized position movements by canonical instrument key. A complete empty
scope clears only that scope. A partial/unknown later scope does not clear the
prior anchor; it is returned as an `incomplete_position_scope_after_checkpoint`
quality warning. Before a complete checkpoint the service uses the latest
`initial_positions` row plus later movements.

Transactions do not have a snapshot-scope field. If several complete scopes
for one account/currency are candidates, the service does not fan one movement
out across them; it leaves the quantities unchanged and marks the affected
rows incomplete with an ambiguity warning.

Cash is reconstructed independently from a complete cash scope plus later
non-corporate-action `cash_delta` values by `cash_effective_date` (falling back
to `trade_date` for legacy rows). It falls back to `initial_cash` before the
first complete cash checkpoint. Native totals remain primary; Monthly can add
dated DuckDB FX totals for presentation.

Every holding includes `checkpoint_date`, checkpoint statement/scope IDs,
`is_reported`/`is_reconstructed`, `holding_state`, reconciliation status/reason,
`price_date`, `price_status`, and `quality_warnings`. At an exact statement
checkpoint it preserves broker-reported price/value. Afterward it uses the
latest available market close (falling back to adjusted close only where that
is all the market store contains) on or before the requested date. Option
contracts are not repriced from an underlying equity quote. If no applicable
market price exists, the service may use a clearly marked stale checkpoint
price/value; otherwise the holding is unpriced. A post-checkpoint position
movement leaves cost basis and unrealized P/L unavailable rather than
recomputing them from an old average cost.

Performance evaluates the same service on each complete checkpoint date and
optionally today, so its carried-forward state follows the same scoped rules.
Visualisation sector, correlation, and RRG symbol selection call the same
service. Monthly displays checkpoint/provenance, holding state, reconciliation,
and compact scope/price warnings, while Verify exposes the underlying
statement-level scope and reconciliation evidence. These are read-only views;
they do not change the ledger or mask an incomplete input.

Remaining limits:

- parser v2 can declare recognized complete scopes, but existing active/live
  rows predate that work and remain `unknown` until a reviewed re-ingest or
  shadow rebuild gives the live ledger trusted checkpoints; and
- DuckDB price identity is still symbol/date only, so exchange/currency-specific
  market-price disambiguation remains a data-model limitation.

## Initial rows

`ingest infer-initials` derives pre-history positions/cash only from the
earliest **complete** snapshot minus prior parsed movements. Tagged inferred
rows are replaceable; user-curated rows are intended to survive. Because
extraction and signs still require source review, inference must be rerun only
after the canonical ledger rebuild and must never be used to mask a
reconciliation residual.
