import { useQuery } from "@tanstack/react-query";
import { useParams, useNavigate } from "react-router-dom";
import { useState } from "react";
import Plot from "react-plotly.js";
import { api } from "../api";

type Freq = "D" | "W" | "M";

export default function Research() {
  const params = useParams<{ symbol: string }>();
  const nav = useNavigate();
  const symbol = (params.symbol || "").toUpperCase();
  const [input, setInput] = useState(symbol);
  const [freq, setFreq] = useState<Freq>("D");
  const [showMA50, setShowMA50] = useState(true);
  const [showMA200, setShowMA200] = useState(true);
  const [period, setPeriod] = useState<"quarterly" | "annual">("quarterly");
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
    queryKey: ["fin", symbol, period],
    queryFn: () => api.financials(symbol, period),
    enabled: !!symbol,
  });

  const rows = pricesQ.data?.rows ?? [];
  const ma = (n: number) => rows.map((_: any, i: number) => {
    if (i < n - 1) return null;
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) s += rows[j].close;
    return s / n;
  });

  const buyTrades = (tradesQ.data?.rows ?? []).filter((t: any) => t.txn_type.startsWith("buy") || t.txn_type === "option_buy_to_open");
  const sellTrades = (tradesQ.data?.rows ?? []).filter((t: any) => t.txn_type.startsWith("sell") || t.txn_type === "option_sell_to_open");

  return (
    <>
      <h2>Stock research {symbol && <>— {symbol}</>}</h2>
      <div className="filters">
        <input value={input} onChange={(e) => setInput(e.target.value.toUpperCase())}
               placeholder="Symbol (e.g. AAPL)"
               onKeyDown={(e) => { if (e.key === "Enter") nav(`/research/${input}`); }} />
        <button onClick={() => nav(`/research/${input}`)}>Go</button>
        {(["D", "W", "M"] as Freq[]).map((f) =>
          <button key={f} className={f === freq ? "active" : ""} onClick={() => setFreq(f)}>{f}</button>
        )}
        <label><input type="checkbox" checked={showMA50} onChange={(e) => setShowMA50(e.target.checked)} /> MA50</label>
        <label><input type="checkbox" checked={showMA200} onChange={(e) => setShowMA200(e.target.checked)} /> MA200</label>
      </div>

      {!symbol && <p style={{ color: "var(--fg-dim)" }}>Enter a symbol to load prices, trades, and financials.</p>}

      {symbol && (
        <div className="card">
          <Plot
            data={[
              {
                type: "candlestick",
                x: rows.map((r: any) => r.trade_date),
                open: rows.map((r: any) => r.open),
                high: rows.map((r: any) => r.high),
                low: rows.map((r: any) => r.low),
                close: rows.map((r: any) => r.close),
                name: symbol, yaxis: "y",
                increasing: { line: { color: "#2bbf73" } },
                decreasing: { line: { color: "#e35d6a" } },
              },
              ...(showMA50 ? [{
                type: "scatter", mode: "lines", name: "MA50",
                x: rows.map((r: any) => r.trade_date), y: ma(50), yaxis: "y",
                line: { color: "#f0a020", width: 1 },
              }] : []),
              ...(showMA200 ? [{
                type: "scatter", mode: "lines", name: "MA200",
                x: rows.map((r: any) => r.trade_date), y: ma(200), yaxis: "y",
                line: { color: "#9966ff", width: 1 },
              }] : []),
              {
                type: "scatter", mode: "markers", name: "Buys",
                x: buyTrades.map((t: any) => t.trade_date),
                y: buyTrades.map((t: any) => t.price),
                marker: { color: "#2bbf73", symbol: "triangle-up", size: 10 }, yaxis: "y",
              },
              {
                type: "scatter", mode: "markers", name: "Sells",
                x: sellTrades.map((t: any) => t.trade_date),
                y: sellTrades.map((t: any) => t.price),
                marker: { color: "#e35d6a", symbol: "triangle-down", size: 10 }, yaxis: "y",
              },
              {
                type: "bar", name: "Volume",
                x: rows.map((r: any) => r.trade_date),
                y: rows.map((r: any) => r.volume),
                marker: { color: "#3a3f4a" }, yaxis: "y2",
              },
            ]}
            layout={{
              paper_bgcolor: "#161a22", plot_bgcolor: "#161a22",
              font: { color: "#d6d8dc" }, height: 520,
              margin: { t: 10, r: 10, b: 40, l: 60 },
              xaxis: { gridcolor: "#2a2f3a", rangeslider: { visible: false } },
              yaxis: { domain: [0.25, 1], gridcolor: "#2a2f3a", title: "Price" },
              yaxis2: { domain: [0, 0.2], gridcolor: "#2a2f3a", title: "Volume" },
              showlegend: true, legend: { orientation: "h" },
            }}
            style={{ width: "100%" }} useResizeHandler
          />
        </div>
      )}

      {symbol && (
        <div className="card">
          <div className="filters">
            <h3 style={{ marginRight: 12 }}>Financials</h3>
            <button className={period === "quarterly" ? "active" : ""} onClick={() => setPeriod("quarterly")}>Quarterly</button>
            <button className={period === "annual" ? "active" : ""} onClick={() => setPeriod("annual")}>Annual</button>
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
              paper_bgcolor: "#161a22", plot_bgcolor: "#161a22",
              font: { color: "#d6d8dc" }, height: 320, barmode: "group",
              margin: { t: 10, r: 10, b: 40, l: 60 },
              xaxis: { gridcolor: "#2a2f3a" }, yaxis: { gridcolor: "#2a2f3a" },
            }}
            style={{ width: "100%" }} useResizeHandler
          />
        </div>
      )}
    </>
  );
}
