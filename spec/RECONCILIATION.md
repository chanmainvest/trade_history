# Reconciliation and holdings

This document owns movement rules, checkpoint reconciliation, and holdings at
a date. The current implementation does not yet perform financial
reconciliation despite using that word for its link-building command.

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
- all other types and missing quantity contribute zero.

These rules compensate for inconsistent parser signs. Missing quantity
silently producing zero is a known limitation.

## Current `ingest reconcile`

The command performs two operations:

1. It rebuilds automatically generated transfer pairs. Candidates need
   opposite direction, different accounts, equal absolute cash amount or the
   same `instrument_id`/quantity/currency, and dates within seven days.
   Equally near candidates are skipped as ambiguous.
2. It deletes and rebuilds `position_transaction_links`, assigning every
   non-zero same-account/**same-instrument-ID** movement since the previous
   snapshot to the current snapshot.

It does not compare quantities, cash, statement totals, or persist a residual
or status. Instrument duplication prevents many economically identical rows
from matching.

## Current Monthly holdings

For securities, `/monthly/snapshot` chooses the latest position date per
account at or before the requested day, treats all rows at that date as the
account checkpoint, and adds later transaction quantity deltas keyed by
`instrument_id`. Before any checkpoint it uses `initial_positions` plus
movements. Cost/price/value come from the anchor row when one exists.

For cash, it independently chooses the latest balance per account/currency and
adds later non-corporate-action `net_amount` values using trade date. It falls
back to `initial_cash` before the first checkpoint. Native totals are primary;
available DuckDB FX rates also produce CAD/USD combined totals.

Known failures:

- snapshot completeness is assumed at account/date scope;
- canonical identity is absent, so checkpoint and movement IDs may differ;
- a movement-only row has no anchor price/value;
- diff keys omit currency and canonical identity; and
- settlement-date cash semantics are not represented.

## Other holdings consumers

Performance has a separate state machine that clears an account's prior
securities whenever any later account checkpoint date appears, then
forward-fills values. Visualisation routes have their own holdings queries.
Therefore Monthly, Performance, and Visualisations are not guaranteed to agree.

## Initial rows

`ingest infer-initials` derives pre-history positions/cash from the earliest
snapshot minus prior parsed movements. Tagged inferred rows are replaceable;
user-curated rows are intended to survive. Because extraction and signs are
currently unreliable, inference must be rerun only after the canonical ledger
rebuild and must never be used to mask a reconciliation residual.

## Target statuses (not implemented)

The new engine will reconcile within explicit complete statement scopes using
canonical identities and normalized deltas. Results will distinguish at least
`balanced`, `unbalanced`, `incomplete`, and `not_applicable`, preserve component
links, and feed one shared `holdings_at()` service used by all consumers.
