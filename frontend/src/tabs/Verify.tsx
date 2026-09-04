import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as pdfjsLib from "pdfjs-dist";
import { useSearchParams } from "react-router-dom";
// Bundle the worker through Vite so no external fetch is needed.
import PdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  api,
  EvidenceBox,
  StatementBoxes,
  StatementPosition,
  StatementQualityFlag,
  StatementReconciliation,
  StatementRow,
  StatementScope,
} from "../api";
import { useI18n } from "../i18n";
import { usePortfolio } from "../portfolio";
import { groupMonthsByYear, monthLabel } from "../verifyFilters";

pdfjsLib.GlobalWorkerOptions.workerSrc = PdfWorker;

// Render scale: PDF points → device pixels. 1.4 keeps bank statements legible
// without making multi-page statements enormous to scroll.
const RENDER_SCALE = 1.4;

const QUALITY_FLAGS: StatementQualityFlag[] = ["unresolved", "incomplete", "unreconciled"];

type SelectedKey = string;
type SelectionOrigin = "deep_link" | "right_list" | "pdf_box" | "statement_change";
type VerifySelection = {
  key: SelectedKey | null;
  origin: SelectionOrigin;
  requestToken: number;
};

function refKey(kind: string, id: number): SelectedKey {
  return `${kind}:${id}`;
}

function requestedSelection(searchParams: URLSearchParams): {
  statementId: number | null;
  key: SelectedKey | null;
} {
  const statementId = Number(searchParams.get("statement"));
  const key = searchParams.get("ref");
  const validKey = key && /^(transaction|position|cash|cash_close|summary|scope_issue|quarantine):\d+$/.test(key);
  return {
    statementId: Number.isInteger(statementId) && statementId > 0 ? statementId : null,
    key: validKey ? key : null,
  };
}

/** Map of selectedKey → the visually first box (lowest page, then top). */
function boxIndexForRefs(pages: StatementBoxes["pages"]): Map<SelectedKey, { page: number; top: number }> {
  const candidates = new Map<SelectedKey, { page: number; top: number }[]>();
  for (const page of pages) {
    for (const box of page.boxes) {
      const key = refKey(box.ref.kind, box.ref.id);
      const entry = { page: page.page_number, top: box.rect[1] };
      const list = candidates.get(key) || [];
      list.push(entry);
      candidates.set(key, list);
    }
  }
  const m = new Map<SelectedKey, { page: number; top: number }>();
  for (const [key, entries] of candidates) {
    entries.sort((a, b) => a.page - b.page || a.top - b.top);
    m.set(key, entries[0]);
  }
  return m;
}

