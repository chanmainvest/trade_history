import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, HoldingRow } from "../api";

function fmtNum(n: number | null | undefined, dec = 2) {
  if (n === null || n === undefined) return "";
  return n.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

export default function Monthly() {
  const today = new Date();
  const lastMonthEnd = new Date(today.getFullYear(), today.getMonth(), 0).toISOString().slice(0, 10);
  const [a, setA] = useState(lastMonthEnd);
  const [b, setB] = useState(lastMonthEnd);

  const snapQ = useQuery({
    queryKey: ["snap", b],
    queryFn: () => api.monthlySnapshot(b),
  });
  const diffQ = useQuery({
    queryKey: ["diff", a, b],
    queryFn: () => api.monthlyDiff(a, b),
    enabled: a !== b,
  });

  const totalsByCurrency: Record<string, number> = {};
  for (const r of snapQ.data?.rows ?? []) {
    const v = r.market_value || 0;
    totalsByCurrency[r.currency] = (totalsByCurrency[r.currency] || 0) + v;
  }

  return (
    <>
      <h2>Monthly snapshot</h2>
      <div className="filters">
        <label>A (earlier): <input type="date" value={a} onChange={(e) => setA(e.target.value)} /></label>
        <label>B (later): <input type="date" value={b} onChange={(e) => setB(e.target.value)} /></label>
      </div>

      <div className="row">
        <div className="col card">
          <h3>Holdings as of {b}</h3>
          <div style={{ marginBottom: 8, color: "var(--fg-dim)" }}>
            {Object.entries(totalsByCurrency).map(([c, v]) =>
              <span key={c} style={{ marginRight: 12 }}>
                <strong>{c}</strong> {fmtNum(v)}
              </span>
            )}
          </div>
          <div style={{ overflow: "auto", maxHeight: 480 }}>
            <table>
              <thead>
                <tr><th>Inst</th><th>Acct</th><th>Symbol</th><th>Type</th>
                  <th className="num">Qty</th><th className="num">Price</th>
                  <th className="num">Mkt Value</th><th className="num">Unrealized</th><th>Ccy</th></tr>
              </thead>
              <tbody>
                {snapQ.data?.rows.map((r: HoldingRow, i: number) => (
                  <tr key={i}>
                    <td>{r.institution_code}</td>
                    <td>{r.account_number}</td>
                    <td>{r.symbol}</td>
                    <td>{r.asset_type}{r.option_type ? ` ${r.option_type} ${fmtNum(r.option_strike, 2)} ${r.option_expiry || ""}` : ""}</td>
                    <td className="num">{fmtNum(r.quantity, 0)}</td>
                    <td className="num">{fmtNum(r.market_price)}</td>
                    <td className="num">{fmtNum(r.market_value)}</td>
                    <td className={"num " + ((r.unrealized_pnl ?? 0) < 0 ? "neg" : "pos")}>{fmtNum(r.unrealized_pnl)}</td>
                    <td>{r.currency}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="col card">
          <h3>Diff: {a} → {b}</h3>
          {a === b ? <p>Pick two different dates to compare.</p> : (
            <div style={{ overflow: "auto", maxHeight: 480 }}>
              <table>
                <thead>
                  <tr><th>Symbol</th><th>Type</th>
                    <th className="num">Qty A</th><th className="num">Qty B</th><th className="num">Δ</th></tr>
                </thead>
                <tbody>
                  {diffQ.data?.rows.map((d, i) => (
                    <tr key={i}>
                      <td>{d.symbol}</td>
                      <td>{d.asset_type}{d.option_type ? ` ${d.option_type} ${fmtNum(d.option_strike, 2)} ${d.option_expiry || ""}` : ""}</td>
                      <td className="num">{fmtNum(d.qty_a, 0)}</td>
                      <td className="num">{fmtNum(d.qty_b, 0)}</td>
                      <td className={"num " + (d.qty_delta < 0 ? "neg" : "pos")}>{fmtNum(d.qty_delta, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
