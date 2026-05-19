import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import Plot from "react-plotly.js";
import { api } from "../api";
import { usePortfolio } from "../portfolio";
import { SmartSelect } from "../SmartSelect";
import { plotlyTheme } from "../theme";
import { useI18n } from "../i18n";

type Period = "1m" | "3m" | "6m" | "1y" | "3y" | "5y" | "10y" | "max" | "custom";
const PERIODS: Period[] = ["1m", "3m", "6m", "1y", "3y", "5y", "10y", "max", "custom"];

function isoDaysAgo(days: number) {
  const d = new Date(); d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}
function periodStart(p: Period): string {
  switch (p) {
    case "1m": return isoDaysAgo(30);
    case "3m": return isoDaysAgo(90);
    case "6m": return isoDaysAgo(180);
    case "1y": return isoDaysAgo(365);
    case "3y": return isoDaysAgo(365 * 3);
    case "5y": return isoDaysAgo(365 * 5);
    case "10y": return isoDaysAgo(365 * 10);
    default: return "";
  }
}

export default function Performance() {
  const { activeAccountIds, config } = usePortfolio();
  const { t } = useI18n();
  const hideMoney = !!config?.hide_money;

  const [period, setPeriod] = useState<Period>("1y");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [institutions, setInstitutions] = useState<string[]>([]);
  const [accountIds, setAccountIds] = useState<string[]>([]);
  const [showCcy, setShowCcy] = useState<"both" | "CAD" | "USD">("both");
  const [normalize, setNormalize] = useState(false);  // show % rebased to start

  const totalQ = useQuery({
    queryKey: ["perfTotal", institutions, accountIds, activeAccountIds],
    queryFn: () => api.perfTotal({
      institution: institutions,
      account_id: accountIds.length > 0
        ? accountIds
        : activeAccountIds.length > 0 ? activeAccountIds.map(String) : undefined,
    }),
  });
  const cashQ = useQuery({
    queryKey: ["perfCash", institutions, accountIds, activeAccountIds],
    queryFn: () => api.perfCash({
      institution: institutions,
      account_id: accountIds.length > 0
        ? accountIds
        : activeAccountIds.length > 0 ? activeAccountIds.map(String) : undefined,
    }),
  });
  const acctsQ = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });

  const instOpts = useMemo(() => Array.from(new Set(
    (acctsQ.data?.rows ?? []).map((a) => a.institution_code)
  )).sort().map((c) => ({ value: c, label: c })),
  [acctsQ.data]);
  const acctOpts = useMemo(() =>
    (acctsQ.data?.rows ?? []).map((a) => ({
      value: String(a.account_id),
      label: `${a.institution_code} • ${a.account_number}`,
      hint: a.base_currency,
    })),
  [acctsQ.data]);

  const start = period === "custom" ? customStart : periodStart(period);
  const end = period === "custom" ? customEnd : "";

  const totalRows = totalQ.data?.rows ?? [];
  const cashRows = cashQ.data?.rows ?? [];

  const filteredTotal = useMemo(() => {
    return totalRows.filter((r) => {
      if (start && r.as_of_date < start) return false;
      if (end && r.as_of_date > end) return false;
      if (showCcy !== "both" && r.currency !== showCcy) return false;
      return true;
    });
  }, [totalRows, start, end, showCcy]);

  const seriesByCcy = useMemo(() => {
    const m = new Map<string, { x: string[]; y: number[] }>();
    for (const r of filteredTotal) {
      const s = m.get(r.currency) || { x: [], y: [] };
      s.x.push(r.as_of_date);
      s.y.push(r.market_value);
      m.set(r.currency, s);
    }
    return m;
  }, [filteredTotal]);

  const theme = plotlyTheme();

  function rebase(y: number[]): number[] {
    if (y.length === 0) return y;
    const base = y[0] || 1;
    return y.map((v) => (v / base - 1) * 100);
  }

  return (
    <>
      <h2>{t("nav.performance")}</h2>
      <div className="filters">
        {PERIODS.map((p) => (
          <button key={p} className={p === period ? "active" : ""}
                  onClick={() => setPeriod(p)}>{t(`period.${p}`)}</button>
        ))}
        {period === "custom" && (
          <>
            <input type="date" value={customStart} onChange={(e) => setCustomStart(e.target.value)} />
            <input type="date" value={customEnd} onChange={(e) => setCustomEnd(e.target.value)} />
          </>
        )}
        <SmartSelect label={t("f.institution")} options={instOpts} value={institutions} onChange={setInstitutions} />
        <SmartSelect label={t("f.account")} options={acctOpts} value={accountIds} onChange={setAccountIds} />
        <label>{t("cfg.display_currency")}:&nbsp;
          <select value={showCcy} onChange={(e) => setShowCcy(e.target.value as any)}>
            <option value="both">Both</option>
            <option value="CAD">CAD</option>
            <option value="USD">USD</option>
          </select>
        </label>
        <label>
          <input type="checkbox" checked={normalize || hideMoney}
                 disabled={hideMoney}
                 onChange={(e) => setNormalize(e.target.checked)} />
          &nbsp;Show as % from start{hideMoney ? " (forced by config)" : ""}
        </label>
      </div>

      <div className="card">
        <h3>Total market value{(normalize || hideMoney) ? " — % change" : ""}</h3>
        <Plot
          data={Array.from(seriesByCcy.entries()).map(([ccy, s]) => ({
            type: "scatter", mode: "lines", name: ccy,
            x: s.x,
            y: (normalize || hideMoney) ? rebase(s.y) : s.y,
            line: { width: 2 },
          }))}
          layout={{
            paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
            font: theme.font, margin: { t: 10, r: 10, b: 40, l: 60 },
            xaxis: { gridcolor: theme.xaxis_gridcolor, title: "Date" },
            yaxis: {
              gridcolor: theme.yaxis_gridcolor,
              title: (normalize || hideMoney) ? "% change" : "Market value",
              ticksuffix: (normalize || hideMoney) ? "%" : "",
            },
            height: 380,
            hovermode: "x unified",
          }}
          style={{ width: "100%" }}
          useResizeHandler
        />
        <p className="muted" style={{ marginBottom: 0 }}>
          Snapshots are forward-filled from each account's last statement to
          remove the saw-tooth that arose from accounts publishing on
          different calendars.
        </p>
      </div>

      {!hideMoney && (
        <div className="card">
          <h3>Cash by currency</h3>
          <Plot
            data={[
              {
                type: "scatter", mode: "lines", name: "CAD",
                x: cashRows.filter((r) => r.currency === "CAD").map((r) => r.as_of_date),
                y: cashRows.filter((r) => r.currency === "CAD").map((r) => r.closing_balance),
              },
              {
                type: "scatter", mode: "lines", name: "USD",
                x: cashRows.filter((r) => r.currency === "USD").map((r) => r.as_of_date),
                y: cashRows.filter((r) => r.currency === "USD").map((r) => r.closing_balance),
              },
            ]}
            layout={{
              paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
              font: theme.font, margin: { t: 10, r: 10, b: 40, l: 60 },
              xaxis: { gridcolor: theme.xaxis_gridcolor },
              yaxis: { gridcolor: theme.yaxis_gridcolor },
              height: 320,
            }}
            style={{ width: "100%" }}
            useResizeHandler
          />
        </div>
      )}
    </>
  );
}
