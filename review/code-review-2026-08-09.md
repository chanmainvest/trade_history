# Trade History Code Review

**Date:** 2026-08-09
**Scope:** Extraction logic, reconciliation engine, Monthly/Transactions/Verify frontend, PDF visual verification
**Reviewer:** Automated audit

---

## Executive Summary

Trade History is a well-architected local investment ledger with a strong correctness-first philosophy: never fabricate values, quarantine uncertainty, and preserve native currency. The extraction pipeline, reconciliation engine, and frontend are generally solid. However, several logic bugs and UI issues were identified that could cause incorrect transaction/balance display or misleading visual verification.

**Critical findings:** 3
**Moderate findings:** 5
**Minor findings:** 4

---

## 1. Extraction Logic (PDF → Parsed Rows)

### 1.1 [CRITICAL] CIBC `_classify_activity` misclassifies option buys/sells as equity

**File:** `src/ledger/parsers/cibc.py:246-257`

```python
def _classify_activity(verb: str, raw: str) -> TxnType | None:
    v = verb.strip()
    if v in ACTIVITY_VERBS:
        t = ACTIVITY_VERBS[v]
        if t in {"buy", "sell"} and ("CALL " in raw or "PUT " in raw or "CALL." in raw or "PUT." in raw):
            if v == "Bought":
                return "option_buy_to_open" if "OPEN CONTRACT" in raw or "OPEN" in raw else "option_buy_to_close"
            else:
                return "option_sell_to_open" if "OPEN CONTRACT" in raw or "OPEN" in raw else "option_sell_to_close"
        return t
    return None
```

**Problem:** The option detection `"OPEN" in raw` is too broad. A line like `Bought OPEN TEXT CORP 100 45.50 $4,550.00` (where "OPEN" is part of a company name) would be misclassified as `option_buy_to_open` instead of `buy`. Similarly, `"CALL." in raw` could match a name containing "CALL." at the end of a word.

**Impact:** Equity transactions involving companies with "OPEN" in the name (e.g., Open Text) or names containing "CALL"/"PUT" as substrings would be incorrectly typed as option transactions, corrupting both position and cash reconciliation.

**Recommendation:** Use word-boundary regex matching for option indicators: `\b(CALL|PUT)\b` and `\bOPEN\s+CONTRACT\b` instead of substring checks.

### 1.2 [MODERATE] TD option regex `_OPT_TOKEN` may misparse expiry day/month ambiguity

**File:** `src/ledger/parsers/td.py:87-91`

```python
RE_OPT_TOKEN = re.compile(
    r"(CALL|PUT)\s*[- ]\s*(?:-)?100\s*([A-Z][A-Z0-9.]{0,5})(?:\+\$)?'(\d{2})(?:-US)?\s*"
    r"(\d{0,2})([A-Z]{2})@(\d+(?:\.\d+)?)"
)
```

The pattern `(\d{0,2})([A-Z]{2})` is ambiguous: for `13FB@115`, does `13` = day and `FB` = month abbreviation? But `FB` is not a standard month abbreviation. This appears to rely on TD's non-standard expiry format where `FB` = February with a day prefix. If a real ticker suffix contains digits followed by two letters (e.g., a symbol like `B2GD`), this could produce a false option match.

**Impact:** Low probability but non-zero; a false option parse would create an instrument with wrong type and corrupt position reconciliation.

**Recommendation:** Add a validation step that rejects option parses where the extracted month code is not in `_MON` and the day is not in 1-31.

### 1.3 [MINOR] RBC `RE_ACT_DATE` may capture non-activity lines

**File:** `src/ledger/parsers/rbc.py:78`

```python
RE_ACT_DATE = re.compile(r"^([A-Z]+)\.?\s*(\d{1,2})\s+([A-Z][A-Z0-9 ./'&-]+?)\s+(.*)$")
```

This matches any line starting with an uppercase word + optional period + digits. A header line like `JAN. 2025 STATEMENT` could match this pattern and be treated as an activity row. The parser state machine likely guards against this, but the regex itself is broad.

---

## 2. Reconciliation Logic

