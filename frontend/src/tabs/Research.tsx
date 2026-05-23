import { useQuery } from "@tanstack/react-query";
import { useParams, useNavigate } from "react-router-dom";
import { useMemo, useState } from "react";
import Plot from "react-plotly.js";
import { api } from "../api";
import { plotlyTheme } from "../theme";
import { useI18n } from "../i18n";

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

  const ma = (n: number) => rows.map((_: any, i: number) => {
    if (i < n - 1) return null;
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) s += rows[j].close;
    return s / n;
  });

  const allTrades = tradesQ.data?.rows ?? [];
  const trades = useMemo(() => {
    if (!cutoff) return allTrades;
    return allTrades.filter((t: any) => t.trade_date >= cutoff);
  }, [allTrades, cutoff]);

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
      <h2>{t("nav.research")} {symbol && <>— {symbol}</>}</h2>
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
              {filteredSymbols.length === 0 && <div className="ticker-search-empty">No matching tickers.</div>}
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
        <p className="muted">Enter a symbol and press Enter to load prices, trades, and financials.</p>
      )}

      {symbol && (
        <div className="card">
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
        </div>
      )}

      {symbol && (
        <div className="card">
          <div className="filters">
            <h3 style={{ marginRight: 12 }}>Financials</h3>
            <button className={finPeriod === "quarterly" ? "active" : ""}
                    onClick={() => setFinPeriod("quarterly")}>Quarterly</button>
            <button className={finPeriod === "annual" ? "active" : ""}
                    onClick={() => setFinPeriod("annual")}>Annual</button>
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
        </div>
      )}

      {symbol && (
        <div className="card">
          <h3>Trade history for {symbol}</h3>
          <div style={{ overflow: "auto", maxHeight: 320 }}>
            <table>
              <thead>
                <tr>
                  <th>Date</th><th>Type</th>
                  <th className="num">Qty</th><th className="num">Price</th>
                  <th className="num">Amount</th><th>Ccy</th>
                  <th>Account</th><th>Description</th>
                </tr>
              </thead>
              <tbody>
                {allTrades.map((t: any, i: number) => (
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
                {allTrades.length === 0 && (
                  <tr><td colSpan={8} className="muted">No trades recorded for this symbol.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
