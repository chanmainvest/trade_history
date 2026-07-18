import { useQuery } from "@tanstack/react-query";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, TxnRow } from "../api";
import { SmartSelect } from "../SmartSelect";
import { SourceLink } from "../SourceLink";
import { usePortfolio } from "../portfolio";
import { useI18n } from "../i18n";

function fmtNum(n: number | null | undefined, dec = 2) {
  if (n === null || n === undefined) return "";
  return n.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

const MONEY_THRESHOLDS = [
  { value: 0, label: "Any amount" },
  { value: 100, label: "≥ $100" },
  { value: 1_000, label: "≥ $1k" },
  { value: 10_000, label: "≥ $10k" },
  { value: 100_000, label: "≥ $100k" },
  { value: 1_000_000, label: "≥ $1M" },
];

type TxnColumnKey =
  | "source"
  | "date"
  | "institution"
  | "account"
  | "type"
  | "symbol"
  | "option"
  | "quantity"
  | "price"
  | "amount"
  | "currency"
  | "description";

type TxnColumnSpec = {
  key: TxnColumnKey;
  className: string;
  defaultWidth: number;
  minWidth: number;
  maxWidth: number;
};

const TRANSACTION_COLUMN_SPECS: TxnColumnSpec[] = [
  { key: "source", className: "txn-col-source", defaultWidth: 42, minWidth: 36, maxWidth: 56 },
  { key: "date", className: "txn-col-date", defaultWidth: 116, minWidth: 92, maxWidth: 180 },
  { key: "institution", className: "txn-col-institution", defaultWidth: 116, minWidth: 92, maxWidth: 220 },
  { key: "account", className: "txn-col-account", defaultWidth: 158, minWidth: 110, maxWidth: 260 },
  { key: "type", className: "txn-col-type", defaultWidth: 168, minWidth: 116, maxWidth: 280 },
  { key: "symbol", className: "txn-col-symbol", defaultWidth: 104, minWidth: 74, maxWidth: 180 },
  { key: "option", className: "txn-col-option", defaultWidth: 158, minWidth: 96, maxWidth: 320 },
  { key: "quantity", className: "txn-col-qty", defaultWidth: 96, minWidth: 78, maxWidth: 180 },
  { key: "price", className: "txn-col-price", defaultWidth: 92, minWidth: 74, maxWidth: 180 },
  { key: "amount", className: "txn-col-amount", defaultWidth: 132, minWidth: 98, maxWidth: 220 },
  { key: "currency", className: "txn-col-currency", defaultWidth: 72, minWidth: 64, maxWidth: 130 },
  { key: "description", className: "txn-col-description", defaultWidth: 460, minWidth: 220, maxWidth: 760 },
];

const DEFAULT_TRANSACTION_COLUMN_WIDTHS = TRANSACTION_COLUMN_SPECS.reduce(
  (widths, columnSpec) => ({ ...widths, [columnSpec.key]: columnSpec.defaultWidth }),
  {} as Record<TxnColumnKey, number>,
);

function clampColumnWidth(key: TxnColumnKey, width: number) {
  const columnSpec = TRANSACTION_COLUMN_SPECS.find((spec) => spec.key === key);
  if (!columnSpec) return width;
  return Math.min(columnSpec.maxWidth, Math.max(columnSpec.minWidth, Math.round(width)));
}

export default function Transactions() {
  const { activeAccountIds, accounts, config } = usePortfolio();
  const { t } = useI18n();
  const tableWrapRef = useRef<HTMLDivElement | null>(null);
  const scrollSnapTimerRef = useRef<number | undefined>(undefined);

  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [institutions, setInstitutions] = useState<string[]>([]);
  const [accountIds, setAccountIds] = useState<string[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [minAbs, setMinAbs] = useState(0);
  const [columnWidths, setColumnWidths] = useState<Record<TxnColumnKey, number>>(
    () => ({ ...DEFAULT_TRANSACTION_COLUMN_WIDTHS }),
  );
  const showSourceLinks = config?.show_source_links ?? true;
  const visibleColumnSpecs = useMemo(
    () => TRANSACTION_COLUMN_SPECS.filter((spec) => showSourceLinks || spec.key !== "source"),
    [showSourceLinks],
  );

  const tableMinWidth = useMemo(() => visibleColumnSpecs.reduce(
    (totalWidth, columnSpec) => totalWidth + columnWidths[columnSpec.key],
    0,
  ), [columnWidths, visibleColumnSpecs]);

  const syncTransactionsHeaderHeight = () => {
    const node = tableWrapRef.current;
    if (!node) return;
    const headerCell = node.querySelector<HTMLTableCellElement>("thead th");
    const headerHeight = headerCell?.getBoundingClientRect().height ?? 0;
    if (headerHeight > 0) {
      node.style.setProperty("--transactions-header-height", `${Math.ceil(headerHeight)}px`);
    }
  };

  const accountsQ = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const symbolsQ = useQuery({ queryKey: ["symbols"], queryFn: api.symbols });
  const typesQ = useQuery({ queryKey: ["txn-types"], queryFn: api.txnTypes });

  // If a portfolio is set AND the user hasn't manually picked accounts,
  // restrict the query to the portfolio's accounts.
  const effectiveAcctIds = accountIds.length > 0
    ? accountIds
    : activeAccountIds.length > 0
      ? activeAccountIds.map(String)
      : [];

  const txnsQ = useQuery({
    queryKey: ["txns", start, end, institutions, effectiveAcctIds, symbols, types, minAbs],
    queryFn: () =>
      api.transactions({
        start, end,
        institution: institutions,
        account_id: effectiveAcctIds,
        symbol: symbols,
        txn_type: types,
        min_abs_amount: minAbs > 0 ? minAbs : undefined,
        limit: 10_000,
      }),
  });

  const snapTransactionsToRow = () => {
    const node = tableWrapRef.current;
    if (!node) return;
    const firstRow = node.querySelector<HTMLTableRowElement>("tbody tr");
    const rowHeight = firstRow?.getBoundingClientRect().height ?? 0;
    if (rowHeight <= 0) return;

    syncTransactionsHeaderHeight();

    const maxScrollTop = node.scrollHeight - node.clientHeight;
    const target = Math.min(
      Math.max(Math.round(node.scrollTop / rowHeight) * rowHeight, 0),
      maxScrollTop,
    );
    if (Math.abs(node.scrollTop - target) < 0.5) return;
    node.scrollTop = target;
  };

  const scheduleTransactionsSnap = () => {
    window.clearTimeout(scrollSnapTimerRef.current);
    scrollSnapTimerRef.current = window.setTimeout(snapTransactionsToRow, 90);
  };

  const setColumnWidth = (key: TxnColumnKey, width: number) => {
    const nextWidth = clampColumnWidth(key, width);
    setColumnWidths((currentWidths) => {
      if (currentWidths[key] === nextWidth) return currentWidths;
      return { ...currentWidths, [key]: nextWidth };
    });
  };

  const resetColumnWidth = (key: TxnColumnKey) => {
    setColumnWidth(key, DEFAULT_TRANSACTION_COLUMN_WIDTHS[key]);
  };

  const startColumnResize = (event: ReactPointerEvent<HTMLSpanElement>, key: TxnColumnKey) => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();

    const startClientX = event.clientX;
    const startWidth = columnWidths[key];

    const handlePointerMove = (moveEvent: PointerEvent) => {
      setColumnWidth(key, startWidth + moveEvent.clientX - startClientX);
    };

    const handlePointerUp = () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.requestAnimationFrame(syncTransactionsHeaderHeight);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp, { once: true });
  };

  const resizeColumnWithKeyboard = (event: ReactKeyboardEvent<HTMLSpanElement>, key: TxnColumnKey) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    event.stopPropagation();

    const direction = event.key === "ArrowRight" ? 1 : -1;
    const step = event.shiftKey ? 24 : 8;
    setColumnWidth(key, columnWidths[key] + direction * step);
  };

  useEffect(() => {
    const node = tableWrapRef.current;
    const headerCell = node?.querySelector<HTMLTableCellElement>("thead th");
    const resizeObserver = "ResizeObserver" in window
      ? new ResizeObserver(syncTransactionsHeaderHeight)
      : undefined;
    syncTransactionsHeaderHeight();
    if (headerCell) resizeObserver?.observe(headerCell);
    window.addEventListener("resize", syncTransactionsHeaderHeight);
    snapTransactionsToRow();
    return () => {
      window.clearTimeout(scrollSnapTimerRef.current);
      resizeObserver?.disconnect();
      window.removeEventListener("resize", syncTransactionsHeaderHeight);
    };
  }, [txnsQ.data?.rows.length]);

  const instOptions = useMemo(() => {
    const set = new Set<string>();
    for (const a of accountsQ.data?.rows ?? []) set.add(a.institution_code);
    return Array.from(set).sort().map((c) => ({ value: c, label: c }));
  }, [accountsQ.data]);

  const acctOptions = useMemo(() => {
    return (accountsQ.data?.rows ?? []).map((a) => ({
      value: String(a.account_id),
      label: `${a.institution_code} • ${a.account_number}`,
      hint: a.base_currency + (a.nickname ? ` · ${a.nickname}` : ""),
    }));
  }, [accountsQ.data]);

  const symOptions = useMemo(() => {
    const seen = new Set<string>();
    const out: { value: string; label: string; hint?: string }[] = [];
    for (const r of symbolsQ.data?.rows ?? []) {
      if (seen.has(r.symbol)) continue;
      seen.add(r.symbol);
      out.push({ value: r.symbol, label: r.symbol, hint: `${r.asset_type} · ${r.currency}` });
    }
    return out.sort((a, b) => a.label.localeCompare(b.label));
  }, [symbolsQ.data]);

  const typeOptions = useMemo(() =>
    (typesQ.data?.rows ?? []).map((t) => ({ value: t, label: t })),
    [typesQ.data]);

  const acctById = useMemo(() => {
    const m: Record<number, string> = {};
    for (const a of accounts) m[a.account_id] = a.account_number;
    return m;
  }, [accounts]);

  const renderResizableHeader = (key: TxnColumnKey, label: string, className?: string) => (
    <th className={className}>
      <span className="column-header-label">{label}</span>
      <span
        aria-label={`Resize ${label} column`}
        aria-orientation="vertical"
        aria-valuemax={TRANSACTION_COLUMN_SPECS.find((columnSpec) => columnSpec.key === key)?.maxWidth}
        aria-valuemin={TRANSACTION_COLUMN_SPECS.find((columnSpec) => columnSpec.key === key)?.minWidth}
        aria-valuenow={columnWidths[key]}
        className="column-resize-handle"
        onClick={(event) => event.stopPropagation()}
        onDoubleClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          resetColumnWidth(key);
        }}
        onKeyDown={(event) => resizeColumnWithKeyboard(event, key)}
        onPointerDown={(event) => startColumnResize(event, key)}
        role="separator"
        tabIndex={0}
        title={`Resize ${label}`}
      />
    </th>
  );

  return (
    <>
      <h2>{t("nav.transactions")}</h2>
      <div className="filters">
        <input type="date" value={start} onChange={(e) => setStart(e.target.value)} title={t("f.start")} />
        <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} title={t("f.end")} />
        <SmartSelect label={t("f.institution")} options={instOptions} value={institutions} onChange={setInstitutions} />
        <SmartSelect label={t("f.account")} options={acctOptions} value={accountIds} onChange={setAccountIds} />
        <SmartSelect label={t("f.symbol")} options={symOptions} value={symbols} onChange={setSymbols} />
        <SmartSelect label={t("f.type")} options={typeOptions} value={types} onChange={setTypes} />
        <label>{t("f.min_abs_amount")}:&nbsp;
          <select value={minAbs} onChange={(e) => setMinAbs(parseFloat(e.target.value))}>
            {MONEY_THRESHOLDS.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </label>
        <span className="muted">
          {txnsQ.data?.count ?? 0}{txnsQ.data?.has_more ? ` of ${txnsQ.data.total_count}` : ""} rows
        </span>
        {txnsQ.data?.has_more && (
          <span className="tag accent">limited to first {txnsQ.data.count.toLocaleString()} rows</span>
        )}
        {activeAccountIds.length > 0 && accountIds.length === 0 && (
          <span className="tag accent">portfolio filter on</span>
        )}
      </div>

      {txnsQ.isLoading && <p className="muted">Loading transactions…</p>}
      {txnsQ.isError && (
        <div className="card status-error">
          Transactions API is not responding. Start the backend with
          <code> uv run ledger serve </code> and reload this page.
        </div>
      )}
      {!txnsQ.isLoading && !txnsQ.isError && (txnsQ.data?.rows.length ?? 0) === 0 && (
        <div className="card muted">
          No transactions match the current filters.
        </div>
      )}

      <div className="card table-scroll transactions-table-wrap" ref={tableWrapRef} onScroll={scheduleTransactionsSnap}>
        <table className="transactions-table" style={{ minWidth: `${tableMinWidth}px` }}>
          <colgroup>
            {visibleColumnSpecs.map((columnSpec) => (
              <col
                className={columnSpec.className}
                key={columnSpec.key}
                style={{ width: `${columnWidths[columnSpec.key]}px` }}
              />
            ))}
          </colgroup>
          <thead>
            <tr>
              {showSourceLinks && <th aria-label={t("source.column")} />}
              {renderResizableHeader("date", t("th.date"))}
              {renderResizableHeader("institution", t("f.institution"))}
              {renderResizableHeader("account", t("th.account"))}
              {renderResizableHeader("type", t("th.type"))}
              {renderResizableHeader("symbol", t("th.symbol"))}
              {renderResizableHeader("option", "Option")}
              {renderResizableHeader("quantity", t("th.quantity"), "num")}
              {renderResizableHeader("price", t("th.price"), "num")}
              {renderResizableHeader("amount", t("th.amount"), "num")}
              {renderResizableHeader("currency", t("th.currency"))}
              {renderResizableHeader("description", t("th.description"))}
            </tr>
          </thead>
          <tbody>
            {txnsQ.data?.rows.map((row: TxnRow) => (
              <tr key={row.row_id}>
                {showSourceLinks && (
                  <td>
                    {row.statement_id !== null && row.transaction_id !== null ? (
                      <SourceLink
                        source={{
                          statement_id: row.statement_id,
                          kind: "transaction",
                          id: row.transaction_id,
                        }}
                        title={t("source.open_transaction")}
                      />
                    ) : null}
                  </td>
                )}
                <td>{row.trade_date}</td>
                <td>{row.institution_code}</td>
                <td>{acctById[row.account_id] || row.account_number}</td>
                <td>{row.txn_type === "initial_position" ? t("transaction.type.initial_position") : row.txn_type}</td>
                <td>{row.symbol ? <Link to={`/research/${row.symbol}`}>
                  {row.related_symbol ? `${row.symbol} → ${row.related_symbol}` : row.symbol}
                </Link> : ""}</td>
                <td>{row.option_type ? `${row.option_type} ${fmtNum(row.option_strike, 2)} ${row.option_expiry || ""}` : ""}</td>
                <td className="num">{fmtNum(row.quantity, 0)}</td>
                <td className="num">{fmtNum(row.price)}</td>
                <td className={"num " + ((row.net_amount ?? 0) < 0 ? "neg" : "pos")}>{fmtNum(row.net_amount)}</td>
                <td>{row.currency}</td>
                <td className="description-cell">
                  {row.description}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