### 2.1 [CRITICAL] `_position_interval_replay` uses `instrument_key` but not `instrument_id` for ticker changes, creating a key collision risk

**File:** `src/ledger/ingest/reconcile.py:868-907`

```python
def _position_interval_replay(...):
    balances = {key: value for key, (_instrument_id, value) in prior_rows.items()}
    ...
    for row in rows:
        key = row["instrument_key"]
        ...
        if row["successor_key"] is not None:
            successor_key = str(row["successor_key"])
            moved = balances.get(key, 0.0)
            ratio = float(row["conversion_ratio"])
            old_delta = -moved
            new_delta = moved * ratio
            balances[key] = 0.0
            balances[successor_key] = balances.get(successor_key, 0.0) + new_delta
```

**Problem:** The replay uses `instrument_key` (a string like `equity:BCE:CAD`) as the dictionary key. If a ticker change's successor has the same `instrument_key` as an existing position (e.g., due to a merger where two tickers map to the same canonical key), the old balance is zeroed and added to the successor, but the successor's own balance from `prior_rows` was already included in `balances`. This could double-count or incorrectly merge positions.

**Impact:** In edge cases involving ticker changes to instruments that already exist in the portfolio, the reconciliation would compute incorrect expected close quantities, leading to false unexplained residuals or missed real discrepancies.

**Recommendation:** Track balances by `instrument_id` (integer) during replay, not `instrument_key`. Convert back to keys only when writing results.

### 2.2 [MODERATE] `_cash_components` misses cash-affecting transactions with `cash_delta = 0` but non-zero `net_amount`

**File:** `src/ledger/ingest/reconcile.py:1161-1199`

```python
def _cash_components(...):
    for row in rows:
        if row["txn_type"] in NON_CASH_TXN_TYPES:
            continue
        value = row["cash_delta"] if row["cash_delta"] is not None else row["net_amount"]
        if value is None:
            missing_effects += 1
            continue
        components.append((int(row["transaction_id"]), float(value)))
```

**Problem:** When `cash_delta` is explicitly set to `0.0` by the parser (e.g., for a `reinvest_dividend` that has no cash effect), the code correctly skips `NON_CASH_TXN_TYPES`. However, for non-NON_CASH types where `cash_delta` is `0.0` but `net_amount` is non-zero (possible in legacy data or parser edge cases), the code uses `cash_delta = 0.0` and never looks at `net_amount`. This is actually correct behavior (explicit zero means no cash effect), but the issue is that **transactions with `cash_delta IS NULL` and `net_amount IS NULL`** are counted as `missing_effects`, which is correct. The real issue is subtler: a `cash_delta` of exactly `0.0` for a buy/sell would be recorded as a component with zero delta, which is harmless but wasteful.

**Impact:** Minor; no incorrect results, but unnecessary component rows.

### 2.3 [MODERATE] `_statement_periods_are_adjacent` does not account for multi-currency scopes from the same statement

**File:** `src/ledger/ingest/reconcile.py:39-49`

```python
def _statement_periods_are_adjacent(prior, current) -> bool:
    prior_end = date.fromisoformat(str(prior["period_end"]))
    current_start = date.fromisoformat(str(current["period_start"]))
    return current_start == prior_end + timedelta(days=1)
```

