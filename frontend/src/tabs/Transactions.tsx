import { useQuery } from "@tanstack/react-query";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, TxnRow } from "../api";
import { SmartFilterIcon, type SmartOption } from "../SmartSelect";
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

// Virtualization: rows are fixed-height single lines (nowrap + ellipsis), so
// only the slice near the viewport is mounted between two spacer rows.
const ROW_OVERSCAN = 8;
const FALLBACK_ROW_HEIGHT = 29;

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
  const [showAllAccounts, setShowAllAccounts] = useState(false);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [currencies, setCurrencies] = useState<string[]>([]);
  const [sort, setSort] = useState<{ key: TxnColumnKey; dir: 1 | -1 } | null>(null);
  const [minAbs, setMinAbs] = useState(0);
  const [columnWidths, setColumnWidths] = useState<Record<TxnColumnKey, number>>(
    () => ({ ...DEFAULT_TRANSACTION_COLUMN_WIDTHS }),
  );
  const [rowHeight, setRowHeight] = useState(FALLBACK_ROW_HEIGHT);
  const [virtualRange, setVirtualRange] = useState({ start: 0, end: 0 });
  const rowHeightRef = useRef(FALLBACK_ROW_HEIGHT);
  const rowCountRef = useRef(0);
  const scrollRafRef = useRef<number | undefined>(undefined);
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

  // Option lists follow the top-bar portfolio scope (the "All accounts"
  // bypass restores the full lists), but not the user's other filter picks,
  // so selecting one account never empties the other dropdowns.
  const optionScopeIds = activeAccountIds.length > 0 && !showAllAccounts
    ? activeAccountIds.map(String)
    : [];
  const scopedOptionParams = optionScopeIds.length
    ? { account_id: optionScopeIds.map(Number) }
    : {};
  const symbolsQ = useQuery({
    queryKey: ["symbols", optionScopeIds],
    queryFn: () => api.symbols(scopedOptionParams),
  });
  const typesQ = useQuery({
    queryKey: ["txn-types", optionScopeIds],
    queryFn: () => api.txnTypes(scopedOptionParams),
  });
  const currenciesQ = useQuery({
    queryKey: ["currencies", optionScopeIds],
    queryFn: () => api.currencies(scopedOptionParams),
  });

  // If a portfolio is set AND the user hasn't manually picked accounts,
  // restrict the query to the portfolio's accounts.
  const effectiveAcctIds = showAllAccounts
    ? []
    : accountIds.length > 0
      ? accountIds
      : activeAccountIds.length > 0
        ? activeAccountIds.map(String)
        : [];

  const txnsQ = useQuery({
    queryKey: ["txns", start, end, institutions, effectiveAcctIds, symbols, types, currencies, minAbs],
    queryFn: () =>
      api.transactions({
        start, end,
        institution: institutions,
        account_id: effectiveAcctIds,
        symbol: symbols,
        txn_type: types,
        currency: currencies,
        min_abs_amount: minAbs > 0 ? minAbs : undefined,
        limit: 10_000,
      }),
  });

  const rows = txnsQ.data?.rows ?? [];
  rowCountRef.current = rows.length;

  const toggleSort = (key: TxnColumnKey) => {
    setSort((current) =>
      current?.key === key ? { key, dir: current.dir === 1 ? -1 : 1 } : { key, dir: 1 },
    );
  };

  // A new sort order should be read from its first row, not wherever the
  // viewport happened to be scrolled.
  useEffect(() => {
    const node = tableWrapRef.current;
    if (node && node.scrollTop !== 0) node.scrollTop = 0;
    updateVirtualRange();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sort]);

  const sortValue = (row: TxnRow): string | number | null => {
    switch (sort?.key) {
      case "date": return row.trade_date;
      case "institution": return row.institution_code;
      case "account": return row.account_number;
      case "type": return row.txn_type;
      case "symbol": return row.symbol ?? "";
      case "option":
        return row.option_type
          ? `${row.option_type}|${row.option_strike ?? 0}|${row.option_expiry ?? ""}`
          : "";
      case "quantity": return row.quantity;
      case "price": return row.price;
      case "amount": return row.net_amount;
      case "currency": return row.currency ?? "";
      case "description": return row.description ?? "";
      default: return "";
    }
  };

  const sortedRows = useMemo(() => {
    if (!sort) return rows;
    const dir = sort.dir;
    const copy = [...rows];
    copy.sort((a, b) => {
      const va = sortValue(a);
      const vb = sortValue(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1; // nulls last regardless of direction
      if (vb == null) return -1;
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
      return String(va).localeCompare(String(vb), undefined, { numeric: true }) * dir;
    });
    return copy;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, sort]);

  const updateVirtualRange = () => {
    const node = tableWrapRef.current;
    if (!node) return;
    const total = rowCountRef.current;
    const height = rowHeightRef.current;
    const start = Math.max(0, Math.floor(node.scrollTop / height) - ROW_OVERSCAN);
    const end = Math.min(
      total,
      Math.ceil((node.scrollTop + node.clientHeight) / height) + ROW_OVERSCAN,
    );
    setVirtualRange((current) =>
      current.start === start && current.end === end ? current : { start, end },
    );
  };

  const measureRowHeight = () => {
    const node = tableWrapRef.current;
    if (!node) return;
    const dataRow = node.querySelector<HTMLTableRowElement>("tbody tr:not(.virtual-pad)");
    const measured = dataRow?.getBoundingClientRect().height ?? 0;
    if (measured > 0 && measured !== rowHeightRef.current) {
      rowHeightRef.current = measured;
      setRowHeight(measured);
    }
  };

  const handleTableScroll = () => {
    scheduleTransactionsSnap();
    if (scrollRafRef.current !== undefined) return;
    scrollRafRef.current = window.requestAnimationFrame(() => {
      scrollRafRef.current = undefined;
      updateVirtualRange();
    });
  };

  const snapTransactionsToRow = () => {
    const node = tableWrapRef.current;
    if (!node) return;
    const firstRow = node.querySelector<HTMLTableRowElement>("tbody tr:not(.virtual-pad)");
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
    measureRowHeight();
    updateVirtualRange();
    snapTransactionsToRow();
    return () => {
      window.clearTimeout(scrollSnapTimerRef.current);
      if (scrollRafRef.current !== undefined) {
        window.cancelAnimationFrame(scrollRafRef.current);
        scrollRafRef.current = undefined;
      }
      resizeObserver?.disconnect();
      window.removeEventListener("resize", syncTransactionsHeaderHeight);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows.length]);

  // The top-bar portfolio scopes the institution/account dropdown options the
  // same way it scopes the query; "All accounts" bypasses it for both.
  const portfolioScoped = activeAccountIds.length > 0 && !showAllAccounts;
  const scopedAccounts = useMemo(() => {
    const all = accountsQ.data?.rows ?? [];
    if (!portfolioScoped) return all;
    const ids = new Set(activeAccountIds.map(String));
    return all.filter((a) => ids.has(String(a.account_id)));
  }, [accountsQ.data, portfolioScoped, activeAccountIds]);

  const instOptions = useMemo(() => {
    const set = new Set<string>();
    for (const a of scopedAccounts) set.add(a.institution_code);
    return Array.from(set).sort().map((c) => ({ value: c, label: c }));
  }, [scopedAccounts]);

  const acctOptions = useMemo(() => {
    return scopedAccounts.map((a) => ({
      value: String(a.account_id),
      label: `${a.institution_code} • ${a.account_number}`,
      hint: a.base_currency + (a.nickname ? ` · ${a.nickname}` : ""),
    }));
  }, [scopedAccounts]);

  // Drop selected filter values that the active portfolio excludes.
  useEffect(() => {
    if (!portfolioScoped) return;
    const validInst = new Set(scopedAccounts.map((a) => a.institution_code));
    const validAcct = new Set(scopedAccounts.map((a) => String(a.account_id)));
    setInstitutions((prev) => {
      const next = prev.filter((v) => validInst.has(v));
      return next.length === prev.length ? prev : next;
    });
    setAccountIds((prev) => {
      const next = prev.filter((v) => validAcct.has(v));
      return next.length === prev.length ? prev : next;
    });
  }, [portfolioScoped, scopedAccounts]);

  // Symbol/currency options arrive from scoped endpoints; prune once loaded.
  useEffect(() => {
    if (!portfolioScoped) return;
    if (symbolsQ.data) {
      const validSym = new Set(symbolsQ.data.rows.map((r) => r.symbol));
      setSymbols((prev) => {
        const next = prev.filter((v) => validSym.has(v));
        return next.length === prev.length ? prev : next;
      });
    }
    if (currenciesQ.data) {
      const validCcy = new Set(currenciesQ.data.rows);
      setCurrencies((prev) => {
        const next = prev.filter((v) => validCcy.has(v));
        return next.length === prev.length ? prev : next;
      });
    }
  }, [portfolioScoped, symbolsQ.data, currenciesQ.data]);

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

  const ccyOptions = useMemo(() =>
    (currenciesQ.data?.rows ?? []).map((c) => ({ value: c, label: c })),
    [currenciesQ.data]);

  const acctById = useMemo(() => {
    const m: Record<number, string> = {};
    for (const a of accounts) m[a.account_id] = a.account_number;
    return m;
  }, [accounts]);

  const renderResizableHeader = (
    key: TxnColumnKey,
    label: string,
    className?: string,
    filter?: { options: SmartOption[]; value: string[]; onChange: (v: string[]) => void },
  ) => (
    <th className={className}>
      <span className="column-header-inner">
        <button
          type="button"
          className="column-sort-btn"
          title={`Sort by ${label}`}
          aria-label={`Sort by ${label}`}
          onClick={(event) => {
            event.stopPropagation();
            toggleSort(key);
          }}
        >
          <span className="column-header-label">{label}</span>
          <span className="column-sort-arrow" aria-hidden="true">
            {sort?.key === key ? (sort.dir === 1 ? "▲" : "▼") : ""}
          </span>
        </button>
        {filter && (
          <SmartFilterIcon
            label={label}
            options={filter.options}
            value={filter.value}
            onChange={filter.onChange}
          />
        )}
      </span>
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
      <div className="filters">
        <input type="date" value={start} onChange={(e) => setStart(e.target.value)} title={t("f.start")} />
        <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} title={t("f.end")} />
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
        {activeAccountIds.length > 0 && !showAllAccounts && accountIds.length === 0 && (
          <span className="tag accent">portfolio filter on</span>
        )}
        {activeAccountIds.length > 0 && (
          <button
            type="button"
            className={showAllAccounts ? "tag accent" : "tag"}
            onClick={() => {
              setShowAllAccounts(true);
              setAccountIds([]);
            }}
          >
            {t("monthly.all_accounts")}
          </button>
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

      <div className="card table-scroll transactions-table-wrap" ref={tableWrapRef} onScroll={handleTableScroll}>
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
              {renderResizableHeader("institution", t("f.institution"), undefined, {
                options: instOptions, value: institutions, onChange: setInstitutions,
              })}
              {renderResizableHeader("account", t("th.account"), undefined, {
                options: acctOptions,
                value: accountIds,
                onChange: (value) => {
                  setShowAllAccounts(false);
                  setAccountIds(value);
                },
              })}
              {renderResizableHeader("type", t("th.type"), undefined, {
                options: typeOptions, value: types, onChange: setTypes,
              })}
              {renderResizableHeader("symbol", t("th.symbol"), undefined, {
                options: symOptions, value: symbols, onChange: setSymbols,
              })}
              {renderResizableHeader("option", "Option")}
              {renderResizableHeader("quantity", t("th.quantity"), "num")}
              {renderResizableHeader("price", t("th.price"), "num")}
              {renderResizableHeader("amount", t("th.amount"), "num")}
              {renderResizableHeader("currency", t("th.currency"), undefined, {
                options: ccyOptions, value: currencies, onChange: setCurrencies,
              })}
              {renderResizableHeader("description", t("th.description"))}
            </tr>
          </thead>
          <tbody>
            {virtualRange.start > 0 && (
              <tr className="virtual-pad" aria-hidden="true">
                <td colSpan={visibleColumnSpecs.length} style={{ height: virtualRange.start * rowHeight }} />
              </tr>
            )}
            {sortedRows.slice(virtualRange.start, virtualRange.end).map((row: TxnRow, index: number) => {
              const absoluteIndex = virtualRange.start + index;
              return (
              <tr key={row.row_id} className={absoluteIndex % 2 === 1 ? "row-alt-row" : undefined}>
                {showSourceLinks && (
                  <td>
                    {row.source_ref?.linkable ? (
                      <SourceLink
                        source={row.source_ref}
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
              );
            })}
            {rows.length - virtualRange.end > 0 && (
              <tr className="virtual-pad" aria-hidden="true">
                <td colSpan={visibleColumnSpecs.length} style={{ height: (rows.length - virtualRange.end) * rowHeight }} />
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