export default function Verify() {
  const { t, lang } = useI18n();
  const { activeAccountIds } = usePortfolio();
  const [searchParams] = useSearchParams();
  const initialRequest = useMemo(() => requestedSelection(searchParams), []);
  const [selectedId, setSelectedId] = useState<number | null>(initialRequest.statementId);
  const [selection, setSelection] = useState<VerifySelection>({
    key: initialRequest.key,
    origin: initialRequest.key ? "deep_link" : "statement_change",
    requestToken: initialRequest.key ? 1 : 0,
  });
  const [dateFilter, setDateFilter] = useState("");
  const [bankFilter, setBankFilter] = useState("");
  const [acctFilter, setAcctFilter] = useState("");
  const [qualityFilters, setQualityFilters] = useState<StatementQualityFlag[]>([]);
  const [indexOpen, setIndexOpen] = useState(false);
  const indexRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const requested = requestedSelection(searchParams);
    if (requested.statementId !== null && requested.key) {
      setDateFilter("");
      setBankFilter("");
      setAcctFilter("");
      setQualityFilters([]);
      setSelectedId(requested.statementId);
      setSelection((currentSelection) => ({
        key: requested.key,
        origin: "deep_link",
        requestToken: currentSelection.requestToken + 1,
      }));
    }
  }, [searchParams]);

  // Statement picker list + client-side filters.
  const listQ = useQuery({ queryKey: ["statements-all"], queryFn: () => api.statements(2000) });
  // An active portfolio narrows the whole page — statement list and the
  // institution/account filter options — to its accounts, like Transactions.
  const allRows: StatementRow[] = useMemo(() => {
    const rows = listQ.data?.rows ?? [];
    if (activeAccountIds.length === 0) return rows;
    const allowed = new Set(activeAccountIds);
    return rows.filter((row) => allowed.has(row.account_id));
  }, [listQ.data, activeAccountIds]);

  const bankOpts = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of allRows) m.set(r.institution_code, r.institution_name || r.institution_code);
    return Array.from(m.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [allRows]);

  // Account options follow the chosen institution so empty combinations are
  // impossible; each option carries its statement count.
  const accountOptions = useMemo(() => {
    const rows = bankFilter ? allRows.filter((r) => r.institution_code === bankFilter) : allRows;
    const m = new Map<string, { key: string; label: string; count: number }>();
    for (const r of rows) {
      const key = `${r.institution_code}::${r.account_number}`;
      const entry = m.get(key) ?? {
        key,
        label: `${r.institution_code} ${r.account_number}`,
        count: 0,
      };
      entry.count += 1;
      m.set(key, entry);
    }
    return Array.from(m.values()).sort((a, b) => a.key.localeCompare(b.key));
  }, [allRows, bankFilter]);

  // Statements matching institution/account only: drives the period picker
  // and the quality-chip counts, independent of date and quality filters.
  const scopeRows = useMemo(() => {
    let rows = allRows;
    if (bankFilter) rows = rows.filter((r) => r.institution_code === bankFilter);
    if (acctFilter) rows = rows.filter((r) => `${r.institution_code}::${r.account_number}` === acctFilter);
    return rows;
  }, [allRows, bankFilter, acctFilter]);

  const monthGroups = useMemo(
    () => groupMonthsByYear(scopeRows.map((r) => r.period_end), lang),
    [scopeRows, lang],
  );

  const chipCounts = useMemo(() => {
    const counts = { all: scopeRows.length } as Record<string, number>;
    for (const flag of QUALITY_FLAGS) {
      counts[flag] = scopeRows.filter((r) => r.quality_flags.includes(flag)).length;
    }
    return counts;
  }, [scopeRows]);

  const filtered = useMemo(() => {
    let rows = scopeRows;
    if (dateFilter) rows = rows.filter((r) => r.period_end === dateFilter);
    if (qualityFilters.length) {
      rows = rows.filter((r) => r.quality_flags.some((flag) => qualityFilters.includes(flag)));
    }
    // allRows is already ordered by period_end DESC; keep that order.
    return rows;
  }, [scopeRows, dateFilter, qualityFilters]);

  // Index drawer rows grouped by calendar year (filtered is newest first).
  const indexGroups = useMemo(() => {
    const groups = new Map<string, StatementRow[]>();
    for (const r of filtered) {
      const year = r.period_end.slice(0, 4);
      const list = groups.get(year) ?? [];
      list.push(r);
      groups.set(year, list);
    }
    return [...groups.entries()]
      .sort((a, b) => (a[0] < b[0] ? 1 : -1))
      .map(([year, rows]) => ({ year, rows }));
  }, [filtered]);

  // Default to the latest statement once the list arrives.
  useEffect(() => {
    if (selectedId === null && filtered.length > 0) {
      setSelectedId(filtered[0].statement_id);
      setSelection((currentSelection) => ({
        key: null,
        origin: "statement_change",
        requestToken: currentSelection.requestToken + 1,
      }));
    }
  }, [filtered, selectedId]);

  // Keep the selected id valid as filters change: if it falls out of the
  // filtered set, jump back to the latest visible one.
  useEffect(() => {
    if (selectedId !== null && filtered.length > 0 && !filtered.some((r) => r.statement_id === selectedId)) {
      setSelectedId(filtered[0].statement_id);
      setSelection((currentSelection) => ({
        key: null,
        origin: "statement_change",
        requestToken: currentSelection.requestToken + 1,
      }));
    }
  }, [filtered, selectedId]);

  const currentIndex = filtered.findIndex((r) => r.statement_id === selectedId);
  const current = currentIndex >= 0 ? filtered[currentIndex] : null;

  function resetSelection() {
    setSelection((currentSelection) => ({
      key: null,
      origin: "statement_change",
      requestToken: currentSelection.requestToken + 1,
    }));
  }

  // List is period_end DESC: index 0 is newest. delta -1 = newer, +1 = older.
  function step(delta: number) {
    const next = currentIndex + delta;
    if (currentIndex < 0 || next < 0 || next >= filtered.length) return;
    setSelectedId(filtered[next].statement_id);
    resetSelection();
  }
  const goNext = () => step(-1); // newer
  const goPrev = () => step(1); // older

  // Change handlers drop dependent filters when the chosen value makes them
  // meaningless (e.g. a month the selected account does not have).
  function scopeCoversDate(institution: string, account: string, periodEnd: string): boolean {
    return allRows.some((r) =>
      (!institution || r.institution_code === institution)
      && (!account || `${r.institution_code}::${r.account_number}` === account)
      && r.period_end === periodEnd);
  }

  function changeInstitution(code: string) {
    setBankFilter(code);
    const nextAccount = code && acctFilter && !acctFilter.startsWith(`${code}::`)
      ? ""
      : acctFilter;
    if (nextAccount !== acctFilter) setAcctFilter("");
    if (dateFilter && !scopeCoversDate(code, nextAccount, dateFilter)) setDateFilter("");
    resetSelection();
  }

  function changeAccount(key: string) {
    setAcctFilter(key);
    if (dateFilter && !scopeCoversDate(bankFilter, key, dateFilter)) setDateFilter("");
    resetSelection();
  }

  function changePeriod(periodEnd: string) {
    setDateFilter(periodEnd);
    resetSelection();
  }

  function clearFilters() {
    setDateFilter("");
    setBankFilter("");
    setAcctFilter("");
    setQualityFilters([]);
  }

  function toggleQualityFilter(flag: StatementQualityFlag) {
    setQualityFilters((currentFilters) => currentFilters.includes(flag)
      ? currentFilters.filter((currentFlag) => currentFlag !== flag)
      : [...currentFilters, flag]);
  }

  // Arrow keys step through statements (↑ newer, ↓ older) unless the user is
  // typing in a form control; Escape closes the index drawer.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "SELECT"
        || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
      if (e.key === "Escape") {
        setIndexOpen(false);
        return;
      }
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
      e.preventDefault();
      step(e.key === "ArrowUp" ? -1 : 1);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  // Clicking outside the index drawer closes it; while it is open the
  // current statement's row stays scrolled into view.
  useEffect(() => {
    if (!indexOpen) return;
    function onPointerDown(e: MouseEvent) {
      if (indexRef.current && !indexRef.current.contains(e.target as Node)) setIndexOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [indexOpen]);

  useEffect(() => {
    if (!indexOpen) return;
    indexRef.current?.querySelector(".verify-index-row.selected")
      ?.scrollIntoView({ block: "nearest" });
  }, [indexOpen, selectedId]);

  const bankLabel = bankOpts.find(([code]) => code === bankFilter)?.[1] || bankFilter;
  const acctLabel = accountOptions.find((a) => a.key === acctFilter)?.label || acctFilter;
  const hasActiveFilters = Boolean(dateFilter || bankFilter || acctFilter || qualityFilters.length);

  return (
    <>
      <div className="filters verify-toolbar">
        <div className="verify-toolbar-row">
          <label>{t("f.institution")}:&nbsp;
            <select value={bankFilter} onChange={(e) => changeInstitution(e.target.value)}>
              <option value="">{t("verify.all")}</option>
              {bankOpts.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
            </select>
          </label>
          <label>{t("f.account")}:&nbsp;
            <select value={acctFilter} onChange={(e) => changeAccount(e.target.value)}>
              <option value="">{t("verify.all")}</option>
              {accountOptions.map((a) => (
                <option key={a.key} value={a.key}>{a.label} · {a.count}</option>
              ))}
            </select>
          </label>
          <select
            className="verify-month-select"
            value={dateFilter}
            title={t("verify.period")}
            aria-label={t("verify.period")}
            onChange={(e) => changePeriod(e.target.value)}
          >
            <option value="">{t("verify.all_months")}</option>
            {monthGroups.map((group) => (
              <optgroup key={group.year} label={group.year}>
                {group.months.map((m) => (
                  <option key={m.periodEnd} value={m.periodEnd}>{m.label}</option>
                ))}
              </optgroup>
            ))}
          </select>
          <span className="verify-stepper">
            {/* "next" (newer statement) is on the left, since newer is higher in the list */}
            <button className="icon-btn" onClick={goNext} disabled={currentIndex <= 0}
                    title={t("verify.next")} aria-label={t("verify.next")}>
              <ChevronLeftIcon />
            </button>
            <select
              className="verify-statement-select"
              value={selectedId ?? ""}
              title={t("verify.statement")}
              aria-label={t("verify.statement")}
              onChange={(e) => {
                setSelectedId(Number(e.target.value));
                resetSelection();
              }}
            >
              {indexGroups.map((group) => (
                <optgroup key={group.year} label={group.year}>
                  {group.rows.map((r) => (
                    <option key={r.statement_id} value={r.statement_id}>
                      {r.institution_code} {r.account_number} · {monthLabel(r.period_end, lang)}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
            <button className="icon-btn" onClick={goPrev}
                    disabled={currentIndex < 0 || currentIndex >= filtered.length - 1}
                    title={t("verify.prev")} aria-label={t("verify.prev")}>
              <ChevronRightIcon />
            </button>
          </span>
          <span className="verify-position">
            {filtered.length ? `${currentIndex + 1} / ${filtered.length}` : "—"}
          </span>
        </div>
        <div className="verify-toolbar-row">
          <span className="verify-chip-row">
            <span className="verify-chip-label">{t("verify.show")}:</span>
            <button
              className={`verify-chip ${qualityFilters.length === 0 ? "active" : ""}`}
              onClick={() => setQualityFilters([])}
            >
              {t("verify.all")} {chipCounts.all}
            </button>
            {QUALITY_FLAGS.map((flag) => (
              <button
                key={flag}
                className={`verify-chip ${qualityFilters.includes(flag) ? "active" : ""}`}
                onClick={() => toggleQualityFilter(flag)}
              >
                {qualityFlagLabel(t, flag)} {chipCounts[flag]}
              </button>
            ))}
          </span>
          {bankFilter && (
            <button className="verify-filter-chip" onClick={() => changeInstitution("")}>
              {bankLabel} ✕
            </button>
          )}
          {acctFilter && (
            <button className="verify-filter-chip" onClick={() => changeAccount("")}>
              {acctLabel} ✕
            </button>
          )}
          {dateFilter && (
            <button className="verify-filter-chip" onClick={() => changePeriod("")}>
              {monthLabel(dateFilter, lang)} ✕
            </button>
          )}
          {hasActiveFilters && <button onClick={clearFilters}>{t("f.clear")}</button>}
          <span className="verify-flex-spacer" />
          {current?.quality_flags.map((flag) => (
            <span key={flag} className={`quality-tag ${flag}`}>{qualityFlagLabel(t, flag)}</span>
          ))}
          <span className="muted">{filtered.length} {t("verify.statements")}</span>
          <div className="verify-index" ref={indexRef}>
            <button
              className={indexOpen ? "active" : ""}
              aria-expanded={indexOpen}
              onClick={() => setIndexOpen((open) => !open)}
            >
              {t("verify.index")} ({filtered.length}) {indexOpen ? "▾" : "▸"}
            </button>
            {indexOpen && (
              <div className="verify-index-drawer">
                {indexGroups.map((group) => (
                  <div key={group.year}>
                    <div className="verify-index-year">{group.year}</div>
                    {group.rows.map((r) => (
                      <div
                        key={r.statement_id}
                        className={`verify-index-row ${r.statement_id === selectedId ? "selected" : ""}`}
                        title={`${r.institution_code} ${r.account_number}`}
                        onClick={() => {
                          setSelectedId(r.statement_id);
                          resetSelection();
                          setIndexOpen(false);
                        }}
                      >
                        <span className="verify-index-month">{monthLabel(r.period_end, lang)}</span>
                        <span className="verify-index-account">{r.institution_code} {r.account_number}</span>
                        <span className="verify-index-flags">
                          {r.quality_flags.map((flag) => (
                            <span key={flag} className={`flag-dot ${flag}`} title={qualityFlagLabel(t, flag)} />
                          ))}
                        </span>
                      </div>
                    ))}
                  </div>
                ))}
                {filtered.length === 0 && (
                  <p className="muted verify-index-empty">{t("verify.no_statements")}</p>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
      {listQ.isLoading && <p className="muted">{t("viz.loading")}</p>}
      {listQ.error && <p className="inline-status status-error">{String(listQ.error)}</p>}
      {!listQ.isLoading && filtered.length === 0 && (
        <p className="muted">{t("verify.no_statements")}</p>
      )}
      {current && (
        <VerifyPane
          statementId={current.statement_id}
          selection={selection}
          onSelect={(key, origin) => setSelection((currentSelection) => ({
            key,
            origin,
            requestToken: currentSelection.requestToken + 1,
          }))}
        />
      )}
    </>
  );
}

function ChevronLeftIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="15 18 9 12 15 6" />
    </svg>
  );
}
function ChevronRightIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

function VerifyPane({
  statementId,
  selection,
  onSelect,
}: {
  statementId: number;
  selection: VerifySelection;
  onSelect: (k: SelectedKey | null, origin: SelectionOrigin) => void;
}) {
  const { t } = useI18n();
  const boxesQ = useQuery({
    queryKey: ["statement-boxes", statementId],
    queryFn: () => api.statementBoxes(statementId),
  });

  const pdfUrl = api.statementPdfUrl(statementId);

  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const itemRefs = useRef<Record<SelectedKey, HTMLDivElement | null>>({});
  const pdfScrollRef = useRef<HTMLDivElement | null>(null);
  const itemsScrollRef = useRef<HTMLDivElement | null>(null);
  const readyPages = useRef<Set<number>>(new Set());
  const [renderRevision, setRenderRevision] = useState(0);

  const data: StatementBoxes | undefined = boxesQ.data;
  useEffect(() => {
    readyPages.current.clear();
    pageRefs.current = {};
    itemRefs.current = {};
  }, [statementId]);
  const firstBoxByRef = useMemo(
    () => (data ? boxIndexForRefs(data.pages) : new Map<SelectedKey, { page: number; top: number }>()),
    [data],
  );

  const selectedKey = selection.key;

  useEffect(() => {
    if (!selectedKey || !data) return;
    if (selection.origin === "pdf_box") {
      const item = itemRefs.current[selectedKey];
      const pane = itemsScrollRef.current;
      if (item && pane) {
        const target = pane.scrollTop
          + item.getBoundingClientRect().top
          - pane.getBoundingClientRect().top
          - 24;
        pane.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
      }
      return;
    }
    const loc = firstBoxByRef.get(selectedKey);
    if (!loc || !readyPages.current.has(loc.page)) return;
    const el = pageRefs.current[loc.page];
    const pane = pdfScrollRef.current;
    if (el && pane) {
      const target = pane.scrollTop
        + el.getBoundingClientRect().top
        - pane.getBoundingClientRect().top
        + loc.top * RENDER_SCALE
        - 24;
      pane.scrollTo({
        top: Math.max(0, target),
        behavior: "smooth",
      });
    }
  }, [selection, selectedKey, firstBoxByRef, data, renderRevision, statementId]);

  if (boxesQ.isLoading) return <p className="muted">{t("viz.loading")}</p>;
  if (boxesQ.error) {
    return <p className="inline-status status-error">{t("verify.load_failed")}: {String(boxesQ.error)}</p>;
  }
  if (!data) return null;

  const boxesByPage = new Map<number, EvidenceBox[]>();
  for (const page of data.pages) {
    if (page.boxes.length) boxesByPage.set(page.page_number, page.boxes);
  }

  const requestedRefMissing = selectedKey !== null && ![
    ...data.transactions.map((row) => refKey("transaction", row.transaction_id)),
    ...data.positions.map((row) => refKey("position", row.snapshot_id)),
    ...data.cash_balances.map((row) => refKey("cash", row.cash_balance_id)),
    ...data.cash_balances.map((row) => refKey("cash_close", row.cash_balance_id)),
    ...data.summary_totals.map((row) => refKey("summary", row.snapshot_set_id)),
    ...data.scope_issues.map((row) => refKey("scope_issue", row.scope_issue_id)),
    ...data.quarantine.map((row) => refKey("quarantine", row.quarantine_id)),
  ].includes(selectedKey);

  const currencies = [...new Set([
    ...data.transactions.map((row) => row.currency).filter(Boolean),
    ...data.positions.map((row) => row.currency).filter(Boolean),
    ...data.cash_balances.map((row) => row.currency).filter(Boolean),
    ...data.summary_totals.map((row) => row.currency).filter(Boolean),
  ] as string[])].sort();

  return (
    <div className="verify-layout">
      <div className="verify-pane verify-pdf-pane" ref={pdfScrollRef}>
        {data.pages.length ? (
          <PdfView
            url={pdfUrl}
            pages={data.pages}
            scale={RENDER_SCALE}
            boxesByPage={boxesByPage}
            selectedKey={selectedKey}
            onSelect={(key) => onSelect(key, "pdf_box")}
            pageRefs={pageRefs}
            onPageRendered={(pageNumber) => {
              readyPages.current.add(pageNumber);
              setRenderRevision((revision) => revision + 1);
            }}
          />
        ) : <p className="muted">{t("verify.no_pdf")}</p>}
      </div>

      <div className="verify-pane verify-items-pane" ref={itemsScrollRef}>
        <StatusStrip data={data} />
        {requestedRefMissing && (
          <p className="inline-status status-error">{t("verify.requested_ref_missing")}</p>
        )}
        {currencies.map((currency) => (
          <div className="verify-currency-group" key={currency}>
            <h3>{currency}</h3>
            <ItemsGroup
              title={t("verify.transactions")}
              rows={data.transactions.filter((row) => row.currency === currency)}
              render={(r) => [r.trade_date, r.txn_type, r.symbol || "", contractLabel(r), fmt(r.net_amount), r.currency || ""]
                .filter(Boolean).join(" ")}
              kind="transaction" idOf={(r) => r.transaction_id}
              titleOf={(r) => r.description || ""} selectedKey={selectedKey}
              onSelect={(key) => onSelect(key, "right_list")}
              matchedKeys={firstBoxByRef} itemRefs={itemRefs}
            />
            <ItemsGroup
              title={t("verify.positions")}
              rows={data.positions.filter((row) => row.currency === currency)}
              render={renderPosition}
              kind="position" idOf={(r) => r.snapshot_id}
              titleOf={(r) => r.raw_line || ""} selectedKey={selectedKey}
              onSelect={(key) => onSelect(key, "right_list")}
              matchedKeys={firstBoxByRef} itemRefs={itemRefs}
            />
            <ItemsGroup
              title={t("verify.cash")}
              rows={data.cash_balances
                .filter((row) => row.currency === currency)
                .flatMap((row) => [{ part: "opening" as const, row }, { part: "closing" as const, row }])}
              render={(r) => r.part === "opening"
                ? `${t("verify.opening")} ${fmt(r.row.opening_balance)} ${r.row.currency || ""}`
                : `${t("verify.closing")} ${fmt(r.row.closing_balance)} ${r.row.currency || ""}`}
              kind="cash" idOf={(r) => r.row.cash_balance_id}
              keyOf={(r) => {
                if (r.part === "opening") return refKey("cash", r.row.cash_balance_id);
                const closingKey = refKey("cash_close", r.row.cash_balance_id);
                // Legacy single-box evidence has no separate closing line;
                // its one box still contains the closing amount.
                return firstBoxByRef.has(closingKey) ? closingKey : refKey("cash", r.row.cash_balance_id);
              }}
              titleOf={(r) => r.row.raw_line || ""} selectedKey={selectedKey}
              onSelect={(key) => onSelect(key, "right_list")}
              matchedKeys={firstBoxByRef} itemRefs={itemRefs}
            />
            <ItemsGroup
              title={t("verify.summary_totals")}
              rows={data.summary_totals.filter((row) => row.currency === currency)}
              render={(r: StatementScope) => `${scopeKindLabel(t, r.section_type)} · ${t("verify.opening")} ${fmt(r.opening_total)} · ${t("verify.change")} ${fmt(r.reported_change)} · ${t("verify.closing")} ${fmt(r.reported_total)}`}
              kind="summary" idOf={(r) => r.snapshot_set_id}
              titleOf={(r) => r.raw_line || ""} selectedKey={selectedKey}
              onSelect={(key) => onSelect(key, "right_list")}
              matchedKeys={firstBoxByRef} itemRefs={itemRefs}
            />
          </div>
        ))}
        <QualityPanel data={data} />
        <ItemsGroup
          title={t("verify.scope_issues")}
          rows={data.scope_issues}
          render={(r) => `${scopeKindLabel(t, r.section_type)} · ${r.currency} · ${scopeIssueLabel(t, r.issue_code, r.detail)}`}
          kind="scope_issue"
          idOf={(r) => r.scope_issue_id}
          titleOf={(r) => r.quarantine_reason || r.raw_text || ""}
          selectedKey={selectedKey}
          onSelect={(key) => onSelect(key, "right_list")}
          matchedKeys={firstBoxByRef}
          itemRefs={itemRefs}
        />
        <ItemsGroup
          title={t("verify.quarantine")}
          rows={data.quarantine}
          render={(r) => r.reason || ""}
          kind="quarantine"
          idOf={(r) => r.quarantine_id}
          titleOf={(r) => r.raw_line || ""}
          selectedKey={selectedKey}
          onSelect={(key) => onSelect(key, "right_list")}
          matchedKeys={firstBoxByRef}
          itemRefs={itemRefs}
        />
      </div>
    </div>
  );
}

function fmt(n: number | null | undefined, dec = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "";
  return n.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

/** Option contract detail shared by transaction and position rows. */
function contractLabel(r: {
  option_type?: string | null;
  option_strike?: number | null;
  option_expiry?: string | null;
  option_multiplier?: number | null;
}): string {
  if (!r.option_type) return "";
  return [
    r.option_type,
    fmt(r.option_strike),
    r.option_expiry || "",
    r.option_multiplier && r.option_multiplier !== 100 ? `×${r.option_multiplier}` : "",
  ].filter(Boolean).join(" ");
}

/** Positions render as option contracts (type/strike/expiry/multiplier) or plain shares. */
function renderPosition(r: StatementPosition): string {
  const parts = [r.symbol || "", fmt(r.quantity, 0)];
  if (r.option_type) parts.push(contractLabel(r));
  parts.push(fmt(r.market_value), r.currency || "");
  return parts.filter(Boolean).join(" ");
}

/**
 * Renders every page of the PDF to a stacked sequence of canvases.
 *
 * Each page is a `position: relative` container that holds both the canvas
 * (the rendered PDF page) and an absolutely-positioned box overlay. Because
 * the overlay lives in the same container as its canvas and the boxes are
 * positioned with the same RENDER_SCALE, they line up exactly with the text.
 */
function PdfView({
  url,
  pages,
  scale,
  boxesByPage,
  selectedKey,
  onSelect,
  pageRefs,
  onPageRendered,
}: {
  url: string;
  pages: StatementBoxes["pages"];
  scale: number;
  boxesByPage: Map<number, EvidenceBox[]>;
  selectedKey: SelectedKey | null;
  onSelect: (k: SelectedKey | null) => void;
  pageRefs: React.MutableRefObject<Record<number, HTMLDivElement | null>>;
  onPageRendered: (pageNumber: number) => void;
}) {
  const { t } = useI18n();
  const [doc, setDoc] = useState<pdfjsLib.PDFDocumentProxy | null>(null);
  const [error, setError] = useState("");
  const [renderedCount, setRenderedCount] = useState(0);
  const [visiblePages, setVisiblePages] = useState<Set<number>>(() => new Set());
  const observerRef = useRef<IntersectionObserver | null>(null);
  const pageVisibilityRef = useRef<Map<number, HTMLDivElement>>(new Map());

  const markPageVisible = useCallback((pageNumber: number) => {
    setVisiblePages((current) => {
      if (current.has(pageNumber)) return current;
      const next = new Set(current);
      next.add(pageNumber);
      return next;
    });
  }, []);

  useEffect(() => {
    observerRef.current?.disconnect();
    observerRef.current = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const pageNumber = Number((entry.target as HTMLElement).dataset.page);
          if (Number.isFinite(pageNumber)) markPageVisible(pageNumber);
        }
      },
      { rootMargin: "240px 0px" },
    );
    for (const node of pageVisibilityRef.current.values()) {
      observerRef.current.observe(node);
    }
    return () => observerRef.current?.disconnect();
  }, [markPageVisible, pages.length]);

  useEffect(() => {
    setVisiblePages(new Set(pages.length ? [pages[0].page_number] : []));
    pageVisibilityRef.current.clear();
  }, [url, pages]);

  useEffect(() => {
    let cancelled = false;
    let task: pdfjsLib.PDFDocumentLoadingTask | null = null;
    setError("");
    setDoc(null);
    setRenderedCount(0);
    task = pdfjsLib.getDocument({ url });
    task.promise
      .then((d) => { if (!cancelled) setDoc(d); })
      .catch((e: unknown) => { if (!cancelled) setError(String(e)); });
    return () => {
      cancelled = true;
      if (task) {
        try { task.destroy(); } catch { /* ignore */ }
      }
    };
  }, [url]);

  if (error) return <p className="inline-status status-error">{t("verify.pdf_failed")}: {error}</p>;
  if (!doc) return <p className="muted">{t("viz.loading")}</p>;

  return (
    <div className="verify-pdf-pages">
      {pages.map((page) => {
        const pageNumber = page.page_number;
        const registerPage = (el: HTMLDivElement | null) => {
          pageRefs.current[pageNumber] = el;
          const prior = pageVisibilityRef.current.get(pageNumber);
          if (prior && observerRef.current) observerRef.current.unobserve(prior);
          if (el) {
            pageVisibilityRef.current.set(pageNumber, el);
            observerRef.current?.observe(el);
          } else {
            pageVisibilityRef.current.delete(pageNumber);
          }
        };
        if (!visiblePages.has(pageNumber)) {
          return (
            <div
              key={pageNumber}
              className="verify-pdf-page verify-pdf-page-placeholder"
              ref={registerPage}
              data-page={pageNumber}
              style={{ width: page.width * scale, height: page.height * scale }}
            />
          );
        }
        return (
          <PdfPage
            key={pageNumber}
            doc={doc}
            pageNumber={pageNumber}
            width={page.width}
            height={page.height}
            scale={scale}
            boxes={boxesByPage.get(pageNumber) || []}
            selectedKey={selectedKey}
            onSelect={onSelect}
            pageRef={registerPage}
            onRendered={() => {
              setRenderedCount((count) => count + 1);
              onPageRendered(pageNumber);
            }}
          />
        );
      })}
      {renderedCount < pages.length && (
        <span className="muted verify-rendering">{t("verify.rendering")} {renderedCount}/{pages.length}</span>
      )}
    </div>
  );
}

function PdfPage({
  doc,
  pageNumber,
  width,
  height,
  scale,
  boxes,
  selectedKey,
  onSelect,
  pageRef,
  onRendered,
}: {
  doc: pdfjsLib.PDFDocumentProxy;
  pageNumber: number;
  width: number;
  height: number;
  scale: number;
  boxes: EvidenceBox[];
  selectedKey: SelectedKey | null;
  onSelect: (k: SelectedKey | null) => void;
  pageRef: (el: HTMLDivElement | null) => void;
  onRendered: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // Known rendered size; the overlay must wait for this before sizing itself.
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const onRenderedRef = useRef(onRendered);
  onRenderedRef.current = onRendered;

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
            onRenderedRef.current();
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
  }, [doc, pageNumber, scale]);

  const renderedWidth = size?.w ?? width * scale;
  const renderedHeight = size?.h ?? height * scale;

  return (
    <div
      className="verify-pdf-page"
      ref={pageRef}
      data-page={pageNumber}
      style={{ width: renderedWidth, height: renderedHeight }}
    >
      <canvas ref={canvasRef} />
      <span className="verify-physical-page">{pageNumber}</span>
      <div
        className="verify-page-overlay"
        style={{ width: renderedWidth, height: renderedHeight }}
      >
          {boxes.map((line, i) => (
            <BoxDiv
              key={i}
              line={line}
              scale={scale}
              selectedKey={selectedKey}
              onSelect={onSelect}
            />
          ))}
      </div>
    </div>
  );
}

function BoxDiv({
  line: box,
  scale,
  selectedKey,
  onSelect,
}: {
  line: EvidenceBox;
  scale: number;
  selectedKey: SelectedKey | null;
  onSelect: (k: SelectedKey | null) => void;
}) {
  const [x0, top, x1, bottom] = box.rect;
  const key = refKey(box.ref.kind, box.ref.id);
  const selected = selectedKey === key;

  const cls = ["verify-box"];
  if (box.ref.kind === "quarantine") cls.push("quarantine");
  if (selected) cls.push("selected");

  function onClick(e: React.MouseEvent) {
    e.stopPropagation();
    onSelect(selectedKey === key ? null : key);
  }

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
}

function ItemsGroup({
  title,
  rows,
  render,
  kind,
  idOf,
  keyOf,
  titleOf,
  selectedKey,
  onSelect,
  matchedKeys,
  itemRefs,
}: {
  title: string;
  rows: any[];
  render: (r: any) => string;
  kind: string;
  idOf: (r: any) => number;
  /** Overrides the default `kind:id` selection key (used by split cash rows). */
  keyOf?: (r: any) => SelectedKey;
  titleOf: (r: any) => string;
  selectedKey: SelectedKey | null;
  onSelect: (k: SelectedKey | null) => void;
  matchedKeys: Map<SelectedKey, { page: number; top: number }>;
  itemRefs: React.MutableRefObject<Record<SelectedKey, HTMLDivElement | null>>;
}) {
  const { t } = useI18n();
  return (
    <div className="verify-group">
      <h4>{title} <span className="muted">({rows.length})</span></h4>
      {rows.map((r) => {
        const id = idOf(r);
        const key = keyOf ? keyOf(r) : refKey(kind, id);
        const isSelected = selectedKey === key;
        const hasBox = matchedKeys.has(key);
        const geometryStatus = r.geometry_status || "unavailable";
        const cls = ["verify-item"];
        if (kind === "quarantine") cls.push("quarantine");
        if (isSelected) cls.push("selected");
        if (!hasBox) cls.push("no-box");
        return (
          <div
            key={key}
            ref={(element) => { itemRefs.current[key] = element; }}
            className={cls.join(" ")}
            title={titleOf(r)}
            onClick={() => onSelect(isSelected ? null : key)}
          >
            <span className="verify-item-kind">{kindTag(kind)}</span>
            <span className="verify-item-text">{render(r)}</span>
            {!hasBox && (
              <span className="verify-item-nobox" title={t(`verify.geometry.${geometryStatus}`)}>
                {t(`verify.geometry.${geometryStatus}`)}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function kindTag(kind: string): string {
  switch (kind) {
    case "transaction": return "T";
    case "position": return "P";
    case "quarantine": return "Q";
    case "cash": return "C";
    case "summary": return "S";
    default: return kind.charAt(0).toUpperCase();
  }
}

function qualityFlagLabel(t: (key: string) => string, flag: StatementQualityFlag): string {
  return t(`verify.filter.${flag}`);
}

function reconciliationStatusLabel(t: (key: string) => string, status: string): string {
  const key = `quality.status.${status}`;
  const translated = t(key);
  return translated === key ? status.replaceAll("_", " ") : translated;
}

function completenessLabel(t: (key: string) => string, completeness: string): string {
  const key = `quality.completeness.${completeness}`;
  const translated = t(key);
  return translated === key ? completeness : translated;
}

function reconciliationKindLabel(t: (key: string) => string, kind: StatementReconciliation["kind"]): string {
  return t(`verify.reconciliation_kind.${kind}`);
}

function scopeKindLabel(t: (key: string) => string, kind: StatementScope["section_type"]): string {
  return t(`verify.scope_kind.${kind}`);
}

function scopeIssueLabel(
  t: (key: string) => string,
  code: string,
  detail: Record<string, unknown>,
): string {
  const key = `verify.scope_issue.${code}`;
  const translated = t(key);
  const label = translated === key ? code.replaceAll("_", " ") : translated;
  const count = typeof detail.count === "number" ? ` (${detail.count})` : "";
  return `${label}${count}`;
}

function StatusStrip({ data }: { data: StatementBoxes }) {
  const { t } = useI18n();
  const blocking = data.scope_issues.filter((issue) => issue.blocks_completeness);
  const failedReconciliation = data.reconciliation_results.filter((row) =>
    !["reconciled", "within_rounding", "not_applicable"].includes(row.status));
  const needsReview = blocking.length > 0 || data.quarantine.length > 0 || failedReconciliation.length > 0;
  return (
    <section className={`verify-status-strip ${needsReview ? "needs-review" : "clear"}`}>
      <strong>{needsReview ? t("verify.status_needs_review") : t("verify.status_clear")}</strong>
      <span>
        {blocking.length > 0
          ? `${blocking.length} ${t("verify.status_scope_issues")}`
          : data.quarantine.length > 0
            ? `${data.quarantine.length} ${t("verify.status_quarantine")}`
            : failedReconciliation.length > 0
              ? `${failedReconciliation.length} ${t("verify.status_reconciliation")}`
              : t("verify.status_no_blockers")}
      </span>
    </section>
  );
}

function ScopeRows({ scopes }: { scopes: StatementScope[] }) {
  const { t } = useI18n();
  if (!scopes.length) return <p className="muted verify-quality-empty">{t("verify.no_scopes")}</p>;
  return (
    <div className="verify-quality-list">
      {scopes.map((scope) => (
        <div key={scope.snapshot_set_id} className={`verify-quality-row scope-${scope.completeness}`}>
          <span>
            {scope.currency} · {scopeKindLabel(t, scope.section_type)}
            {scope.scope_key !== "default" ? ` · ${scope.scope_key}` : ""}
          </span>
          <span className={`quality-tag ${scope.completeness}`}>{completenessLabel(t, scope.completeness)}</span>
        </div>
      ))}
    </div>
  );
}

function ReconciliationRows({ rows }: { rows: StatementReconciliation[] }) {
  const { t } = useI18n();
  if (!rows.length) return <p className="muted verify-quality-empty">{t("verify.no_reconciliation")}</p>;
  return (
    <div className="verify-quality-list">
      {rows.map((row) => (
        <div key={row.reconciliation_id} className={`verify-quality-row reconciliation-${row.status}`}>
          <span title={row.reason || ""}>
            {row.symbol || row.instrument_name || reconciliationKindLabel(t, row.kind)} · {row.currency}
            {row.check_type ? ` · ${row.check_type.replaceAll("_", " ")}` : ""}
            {row.scope_key && row.scope_key !== "default" ? ` · ${row.scope_key}` : ""}
            {row.residual !== null ? ` · ${t("verify.residual")} ${fmt(row.residual)}` : ""}
            {row.reason ? ` — ${row.reason}` : ""}
          </span>
          <span className={`quality-tag reconciliation-${row.status}`}>{reconciliationStatusLabel(t, row.status)}</span>
        </div>
      ))}
    </div>
  );
}

function QualityPanel({ data }: { data: StatementBoxes }) {
  const { t } = useI18n();
  const source = data.source_file;
  const quality = data.statement.quality;
  const hasQualityFacts = quality.scope_count > 0 || quality.reconciliation_result_count > 0;
  return (
    <section className="verify-quality">
      <h3>{t("verify.quality")}</h3>
      <div className="verify-quality-summary">
        {quality.quality_flags.length ? quality.quality_flags.map((flag) => (
          <span key={flag} className={`quality-tag ${flag}`}>{qualityFlagLabel(t, flag)}</span>
        )) : hasQualityFacts ? <span className="quality-tag complete">{t("verify.quality_clear")}</span>
          : <span className="quality-tag unavailable">{t("quality.unavailable")}</span>}
        {hasQualityFacts ? (
          <span className="muted">
            {quality.complete_scope_count}/{quality.scope_count} {t("verify.complete_scopes")}
          </span>
        ) : <span className="muted">{t("verify.no_quality")}</span>}
      </div>
      <h4>{t("verify.scopes")}</h4>
      <ScopeRows scopes={data.scopes} />
      <h4>{t("verify.reconciliation")}</h4>
      <ReconciliationRows rows={data.reconciliation_results} />
      <details className="verify-diagnostics">
        <summary>{t("verify.diagnostics")}</summary>
        <div className="verify-quality-meta">
          <span>{t("verify.parser")}: {source?.parser_name || t("verify.not_available")}</span>
          <span>{t("verify.parser_version")}: {source?.parser_version || t("verify.not_available")}</span>
          <span>{t("verify.contract_version")}: {source?.contract_version || t("verify.not_available")}</span>
          <span>{t("verify.run_schema")}: {source?.run_schema_version ?? t("verify.not_available")}</span>
          <span>{t("verify.active_run")}: {source?.active_run_status || t("verify.not_available")}</span>
          <span>{t("verify.parse_status")}: {source?.parse_status || t("verify.not_available")}</span>
        </div>
      </details>
    </section>
  );
}