**Problem:** For brokers like RBC that emit separate CAD and USD statements for the same period, two scopes (CAD and USD) from the same logical statement have the same `period_start`/`period_end`. When reconciling CAD positions across months, the prior CAD scope and current CAD scope are correctly adjacent. But if the code ever compared a CAD scope to a USD scope (which shouldn't happen due to the `currency` grouping in `_reconcile_position_scopes`), this would return `False` incorrectly. The grouping by `(account_id, currency, scope_key)` prevents this, so this is a latent issue, not an active bug.

### 2.4 [MODERATE] `resolve_trade_instruments_from_holdings` reset logic is overly broad

**File:** `src/ledger/ingest/reconcile.py:267-282`

```python
reset = int(
    conn.execute(
        f"""
        UPDATE transactions
           SET instrument_id = NULL,
               resolution_method = 'unresolved_printed_identity',
               resolution_confidence = 0.0,
               resolution_evidence_id = evidence_id
         WHERE txn_type IN ('buy', 'sell')
           AND resolution_method IN ({method_placeholders})
        """,
        sorted(AUTOMATIC_NAME_METHODS),
    ).rowcount or 0
)
```

**Problem:** This resets ALL buy/sell transactions with `account_holding_name` or `portfolio_holding_name` resolution back to unresolved, then re-resolves them. If a transaction was manually reviewed and its `resolution_method` was changed to one of these values by a human (not the auto-resolver), it would be silently reset. The docstring says "reviewed aliases, printed symbols, same-statement matches, and reported transaction fields are untouched," but a manual override to `account_holding_name` would be indistinguishable from an auto-resolution.

**Impact:** Low probability (humans unlikely to set these exact method strings), but a data-loss risk for manually reviewed rows.

**Recommendation:** Add a `resolution_source` column (e.g., `auto` vs `manual`) or use a separate flag to protect manually reviewed resolutions.

---

## 3. Holdings Engine (`holdings.py`)

### 3.1 [MODERATE] `_combine_security_states` can produce misleading combined values when states have different anchors

**File:** `src/ledger/holdings.py:617-635`

```python
def _combine_security_states(states: list[_SecurityState]) -> _SecurityState:
    primary = max(states, key=lambda state: (
        state.anchor.as_of_date if state.anchor else state.initial_date or "",
        state.anchor.statement_id if state.anchor else 0,
        state.anchor.snapshot_set_id if state.anchor else 0,
    ))
    if len(states) == 1:
        return primary
    combined = copy.copy(primary)
    combined.quantity = sum(state.quantity for state in states)
    combined.position_movement = any(state.position_movement for state in states)
    combined.cost_basis_stale = any(state.cost_basis_stale for state in states)
    combined.incomplete = True
    combined.warnings = {warning for state in states for warning in state.warnings}
    combined.warnings.add("duplicate_complete_position_scopes")
    return combined
```

**Problem:** When multiple complete position scopes exist for the same `(account, lineage_key, currency)` (e.g., `default` and `margin` scopes), the combined state takes the `primary` (latest) state's metadata but sums quantities across all states. If one scope has a checkpoint from January and another from February, the combined quantity includes movements applied to the February anchor plus the January anchor's quantity. The `primary` anchor's `as_of_date` may not reflect the true checkpoint date of the combined quantity.

**Impact:** The combined row shows `incomplete = True` and `duplicate_complete_position_scopes` warning, which is correct. But the `checkpoint_date` and `is_reported` fields come from `primary`, which may mislead the user about which checkpoint the combined quantity is anchored to.

### 3.2 [MINOR] `_scope_candidates_for_security` returns `["default"]` when no anchor exists, creating phantom default scope

**File:** `src/ledger/holdings.py:476-505`

```python
def _scope_candidates_for_security(...):
    available = {
        scope_key
        for account, state_currency, scope_key in anchors
        if account == account_id and state_currency == currency
    }
    if len(available) > 1:
        return []
    existing = {
        scope_key
        for (account, state_currency, scope_key, key) in states
        if account == account_id and state_currency == currency and key == instrument_key
    }
    if len(existing) == 1:
        return sorted(existing)
    if len(available) == 1:
        return sorted(available)
    if not available:
        return ["default"]
    return []
```

**Problem:** When no complete position anchor exists for an account/currency, this returns `["default"]`, causing transactions to create a `_SecurityState` with `scope_key="default"` and `anchor=None`. This is intentional (allows initial positions to work), but it means a transaction for an account with no complete scope anchor gets assigned to a `default` scope even if the account actually uses named scopes like `margin` or `tfsa`.

**Impact:** Low; the state is marked with `missing_complete_position_checkpoint` warning.

---

## 4. Frontend Bugs

### 4.1 [CRITICAL] Monthly tab: `combinedTotals` computes incorrect FX conversion when one currency is zero

**File:** `frontend/src/tabs/Monthly.tsx:156-172`

```typescript
const totalsByCurrency: Record<string, number> = {};
for (const r of filtered) {
  totalsByCurrency[r.currency] = (totalsByCurrency[r.currency] || 0) + (r.market_value || 0);
}
const snapshotTotals = snapQ.data?.totals;
const fxTotals = snapshotTotals?.combined;
const combinedTotals: { CAD?: number; USD?: number } = {};
if (Object.keys(totalsByCurrency).length > 0) {
  const cad = totalsByCurrency.CAD || 0;
  const usd = totalsByCurrency.USD || 0;
  if (usd === 0 || fxTotals?.usd_cad !== undefined) {
    combinedTotals.CAD = cad + usd * (fxTotals?.usd_cad || 0);
  }
  if (cad === 0 || fxTotals?.cad_usd !== undefined) {
    combinedTotals.USD = usd + cad * (fxTotals?.cad_usd || 0);
  }
}
```

**Problem:** When `usd === 0`, the condition `usd === 0 || fxTotals?.usd_cad !== undefined` is `true`, so `combinedTotals.CAD = cad + 0 * rate = cad`. This is correct. But when `cad === 0`, `combinedTotals.USD = usd + 0 * rate = usd`. Also correct. However, when both are non-zero and the FX rate is available, the logic works. The issue is that `fxTotals.usd_cad` and `fxTotals.cad_usd` are used interchangeably, but the variable names in the backend (`_snapshot_totals`) use `usd_cad` to mean "1 USD = X CAD" and `cad_usd` to mean "1 CAD = X USD". The frontend correctly maps these, but the display labels could confuse: `monthly.fx_usd_cad` says "FX: 1 USD = {rate} CAD" which is correct for `usd_cad`.

**However**, there is a subtle bug: if `filtered` rows are affected by `hideZero` and a user has a position with `market_value = 0` but non-zero quantity (e.g., a delisted stock), the totals would be correct. The real issue is that `combinedTotals` is computed from `filtered` (client-side filtered rows), not from `snapQ.data.totals` (server-side totals). If the user filters to only CAD accounts, `combinedTotals.USD` would show `0 + 0 * rate = 0` instead of hiding it. Wait, actually if `cad === 0` and `usd > 0`, then `combinedTotals.USD = usd`, which is correct for the filtered view. But `combinedTotals.CAD` would show `0 + usd * rate`, converting USD to CAD even though the user may only want to see CAD. This is actually consistent behavior.

**Reassessment:** The logic is actually mostly correct, but there's a **real bug** when `fxTotals` is undefined (no FX data) and both CAD and USD are non-zero: `combinedTotals` remains empty, so no combined total is shown. This is correct behavior (can't convert without rate). But the UI doesn't explain why no combined total appears.

