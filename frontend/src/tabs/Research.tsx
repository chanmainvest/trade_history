import { useQuery } from "@tanstack/react-query";
import { useParams, useNavigate } from "react-router-dom";
import { useMemo, useState } from "react";
import Plot from "react-plotly.js";
import { api } from "../api";
import { plotlyTheme } from "../theme";
import { useI18n } from "../i18n";
import { CollapsibleCard } from "../CollapsibleCard";
import { sortTrades } from "../tradeSort";
import type { TradeCol } from "../tradeSort";

type Freq = "D" | "W" | "M";
type Period = "1d" | "1w" | "1m" | "3m" | "6m" | "1y" | "3y" | "5y" | "10y" | "max";
const PERIODS: Period[] = ["1d", "1w", "1m", "3m", "6m", "1y", "3y", "5y", "10y", "max"];

function daysFor(p: Period): number {
  switch (p) {
    case "1d": return 1;
    case "1w": return 7;
    case "1m": return 30;
    case "3m": return 90;
    case "6m": return 180;
    case "1y": return 365;
    case "3y": return 365 * 3;
    case "5y": return 365 * 5;
    case "10y": return 365 * 10;
    case "max": return 365 * 100;
  }
}

function fmtNum(n: number | null | undefined, dec = 2) {
  if (n == null) return "";
  return n.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function interpolate(text: string, values: Record<string, string>): string {
  return Object.entries(values).reduce(
    (result, [key, value]) => result.replaceAll(`{${key}}`, value),
    text,
  );
}

const OPTION_TYPES = new Set([
  "option_buy_to_open", "option_sell_to_open",
  "option_buy_to_close", "option_sell_to_close",
  "option_assignment", "option_exercise", "option_expiration",
]);

export default function Research() {
  const params = useParams<{ symbol: string }>();
  const nav = useNavigate();
  const { t } = useI18n();
  const symbol = (params.symbol || "").toUpperCase();
  const [input, setInput] = useState(symbol);
  const [freq, setFreq] = useState<Freq>("D");
  const [period, setPeriod] = useState<Period>("1y");
  const [showMA50, setShowMA50] = useState(true);
  const [showMA200, setShowMA200] = useState(true);
  const [searchOpen, setSearchOpen] = useState(false);
  const [finPeriod, setFinPeriod] = useState<"quarterly" | "annual">("quarterly");
  const [finMetrics, setFinMetrics] = useState<Record<string, boolean>>({
    revenue: true, net_income: true, free_cash_flow: true,
    eps_diluted: false, gross_profit: false, operating_income: false,
  });

  const pricesQ = useQuery({
    queryKey: ["prices", symbol, freq],
    queryFn: () => api.prices(symbol, freq),
    enabled: !!symbol,
  });
  const tradesQ = useQuery({
    queryKey: ["trades", symbol],
    queryFn: () => api.trades(symbol),
    enabled: !!symbol,
  });
  const finQ = useQuery({
    queryKey: ["fin", symbol, finPeriod],
    queryFn: () => api.financials(symbol, finPeriod),
    enabled: !!symbol,
  });
  const symbolsQ = useQuery({ queryKey: ["symbols"], queryFn: api.symbols });

  const allRows = pricesQ.data?.rows ?? [];
  // Period cutoff
  const cutoff = useMemo(() => {
    if (period === "max" || allRows.length === 0) return null;
    const d = new Date(); d.setDate(d.getDate() - daysFor(period));
    return d.toISOString().slice(0, 10);
  }, [period, allRows.length]);
  const rows = useMemo(() => {
    if (!cutoff) return allRows;
    return allRows.filter((r: any) => r.trade_date >= cutoff);
  }, [allRows, cutoff]);

  const movingAverages = useMemo(() => {
    const build = (windowSize: number) => {
      const values = new Map<string, number>();
      let sum = 0;
      for (let i = 0; i < allRows.length; i++) {
        sum += Number(allRows[i].close);
        if (i >= windowSize) sum -= Number(allRows[i - windowSize].close);
        if (i >= windowSize - 1) {
          values.set(allRows[i].trade_date, sum / windowSize);
        }
      }
      return values;
    };
    return { 50: build(50), 200: build(200) };
  }, [allRows]);

  const ma = (n: 50 | 200) => rows.map(
    (row: any) => movingAverages[n].get(row.trade_date) ?? null,
  );

  const allTrades = tradesQ.data?.rows ?? [];
  const trades = useMemo(() => {
    if (!cutoff) return allTrades;
    return allTrades.filter((t: any) => t.trade_date >= cutoff);
  }, [allTrades, cutoff]);

  const [tradeSortCol, setTradeSortCol] = useState<TradeCol | null>(null);
  const [tradeSortDir, setTradeSortDir] = useState<"asc" | "desc">("asc");
  const sortedTrades = useMemo(
    () => sortTrades(allTrades, tradeSortCol, tradeSortDir),
    [allTrades, tradeSortCol, tradeSortDir],
  );
  function toggleTradeSort(c: TradeCol) {
    if (c === tradeSortCol) setTradeSortDir(tradeSortDir === "asc" ? "desc" : "asc");
    else { setTradeSortCol(c); setTradeSortDir("asc"); }
  }
  function tradeArrow(c: TradeCol) {
    return c === tradeSortCol ? (tradeSortDir === "asc" ? " ▲" : " ▼") : "";
  }

  function isBuy(t: any): boolean {
    return t.txn_type.startsWith("buy") || t.txn_type === "option_buy_to_open" || t.txn_type === "option_buy_to_close";
  }
  function isSell(t: any): boolean {
    return t.txn_type.startsWith("sell") || t.txn_type === "option_sell_to_open" || t.txn_type === "option_sell_to_close";
  }
  function isOption(t: any): boolean { return OPTION_TYPES.has(t.txn_type); }

  const buyStock = trades.filter((t) => isBuy(t) && !isOption(t));
  const sellStock = trades.filter((t) => isSell(t) && !isOption(t));
  const buyOpt = trades.filter((t) => isBuy(t) && isOption(t));
  const sellOpt = trades.filter((t) => isSell(t) && isOption(t));

  const theme = plotlyTheme();

  const summary = useMemo(() => {
    if (allRows.length === 0) return null;
    const last = allRows[allRows.length - 1];
    // today's live bar can carry a null close; show the last completed close
    const lastClosed = [...allRows].reverse().find((r: any) => r.close !== null && r.close !== undefined) ?? last;
    const first = rows.length > 0 ? rows[0] : lastClosed;
    const lastClose = Number(lastClosed.close);
    const periodPct = Number(first.adj_close ?? first.close) > 0
      ? (lastClose / Number(first.adj_close ?? first.close) - 1) * 100
      : null;
    const cutoff52w = new Date(); cutoff52w.setFullYear(cutoff52w.getFullYear() - 1);
    const rows52w = allRows.filter((r: any) => new Date(r.trade_date) >= cutoff52w);
    const high = rows52w.length ? Math.max(...rows52w.map((r: any) => Number(r.high))) : null;
    const low = rows52w.length ? Math.min(...rows52w.map((r: any) => Number(r.low))) : null;
    return { lastClose, periodPct, high, low, dataThrough: String(last.trade_date) };
  }, [allRows, rows]);

  const staleDays = useMemo(() => {
    if (!summary) return null;
    const days = Math.floor((Date.now() - new Date(summary.dataThrough).getTime()) / 86400000);
    return days >= 7 ? days : null;
  }, [summary]);

  const symbolOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of symbolsQ.data?.rows ?? []) {
      if (!row.symbol) continue;
      seen.set(row.symbol, `${row.asset_type} • ${row.currency}`);
    }
    return Array.from(seen.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [symbolsQ.data]);

  const filteredSymbols = useMemo(() => {
    const query = input.trim().toLowerCase();
    if (!query) return symbolOptions;
    return symbolOptions.filter(([ticker, hint]) =>
      ticker.toLowerCase().includes(query) || hint.toLowerCase().includes(query));
  }, [input, symbolOptions]);

  function go() {
    if (input.trim()) nav(`/research/${input.trim().toUpperCase()}`);
  }
  function chooseSymbol(nextSymbol: string) {
    setInput(nextSymbol);
    setSearchOpen(false);
    nav(`/research/${nextSymbol}`);
  }

  return (
    <>
      {(pricesQ.data?.ticker_changes?.length ?? 0) > 0 && (
        <p className="muted">
          {t("research.tickerHistory")}: {pricesQ.data!.ticker_changes.map((change) =>
            `${change.from_symbol} → ${change.to_symbol} (${change.effective_date})`
          ).join(", ")}. {t("research.currentTicker")}: {pricesQ.data!.symbol}.
        </p>
      )}
      <div className="filters">
        <div className="ticker-search">
          <input value={input}
                 onFocus={() => setSearchOpen(true)}
                 onChange={(e) => { setInput(e.target.value.toUpperCase()); setSearchOpen(true); }}
                 placeholder={t("f.symbol") + " (e.g. AAPL)"}
                 onKeyDown={(e) => { if (e.key === "Enter") go(); if (e.key === "Escape") setSearchOpen(false); }}
                 onBlur={() => window.setTimeout(() => setSearchOpen(false), 120)}
                 style={{ minWidth: 180 }} />
          {searchOpen && (
            <div className="ticker-search-panel">
              {filteredSymbols.map(([ticker, hint]) => (
                <button key={ticker} type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => chooseSymbol(ticker)}>
                  <strong>{ticker}</strong>
                  <span>{hint}</span>
                </button>
              ))}
              {filteredSymbols.length === 0 && <div className="ticker-search-empty">{t("research.no_matching")}</div>}
            </div>
          )}
        </div>
        {PERIODS.map((p) => (
          <button key={p} className={p === period ? "active" : ""}
                  onClick={() => setPeriod(p)}>{t(`period.${p}`)}</button>
        ))}
        <span className="muted">|</span>
        {(["D", "W", "M"] as Freq[]).map((f) =>
          <button key={f} className={f === freq ? "active" : ""} onClick={() => setFreq(f)}>{f}</button>
        )}
        <label><input type="checkbox" checked={showMA50}
                      onChange={(e) => setShowMA50(e.target.checked)} /> MA50</label>
        <label><input type="checkbox" checked={showMA200}
                      onChange={(e) => setShowMA200(e.target.checked)} /> MA200</label>
      </div>

      {!symbol && (
        <p className="muted">{t("research.enter_symbol")}</p>
      )}

      {symbol && summary && (
        <div className="filters">
          <span><strong>{t("research.last_close")}:</strong> {fmtNum(summary.lastClose)}</span>
          <span>
            <strong>{t("research.period_change")} ({period}):</strong>{" "}
            {summary.periodPct == null ? "n/a" : (
              <span className={summary.periodPct >= 0 ? "pos" : "neg"}>
                {summary.periodPct > 0 ? "+" : ""}{summary.periodPct.toFixed(2)}%
              </span>
            )}
          </span>
          {summary.high != null && (
            <span><strong>{t("research.range_52w")}:</strong> {fmtNum(summary.low)} – {fmtNum(summary.high)}</span>
          )}
          <span className="muted">
            {interpolate(t("viz.price_data_through"), { date: summary.dataThrough })}
            {staleDays != null && interpolate(t("viz.data_stale"), { days: String(staleDays) })}
          </span>
        </div>
      )}

      {symbol && (
        <CollapsibleCard title={t("research.price_chart")}>
          <Plot
            data={[
              {
                type: "candlestick", name: symbol, yaxis: "y",
                x: rows.map((r: any) => r.trade_date),
                open: rows.map((r: any) => r.open),
                high: rows.map((r: any) => r.high),
                low: rows.map((r: any) => r.low),
                close: rows.map((r: any) => r.close),
                increasing: { line: { color: theme.pos } },
                decreasing: { line: { color: theme.neg } },
              },
              ...(showMA50 ? [{
                type: "scatter" as const, mode: "lines" as const, name: "MA50", yaxis: "y",
                x: rows.map((r: any) => r.trade_date), y: ma(50),
                line: { color: "#f0a020", width: 1 },
              }] : []),
              ...(showMA200 ? [{
                type: "scatter" as const, mode: "lines" as const, name: "MA200", yaxis: "y",
                x: rows.map((r: any) => r.trade_date), y: ma(200),
                line: { color: "#9966ff", width: 1 },
              }] : []),
              // Stock buys/sells: solid triangles
              {
                type: "scatter", mode: "markers", name: "Buy (stock)", yaxis: "y",
                x: buyStock.map((t: any) => t.trade_date),
                y: buyStock.map((t: any) => t.price),
                marker: { color: theme.pos, symbol: "triangle-up", size: 12 },
              },
              {
                type: "scatter", mode: "markers", name: "Sell (stock)", yaxis: "y",
                x: sellStock.map((t: any) => t.trade_date),
                y: sellStock.map((t: any) => t.price),
                marker: { color: theme.neg, symbol: "triangle-down", size: 12 },
              },
              // Option buys/sells: hollow triangles to distinguish
              {
                type: "scatter", mode: "markers", name: "Buy (option)", yaxis: "y",
                x: buyOpt.map((t: any) => t.trade_date),
                y: buyOpt.map((t: any) => t.price),
                marker: {
                  symbol: "triangle-up-open", size: 14,
                  line: { color: theme.pos, width: 2 }, color: theme.pos,
                },
              },
              {
                type: "scatter", mode: "markers", name: "Sell (option)", yaxis: "y",
                x: sellOpt.map((t: any) => t.trade_date),
                y: sellOpt.map((t: any) => t.price),
                marker: {
                  symbol: "triangle-down-open", size: 14,
                  line: { color: theme.neg, width: 2 }, color: theme.neg,
                },
              },
              {
                type: "bar", name: "Volume", yaxis: "y2",
                x: rows.map((r: any) => r.trade_date),
                y: rows.map((r: any) => r.volume),
                marker: { color: theme.xaxis_gridcolor },
              },
            ]}
            layout={{
              paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
              font: theme.font, height: 540,
              margin: { t: 10, r: 10, b: 40, l: 60 },
              xaxis: { gridcolor: theme.xaxis_gridcolor, rangeslider: { visible: false } },
              yaxis: { domain: [0.25, 1], gridcolor: theme.yaxis_gridcolor, title: "Price" },
              yaxis2: { domain: [0, 0.2], gridcolor: theme.yaxis_gridcolor, title: "Volume" },
              showlegend: true,
              legend: { orientation: "h", y: -0.15 },
            }}
            style={{ width: "100%" }} useResizeHandler
          />
        </CollapsibleCard>
      )}

      {symbol && (
        <CollapsibleCard title={t("research.financials")}>
          <div className="filters">
            <button className={finPeriod === "quarterly" ? "active" : ""}
                    onClick={() => setFinPeriod("quarterly")}>{t("research.quarterly")}</button>
            <button className={finPeriod === "annual" ? "active" : ""}
                    onClick={() => setFinPeriod("annual")}>{t("research.annual")}</button>
          </div>
          <div className="checkbox-row">
            {Object.keys(finMetrics).map((k) =>
              <label key={k}>
                <input type="checkbox" checked={finMetrics[k]}
                       onChange={(e) => setFinMetrics({ ...finMetrics, [k]: e.target.checked })} />
                {k}
              </label>
            )}
          </div>
          <Plot
            data={Object.entries(finMetrics).filter(([, v]) => v).map(([k]) => ({
              type: "bar", name: k,
              x: (finQ.data?.rows ?? []).map((r: any) => r.period_end),
              y: (finQ.data?.rows ?? []).map((r: any) => r[k]),
            }))}
            layout={{
              paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
              font: theme.font, height: 320, barmode: "group",
              margin: { t: 10, r: 10, b: 40, l: 60 },
              xaxis: { gridcolor: theme.xaxis_gridcolor },
              yaxis: { gridcolor: theme.yaxis_gridcolor },
            }}
            style={{ width: "100%" }} useResizeHandler
          />
        </CollapsibleCard>
      )}

      {symbol && (
        <CollapsibleCard title={interpolate(t("research.trade_history"), { symbol })}>
          <div className="research-table-wrap">
            <table className="research-table">
              <thead>
                <tr>
                  <th onClick={() => toggleTradeSort("trade_date")}>Date{tradeArrow("trade_date")}</th>
                  <th onClick={() => toggleTradeSort("txn_type")}>Type{tradeArrow("txn_type")}</th>
                  <th className="num" onClick={() => toggleTradeSort("quantity")}>Qty{tradeArrow("quantity")}</th>
                  <th className="num" onClick={() => toggleTradeSort("price")}>Price{tradeArrow("price")}</th>
                  <th className="num" onClick={() => toggleTradeSort("net_amount")}>Amount{tradeArrow("net_amount")}</th>
                  <th onClick={() => toggleTradeSort("currency")}>Ccy{tradeArrow("currency")}</th>
                  <th onClick={() => toggleTradeSort("account")}>Account{tradeArrow("account")}</th>
                  <th onClick={() => toggleTradeSort("description")}>Description{tradeArrow("description")}</th>
                </tr>
              </thead>
              <tbody>
                {sortedTrades.map((t: any, i: number) => (
                  <tr key={i}>
                    <td>{t.trade_date}</td>
                    <td>{t.txn_type}</td>
                    <td className="num">{fmtNum(t.quantity, 0)}</td>
                    <td className="num">{fmtNum(t.price)}</td>
                    <td className={"num " + ((t.net_amount ?? 0) < 0 ? "neg" : "pos")}>{fmtNum(t.net_amount)}</td>
                    <td>{t.currency}</td>
                    <td>{t.institution_code} • {t.account_number}</td>
                    <td style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis" }}>{t.description}</td>
                  </tr>
                ))}
                {sortedTrades.length === 0 && (
                  <tr><td colSpan={8} className="muted">{t("research.no_trades")}</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </CollapsibleCard>
      )}
    </>
  );
}
