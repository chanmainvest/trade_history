import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as pdfjsLib from "pdfjs-dist";
import { useSearchParams } from "react-router-dom";
// Bundle the worker through Vite so no external fetch is needed.
import PdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  api,
  EvidenceBox,
  StatementBoxes,
  StatementQualityFlag,
  StatementReconciliation,
  StatementRow,
  StatementScope,
} from "../api";
import { useI18n } from "../i18n";

pdfjsLib.GlobalWorkerOptions.workerSrc = PdfWorker;

// Render scale: PDF points → device pixels. 1.4 keeps bank statements legible
// without making multi-page statements enormous to scroll.
const RENDER_SCALE = 1.4;

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
  const validKey = key && /^(transaction|position|cash|summary|scope_issue|quarantine):\d+$/.test(key);
  return {
    statementId: Number.isInteger(statementId) && statementId > 0 ? statementId : null,
    key: validKey ? key : null,
  };
}

/** Map of selectedKey → the physical page + top that holds its first box. */
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

export default function Verify() {
  const { t } = useI18n();
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
  const allRows: StatementRow[] = listQ.data?.rows ?? [];

  const dateOpts = useMemo(() => {
    const s = new Set<string>();
    for (const r of allRows) s.add(r.period_end);
    return Array.from(s).sort((a, b) => (a < b ? 1 : -1));
  }, [allRows]);

  const bankOpts = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of allRows) m.set(r.institution_code, r.institution_name || r.institution_code);
    return Array.from(m.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [allRows]);

  const acctOpts = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of allRows) {
      m.set(`${r.institution_code}::${r.account_number}`, `${r.institution_code} • ${r.account_number}`);
    }
    return Array.from(m.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [allRows]);

  const filtered = useMemo(() => {
    let rows = allRows;
    if (dateFilter) rows = rows.filter((r) => r.period_end === dateFilter);
    if (bankFilter) rows = rows.filter((r) => r.institution_code === bankFilter);
    if (acctFilter) {
      rows = rows.filter((r) => `${r.institution_code}::${r.account_number}` === acctFilter);
    }
    if (qualityFilters.length) {
      rows = rows.filter((r) => r.quality_flags.some((flag) => qualityFilters.includes(flag)));
    }
    // allRows is already ordered by period_end DESC; keep that order.
    return rows;
  }, [allRows, dateFilter, bankFilter, acctFilter, qualityFilters]);

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
  // List is period_end DESC: index 0 is newest. "Next" = newer = lower index.
  const hasPrev = currentIndex >= 0 && currentIndex < filtered.length - 1; // older exists
  const hasNext = currentIndex > 0; // newer exists

  function goPrev() {
    if (hasPrev) {
      setSelectedId(filtered[currentIndex + 1].statement_id);
      setSelection((currentSelection) => ({ key: null, origin: "statement_change", requestToken: currentSelection.requestToken + 1 }));
    }
  }
  function goNext() {
    if (hasNext) {
      setSelectedId(filtered[currentIndex - 1].statement_id);
      setSelection((currentSelection) => ({ key: null, origin: "statement_change", requestToken: currentSelection.requestToken + 1 }));
    }
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

  return (
    <>
      <h2>{t("nav.verify")}</h2>
      <div className="filters">
        <label>{t("f.date")}:&nbsp;
          <select value={dateFilter} onChange={(e) => { setDateFilter(e.target.value); setSelection((currentSelection) => ({ key: null, origin: "statement_change", requestToken: currentSelection.requestToken + 1 })); }}>
            <option value="">{t("verify.all")}</option>
            {dateOpts.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <label>{t("f.institution")}:&nbsp;
          <select value={bankFilter} onChange={(e) => { setBankFilter(e.target.value); setSelection((currentSelection) => ({ key: null, origin: "statement_change", requestToken: currentSelection.requestToken + 1 })); }}>
            <option value="">{t("verify.all")}</option>
            {bankOpts.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </select>
        </label>
        <label>{t("f.account")}:&nbsp;
          <select value={acctFilter} onChange={(e) => { setAcctFilter(e.target.value); setSelection((currentSelection) => ({ key: null, origin: "statement_change", requestToken: currentSelection.requestToken + 1 })); }}>
            <option value="">{t("verify.all")}</option>
            {acctOpts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        {(["unresolved", "incomplete", "unreconciled"] as StatementQualityFlag[]).map((flag) => (
          <label key={flag} className="quality-filter">
            <input
              type="checkbox"
              checked={qualityFilters.includes(flag)}
              onChange={() => toggleQualityFilter(flag)}
            />&nbsp;{qualityFlagLabel(t, flag)}
          </label>
        ))}
        <span className="verify-pager">
          {/* "next" (newer statement) is on the left, since newer is higher in the list */}
          <button className="icon-btn" onClick={goNext} disabled={!hasNext} title={t("verify.next")} aria-label={t("verify.next")}>
            <ChevronLeftIcon />
          </button>
          <span className="verify-date">
            {current ? `${current.period_start} → ${current.period_end}` : "—"}
          </span>
          <button className="icon-btn" onClick={goPrev} disabled={!hasPrev} title={t("verify.prev")} aria-label={t("verify.prev")}>
            <ChevronRightIcon />
          </button>
        </span>
        {current && (
          <>
            <span className="tag">{current.institution_code} {current.account_number}</span>
            {current.quality_flags.map((flag) => (
              <span key={flag} className={`quality-tag ${flag}`}>{qualityFlagLabel(t, flag)}</span>
            ))}
          </>
        )}
        <span className="muted">{filtered.length} {t("verify.statements")}</span>
        {(dateFilter || bankFilter || acctFilter || qualityFilters.length) && (
          <button onClick={clearFilters}>{t("f.clear")}</button>
        )}
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
              render={(r) => `${r.trade_date} ${r.txn_type} ${r.symbol || ""} ${fmt(r.net_amount)} ${r.currency || ""}`}
              kind="transaction" idOf={(r) => r.transaction_id}
              titleOf={(r) => r.description || ""} selectedKey={selectedKey}
              onSelect={(key) => onSelect(key, "right_list")}
              matchedKeys={firstBoxByRef} itemRefs={itemRefs}
            />
            <ItemsGroup
              title={t("verify.positions")}
              rows={data.positions.filter((row) => row.currency === currency)}
              render={(r) => `${r.symbol || ""} ${fmt(r.quantity, 0)} ${fmt(r.market_value)} ${r.currency || ""}`}
              kind="position" idOf={(r) => r.snapshot_id}
              titleOf={(r) => r.raw_line || ""} selectedKey={selectedKey}
              onSelect={(key) => onSelect(key, "right_list")}
              matchedKeys={firstBoxByRef} itemRefs={itemRefs}
            />
            <ItemsGroup
              title={t("verify.cash")}
              rows={data.cash_balances.filter((row) => row.currency === currency)}
              render={(r) => `${t("verify.opening")} ${fmt(r.opening_balance)} · ${t("verify.closing")} ${fmt(r.closing_balance)} ${r.currency || ""}`}
              kind="cash" idOf={(r) => r.cash_balance_id}
              titleOf={(r) => r.raw_line || ""} selectedKey={selectedKey}
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
      {pages.map((page) => (
        <PdfPage
          key={page.page_number}
          doc={doc}
          pageNumber={page.page_number}
          width={page.width}
          height={page.height}
          scale={scale}
          boxes={boxesByPage.get(page.page_number) || []}
          selectedKey={selectedKey}
          onSelect={onSelect}
          pageRef={(el) => { pageRefs.current[page.page_number] = el; }}
          onRendered={() => {
            setRenderedCount((count) => count + 1);
            onPageRendered(page.page_number);
          }}
        />
      ))}
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

  return (
    <div
      className="verify-pdf-page"
      ref={pageRef}
      data-page={pageNumber}
      style={{ width: width * scale, height: height * scale }}
    >
      <canvas ref={canvasRef} />
      <span className="verify-physical-page">{pageNumber}</span>
      <div
        className="verify-page-overlay"
        style={{ width: size?.w ?? width * scale, height: size?.h ?? height * scale }}
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
        const key = refKey(kind, id);
        const isSelected = selectedKey === key;
        const hasBox = matchedKeys.has(key);
        const geometryStatus = r.geometry_status || "unavailable";
        const cls = ["verify-item"];
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