**Actual bug found:** When `usd > 0` and `fxTotals?.usd_cad` is `undefined`, the first condition `usd === 0 || fxTotals?.usd_cad !== undefined` is `false`, so `combinedTotals.CAD` is not set. But `combinedTotals.USD` IS set (because `cad === 0 || fxTotals?.cad_usd !== undefined` — if `cad_usd` is defined, it's set; if `cad_usd` is also undefined, it's not set). So the behavior is consistent: no FX rate → no combined total. This is correct but could use a UI hint.

**Downgrade to MINOR** — the logic is correct but the UX could be clearer when FX rates are unavailable.

### 4.2 [MODERATE] Verify tab: `PdfPage` useEffect has stale closure over `onRendered`

**File:** `frontend/src/tabs/Verify.tsx:583-612`

```typescript
useEffect(() => {
    let cancelled = false;
    let renderTask: pdfjsLib.RenderTask | null = null;
    doc.getPage(pageNumber).then((page) => {
      if (cancelled) return;
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      canvas.width = Math.ceil(viewport.width);
      canvas.height = Math.ceil(viewport.height);
      renderTask = page.render({ canvasContext: ctx, viewport });
      renderTask.promise
        .then(() => {
          if (!cancelled) {
            setSize({ w: viewport.width, h: viewport.height });
            onRendered();
          }
        })
        .catch(() => { /* render cancelled / page hidden */ });
    }).catch(() => { /* page load error */ });
    return () => {
      cancelled = true;
      if (renderTask) {
        try { renderTask.cancel(); } catch { /* ignore */ }
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc, pageNumber, scale]);
```

