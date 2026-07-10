import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import * as pdfjsLib from "pdfjs-dist";
// Bundle the worker through Vite so no external fetch is needed.
import PdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { api, LineBox, StatementBoxes, StatementRow } from "../api";
import { useI18n } from "../i18n";

pdfjsLib.GlobalWorkerOptions.workerSrc = PdfWorker;

// Render scale: PDF points → device pixels. 1.4 keeps bank statements legible
// without making multi-page statements enormous to scroll.
const RENDER_SCALE = 1.4;

type SelectedKey = string;
function refKey(kind: string, id: number): SelectedKey {
  return `${kind}:${id}`;
}

/** Map of selectedKey → the page index + top that holds its first matching box. */
function boxIndexForRefs(pages: StatementBoxes["pages"]): Map<SelectedKey, { page: number; top: number }> {
  const m = new Map<SelectedKey, { page: number; top: number }>();
  for (const page of pages) {
    for (const line of page.lines) {
      for (const ref of line.refs) {
        const key = refKey(ref.kind, ref.id);
        if (!m.has(key)) m.set(key, { page: page.page_number, top: line.bbox[1] });
      }
    }
  }
  return m;
}

export default function Verify() {
  const { t } = useI18n();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedKey, setSelectedKey] = useState<SelectedKey | null>(null);
  const [dateFilter, setDateFilter] = useState("");
  const [bankFilter, setBankFilter] = useState("");
  const [acctFilter, setAcctFilter] = useState("");

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
    // allRows is already ordered by period_end DESC; keep that order.
    return rows;
  }, [allRows, dateFilter, bankFilter, acctFilter]);

  // Default to the latest statement once the list arrives.
  useEffect(() => {
    if (selectedId === null && filtered.length > 0) {
      setSelectedId(filtered[0].statement_id);
    }
  }, [filtered, selectedId]);

  // Keep the selected id valid as filters change: if it falls out of the
  // filtered set, jump back to the latest visible one.
  useEffect(() => {
    if (selectedId !== null && filtered.length > 0 && !filtered.some((r) => r.statement_id === selectedId)) {
      setSelectedId(filtered[0].statement_id);
      setSelectedKey(null);
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
      setSelectedKey(null);
    }
  }
  function goNext() {
    if (hasNext) {
      setSelectedId(filtered[currentIndex - 1].statement_id);
      setSelectedKey(null);
    }
  }

  function clearFilters() {
    setDateFilter("");
    setBankFilter("");
    setAcctFilter("");
  }

  return (
    <>
      <h2>{t("nav.verify")}</h2>
      <div className="filters">
        <label>{t("f.date")}:&nbsp;
          <select value={dateFilter} onChange={(e) => { setDateFilter(e.target.value); setSelectedKey(null); }}>
            <option value="">{t("verify.all")}</option>
            {dateOpts.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <label>{t("f.institution")}:&nbsp;
          <select value={bankFilter} onChange={(e) => { setBankFilter(e.target.value); setSelectedKey(null); }}>
            <option value="">{t("verify.all")}</option>
            {bankOpts.map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </select>
        </label>
        <label>{t("f.account")}:&nbsp;
          <select value={acctFilter} onChange={(e) => { setAcctFilter(e.target.value); setSelectedKey(null); }}>
            <option value="">{t("verify.all")}</option>
            {acctOpts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
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
          <span className="tag">{current.institution_code} {current.account_number}</span>
        )}
        <span className="muted">{filtered.length} {t("verify.statements")}</span>
        {(dateFilter || bankFilter || acctFilter) && (
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
          selectedKey={selectedKey}
          onSelect={setSelectedKey}
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
  selectedKey,
  onSelect,
}: {
  statementId: number;
  selectedKey: SelectedKey | null;
  onSelect: (k: SelectedKey | null) => void;
}) {
  const { t } = useI18n();
  const boxesQ = useQuery({
    queryKey: ["statement-boxes", statementId],
    queryFn: () => api.statementBoxes(statementId),
  });

  const pdfUrl = api.statementPdfUrl(statementId);

  // Refs to each page container so we can scrollIntoView on select.
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const pdfScrollRef = useRef<HTMLDivElement | null>(null);

  const data: StatementBoxes | undefined = boxesQ.data;
  const firstBoxByRef = useMemo(
    () => (data ? boxIndexForRefs(data.pages) : new Map<SelectedKey, { page: number; top: number }>()),
    [data],
  );

  // When the selected key changes, scroll the PDF to the first matching box.
  useEffect(() => {
    if (!selectedKey || !data) return;
    const loc = firstBoxByRef.get(selectedKey);
    if (!loc) return;
    const el = pageRefs.current[loc.page];
    if (el) {
      const topPx = loc.top * RENDER_SCALE;
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      // fine-tune so the box isn't flush against the top edge.
      if (pdfScrollRef.current && topPx) {
        pdfScrollRef.current.scrollBy({ top: topPx - 24, behavior: "smooth" });
      }
    }
  }, [selectedKey, firstBoxByRef, data]);

  if (boxesQ.isLoading) return <p className="muted">{t("viz.loading")}</p>;
  if (boxesQ.error) {
    return <p className="inline-status status-error">{t("verify.load_failed")}: {String(boxesQ.error)}</p>;
  }
  if (!data) return null;

  if (data.pages.length === 0) {
    return <p className="muted">{t("verify.no_pdf")}</p>;
  }

  // Index boxes by page for the per-page overlay.
  const boxesByPage = new Map<number, LineBox[]>();
  for (const page of data.pages) {
    const matched = page.lines.filter((ln) => ln.refs.length > 0);
    if (matched.length) boxesByPage.set(page.page_number, matched);
  }

  return (
    <div className="verify-layout">
      <div className="verify-pane verify-pdf-pane" ref={pdfScrollRef}>
        <PdfView
          url={pdfUrl}
          pageCount={data.pages.length}
          scale={RENDER_SCALE}
          boxesByPage={boxesByPage}
          selectedKey={selectedKey}
          onSelect={onSelect}
          pageRefs={pageRefs}
        />
      </div>

      <div className="verify-pane verify-items-pane">
        <ItemsGroup
          title={t("verify.transactions")}
          rows={data.transactions}
          render={(r) => `${r.trade_date} ${r.txn_type} ${r.symbol || ""} ${fmt(r.net_amount)} ${r.currency || ""}`}
          kind="transaction"
          idOf={(r) => r.transaction_id}
          titleOf={(r) => r.description || ""}
          selectedKey={selectedKey}
          onSelect={onSelect}
          matchedKeys={firstBoxByRef}
        />
        <ItemsGroup
          title={t("verify.positions")}
          rows={data.positions}
          render={(r) => `${r.symbol || ""} ${fmt(r.quantity, 0)} ${fmt(r.market_value)} ${r.currency || ""}`}
          kind="position"
          idOf={(r) => r.snapshot_id}
          titleOf={(r) => r.raw_line || ""}
          selectedKey={selectedKey}
          onSelect={onSelect}
          matchedKeys={firstBoxByRef}
        />
        <ItemsGroup
          title={t("verify.cash")}
          rows={data.cash_balances}
          render={(r) => `${r.currency || ""} ${fmt(r.closing_balance)}`}
          kind="cash"
          idOf={(r) => r.cash_balance_id}
          titleOf={() => ""}
          selectedKey={selectedKey}
          onSelect={onSelect}
          matchedKeys={firstBoxByRef}
        />
        <ItemsGroup
          title={t("verify.quarantine")}
          rows={data.quarantine}
          render={(r) => r.reason || ""}
          kind="quarantine"
          idOf={(r) => r.quarantine_id}
          titleOf={(r) => r.raw_line || ""}
          selectedKey={selectedKey}
          onSelect={onSelect}
          matchedKeys={firstBoxByRef}
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
  pageCount,
  scale,
  boxesByPage,
  selectedKey,
  onSelect,
  pageRefs,
}: {
  url: string;
  pageCount: number;
  scale: number;
  boxesByPage: Map<number, LineBox[]>;
  selectedKey: SelectedKey | null;
  onSelect: (k: SelectedKey | null) => void;
  pageRefs: React.MutableRefObject<Record<number, HTMLDivElement | null>>;
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
      {Array.from({ length: pageCount }, (_, i) => i + 1).map((n) => (
        <PdfPage
          key={n}
          doc={doc}
          pageNumber={n}
          scale={scale}
          boxes={boxesByPage.get(n) || []}
          selectedKey={selectedKey}
          onSelect={onSelect}
          pageRef={(el) => { pageRefs.current[n] = el; }}
          onRendered={() => setRenderedCount((c) => Math.max(c, n))}
        />
      ))}
      {renderedCount < pageCount && (
        <span className="muted verify-rendering">{t("verify.rendering")} {renderedCount}/{pageCount}</span>
      )}
    </div>
  );
}

function PdfPage({
  doc,
  pageNumber,
  scale,
  boxes,
  selectedKey,
  onSelect,
  pageRef,
  onRendered,
}: {
  doc: pdfjsLib.PDFDocumentProxy;
  pageNumber: number;
  scale: number;
  boxes: LineBox[];
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
    <div className="verify-pdf-page" ref={pageRef} data-page={pageNumber}>
      <canvas ref={canvasRef} />
      {size && (
        <div className="verify-page-overlay" style={{ width: size.w, height: size.h }}>
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
      )}
    </div>
  );
}

function BoxDiv({
  line,
  scale,
  selectedKey,
  onSelect,
}: {
  line: LineBox;
  scale: number;
  selectedKey: SelectedKey | null;
  onSelect: (k: SelectedKey | null) => void;
}) {
  const [x0, top, x1, bottom] = line.bbox;
  const selected = line.refs.some((r) => selectedKey === refKey(r.kind, r.id));
  const related = !selected && selectedKey !== null &&
    line.refs.some((r) => selectedKey !== refKey(r.kind, r.id));

  const cls = ["verify-box"];
  if (selected) cls.push("selected");
  else if (related) cls.push("related");

  function onClick(e: React.MouseEvent) {
    e.stopPropagation();
    const first = line.refs[0];
    const key = refKey(first.kind, first.id);
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
      title={line.refs.map((r) => r.label).join(", ")}
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
}) {
  return (
    <div className="verify-group">
      <h4>{title} <span className="muted">({rows.length})</span></h4>
      {rows.map((r) => {
        const id = idOf(r);
        const key = refKey(kind, id);
        const isSelected = selectedKey === key;
        const hasBox = matchedKeys.has(key);
        const cls = ["verify-item"];
        if (isSelected) cls.push("selected");
        if (!hasBox) cls.push("no-box");
        return (
          <div
            key={key}
            className={cls.join(" ")}
            title={titleOf(r)}
            onClick={() => onSelect(isSelected ? null : key)}
          >
            <span className="verify-item-kind">{kindTag(kind)}</span>
            <span className="verify-item-text">{render(r)}</span>
            {!hasBox && <span className="verify-item-nobox" title="no matching PDF line">·</span>}
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
    default: return kind.charAt(0).toUpperCase();
  }
}
