# Reconciliation and holdings

This document owns movement rules, checkpoint reconciliation, and holdings at
a date. Schema support for financial reconciliation exists, but the current
command still performs link-building rather than calculating residuals.

## Intended equations

For one account and canonical instrument between complete checkpoints:

```text
expected_close_quantity = prior_close_quantity + SUM(position_delta)
position_residual        = reported_close_quantity - expected_close_quantity
```

For one account/currency cash scope:

```text
expected_close_cash = opening_or_prior_close + SUM(cash_delta)
cash_residual        = reported_close_cash - expected_close_cash
```

A result needs prior/reported values, included movements, expected close,
residual, tolerance, status, and incomplete/unresolved reasons. No residual may
be hidden with a fabricated adjustment.

## Current quantity rules

`quantity.quantity_delta()` converts transaction type plus parsed quantity:

- buys, buy-to-cover, transfer-in, reinvestment, split credit, and option buys
  use `abs(quantity)`;
- sells, shorts, transfer-out, split debit, and option sells use
  `-abs(quantity)`;
- assignment, exercise, and expiration use `-quantity`;
- journals preserve their printed signed quantity; and
- all other types and missing quantity contribute zero in the legacy helper.

New writes store `transactions.position_delta` and preserve the reported
quantity separately. Missing position quantity is represented as absent in the
new writer; the helper remains for legacy/manual compatibility.

## Current `ingest reconcile`

The command performs two operations:

1. It rebuilds automatically generated transfer pairs. Candidates need
   opposite direction, different accounts, equal absolute cash amount or the
   same `instrument_id`/quantity/currency, and dates within seven days.
   Equally near candidates are skipped as ambiguous.
2. It deletes and rebuilds `position_transaction_links`, assigning every
   non-zero same-account/**same-instrument-ID** movement since the previous
   snapshot to the current snapshot.

It does not compare quantities, cash, or statement totals and does not yet
write `reconciliation_results`. That table can now persist residual/status
records without a balancing adjustment. Transfer pairing and position links
now compare canonical `instrument_key` values rather than vulnerable raw
instrument-ID equality.

## Current Monthly holdings

For securities, `/monthly/snapshot` chooses the latest **complete** position
scope per account/currency at or before the requested day and adds later
normalized (or legacy-derived) quantity deltas by canonical instrument key.
Partial/unknown scopes cannot clear an earlier complete scope. Before a
complete checkpoint it uses `initial_positions` plus movements. Cost/price/
value come from the anchor row when one exists.

For cash, it independently chooses the latest complete cash scope and adds
later non-corporate-action `cash_delta` values using `cash_effective_date`
(falling back to legacy fields). It falls back to `initial_cash` before the
first complete checkpoint. Native totals are primary; available DuckDB FX rates
also produce CAD/USD combined totals.

Known failures:

- a movement-only row has no anchor price/value;
- Performance and visualisation still have separate holdings engines; and
- parser v1 scopes are `unknown`, so a shadow rebuild/parser repair is needed
  before the live ledger gains trusted complete checkpoints.

## Other holdings consumers

Performance has a separate state machine that clears an account's prior
securities whenever any later account checkpoint date appears, then
forward-fills values. Visualisation routes have their own holdings queries.
Therefore Monthly, Performance, and Visualisations are not guaranteed to agree.

## Initial rows

`ingest infer-initials` derives pre-history positions/cash only from the
earliest **complete** snapshot minus prior parsed movements. Tagged inferred
rows are replaceable; user-curated rows are intended to survive. Because
extraction and signs are currently unreliable, inference must be rerun only
after the canonical ledger rebuild and must never be used to mask a
reconciliation residual.

## Persisted result vocabulary; engine pending

`reconciliation_results` accepts `reconciled`, `within_rounding`,
`unexplained_residual`, `incomplete_input`, `missing_prior_checkpoint`,
`ambiguous_transfer`, and `not_applicable`, with optional transaction
components. Phase 5 will calculate these deterministically within explicit
complete scopes and Phase 6 will move all consumers to one holdings service.