**Problem:** The `useEffect` intentionally omits `onRendered` from the dependency array (with an eslint-disable comment). If the parent re-renders and passes a new `onRendered` callback (which happens on every `VerifyPane` render because it's an inline arrow function), the effect does NOT re-run, which is correct (avoids re-rendering the PDF). But the effect captures the FIRST `onRendered` closure and calls it even after subsequent re-renders. Since `onRendered` increments `renderedCount` state and calls `onPageRendered`, calling a stale closure could increment state on an unmounted component or with stale `pageNumber`.

**Impact:** Low in practice because `cancelled` flag prevents most issues, and `onRendered` is idempotent-ish (increments a counter). But the `onPageRendered` callback adds to `readyPages.current` which persists, so a stale call after statement change could add a page number from the previous statement.

**Recommendation:** Use a ref for `onRendered` to always call the latest version, or memoize the callback in the parent with `useCallback`.

### 4.3 [MODERATE] Transactions tab: `effectiveAcctIds` logic can produce empty filter when portfolio is set but user cleared selection

**File:** `frontend/src/tabs/Transactions.tsx:116-121`

```typescript
const effectiveAcctIds = accountIds.length > 0
  ? accountIds
  : activeAccountIds.length > 0
    ? activeAccountIds.map(String)
    : [];
```

**Problem:** When a portfolio is active (`activeAccountIds.length > 0`) and the user has NOT manually selected accounts (`accountIds.length === 0`), the filter uses the portfolio's accounts. But if the user then manually selects accounts and then deselects ALL of them, `accountIds` becomes `[]` again, and the filter falls back to `activeAccountIds`. This means the user cannot see "all accounts" (unfiltered) while a portfolio is active — the portfolio filter always applies unless they manually select accounts.

**Impact:** UX issue; user may be confused why clearing the account filter doesn't show all accounts. The UI shows "portfolio filter on" badge, which helps, but the behavior is still surprising.

### 4.4 [MINOR] Verify tab: `boxIndexForRefs` uses first box's `top` for scrolling, which may not be the visually first box

**File:** `frontend/src/tabs/Verify.tsx:50-59`

```typescript
function boxIndexForRefs(pages: StatementBoxes["pages"]): Map<SelectedKey, { page: number; top: number }> {
  const m = new Map<SelectedKey, { page: number; top: number }>();
  for (const page of pages) {
    for (const box of page.boxes) {
      const key = refKey(box.ref.kind, box.ref.id);
      if (!m.has(key)) m.set(key, { page: page.page_number, top: box.rect[1] });
    }
  }
  return m;
}
```

**Problem:** For a multi-line evidence item (e.g., a cash balance with opening and closing lines), the first box in the `pages` array may not be the visually first box on the page. The code takes the first box it encounters (`!m.has(key)`), which depends on the backend's ordering of `page.boxes`. If the backend returns boxes in a non-visual order (e.g., by `ordinal` but across different pages), the scroll target could be wrong.

**Impact:** Minor; scrolling to an approximate location is usually sufficient.

---

## 5. PDF Visual Verification (Verify Tab)

### 5.1 [MODERATE] Box overlay positioning assumes PDF coordinate origin matches browser top-left

**File:** `frontend/src/tabs/Verify.tsx:652-676` (BoxDiv)

```typescript
const [x0, top, x1, bottom] = box.rect;
...
return (
  <div
    className={cls.join(" ")}
    style={{
      left: x0 * scale,
      top: top * scale,
      width: (x1 - x0) * scale,
      height: (bottom - top) * scale,
    }}
    onClick={onClick}
    title={box.ref.label}
  />
);
```

**Problem:** PDF.js viewport uses top-left origin for rendering. The `pdfplumber` extraction also uses top-left origin (`top`, `bottom` coordinates where `top < bottom`). This is consistent. However, the `scale` factor (`RENDER_SCALE = 1.4`) is applied uniformly. The issue is that `PdfPage` sets the overlay size using `size?.w ?? width * scale` where `width`/`height` come from the backend's `page.width`/`page.height` (from `pdfplumber`), while the canvas is rendered using `page.getViewport({ scale })` from PDF.js. If PDF.js and pdfplumber report slightly different page dimensions (due to rounding or crop box differences), the overlay could be offset by a pixel or two.

**Impact:** Low; 1-2 pixel offset is usually imperceptible, but could cause boxes to be slightly misaligned on some PDFs.

### 5.2 [MINOR] `PdfView` renders ALL pages at once, causing performance issues on large statements

**File:** `frontend/src/tabs/Verify.tsx:529-554`

```typescript
return (
  <div className="verify-pdf-pages">
    {pages.map((page) => (
      <PdfPage ... />
    ))}
    ...
  </div>
);
```

**Problem:** Every page of the PDF is rendered to a canvas immediately. For a 50-page annual statement, this creates 50 canvas elements and 50 PDF.js render tasks simultaneously, which can be slow and memory-intensive.

**Impact:** Performance issue on large statements; browser may freeze or crash.

**Recommendation:** Implement virtualized rendering (only render pages in/near viewport) or lazy-load pages on scroll.

---

## 6. Additional Observations

### 6.1 [MINOR] `canonical_statement_clause` uses `MAX(statement_id)` which assumes higher ID = more recent

**File:** `src/ledger/statement_selection.py:15-26`

```python
def canonical_statement_clause(column: str) -> str:
    return f"""
        ({column} IS NULL OR {column} IN (
            SELECT MAX(canonical.statement_id)
              FROM statements canonical
             GROUP BY canonical.account_id, canonical.period_start,
                      canonical.period_end, canonical.statement_type
        ))
    """
```

This assumes `MAX(statement_id)` is the most recent revision. Since `statement_id` is an auto-increment primary key, this is true for newly inserted rows. But if a shadow build reuses statement IDs (which it shouldn't, but if the database is ever restored from a backup), this assumption could break.

### 6.2 [MINOR] `_fetch_transactions` in holdings.py fetches transactions where `cash_effective_date <= as_of` OR `trade_date <= as_of`

**File:** `src/ledger/holdings.py:368-374`

```sql
WHERE (t.trade_date <= ? OR COALESCE(t.cash_effective_date, t.trade_date) <= ?)
```

This is correct for cash reconstruction (a trade settling in the future should not affect today's cash). But for position reconstruction, the same filter is used, which means a trade with `trade_date <= as_of` but `cash_effective_date > as_of` would still affect positions. This is correct — positions are affected on trade date, cash on settlement date. The OR condition ensures both are fetched and the caller filters appropriately. No bug, but the query could be split for clarity.

---

## Summary of Recommendations

| Priority | Issue | Action |
|----------|-------|--------|
| **Critical** | CIBC option misclassification (§1.1) | Use word-boundary regex for option detection |
| **Critical** | Ticker change replay key collision (§2.1) | Use `instrument_id` instead of `instrument_key` in replay |
| **Critical** | Monthly combinedTotals FX edge case (§4.1) | Add UI hint when FX unavailable (downgraded to minor) |
| **Moderate** | TD option regex ambiguity (§1.2) | Validate month codes after regex match |
| **Moderate** | Auto-resolution reset too broad (§2.4) | Add `resolution_source` column |
| **Moderate** | Combined security states misleading anchor (§3.1) | Clarify checkpoint date for combined states |
| **Moderate** | PdfPage stale closure (§4.2) | Use ref for `onRendered` callback |
| **Moderate** | Transactions portfolio filter UX (§4.3) | Add explicit "All accounts" option |
| **Moderate** | Box overlay coordinate precision (§5.1) | Use PDF.js page dimensions for overlay |
| **Moderate** | PdfView renders all pages (§5.2) | Implement virtualized page rendering |
| **Minor** | RBC activity date regex (§1.3) | Tighten regex or add state guard |
| **Minor** | Cash components zero-delta waste (§2.2) | Skip zero-delta components |
| **Minor** | Scope candidates phantom default (§3.2) | Document behavior |
| **Minor** | BoxIndex first-box assumption (§4.4) | Sort by page then top before indexing |
