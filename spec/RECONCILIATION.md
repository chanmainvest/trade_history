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

## Current Monthly holdings

For securities, `/monthly/snapshot` chooses the latest **complete** position
scope per account/currency at or before the requested day and adds later
normalized (or legacy-derived) quantity deltas by canonical instrument key.
Partial/unknown scopes cannot clear an earlier complete scope. Before a complete
checkpoint it uses `initial_positions` plus movements. Cost/price/value come
from the anchor row when one exists.

For cash, it independently chooses the latest complete cash scope and adds
later non-corporate-action `cash_delta` values using `cash_effective_date`
(falling back to legacy fields). It falls back to `initial_cash` before the
first complete checkpoint. Native totals are primary; available DuckDB FX rates
also produce CAD/USD combined totals.

Known failures remain:

- a movement-only row has no anchor price/value;
- Performance and visualisation still have separate holdings engines; and
- parser v2 can declare recognized complete scopes, but existing active/live
  rows predate that work and remain `unknown` until a reviewed re-ingest or
  shadow rebuild gives the live ledger trusted checkpoints.

## Other holdings consumers

Performance has a separate state machine that clears an account's prior
securities whenever any later account checkpoint date appears, then
forward-fills values. Visualisation routes have their own holdings queries.
Therefore Monthly, Performance, and Visualisations are not guaranteed to agree.

## Initial rows

`ingest infer-initials` derives pre-history positions/cash only from the
earliest **complete** snapshot minus prior parsed movements. Tagged inferred
rows are replaceable; user-curated rows are intended to survive. Because
extraction and signs still require source review, inference must be rerun only
after the canonical ledger rebuild and must never be used to mask a
reconciliation residual.
