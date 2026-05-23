import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import Plot from "react-plotly.js";
import { api } from "../api";
import { usePortfolio } from "../portfolio";
import { SmartSelect } from "../SmartSelect";
import { plotlyTheme } from "../theme";
import { useI18n } from "../i18n";

type Period = "1d" | "1w" | "1m" | "3m" | "6m" | "1y" | "3y" | "5y" | "10y" | "max" | "custom";
const PERIODS: Period[] = ["1d", "1w", "1m", "3m", "6m", "1y", "3y", "5y", "10y", "max", "custom"];

function isoDaysAgo(days: number) {
  const d = new Date(); d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}
function todayISO() { return new Date().toISOString().slice(0, 10); }
function periodStart(p: Period): string {
  switch (p) {
    case "1d": return isoDaysAgo(1);
    case "1w": return isoDaysAgo(7);
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
  const chartEnd = end || todayISO();
  const chartStart = start || totalRows[0]?.as_of_date || cashRows[0]?.as_of_date || chartEnd;

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

  const filteredCash = useMemo(() => {
    return cashRows.filter((r) => {
      if (start && r.as_of_date < start) return false;
      if (end && r.as_of_date > end) return false;
      if (showCcy !== "both" && r.currency !== showCcy) return false;
      return true;
    });
  }, [cashRows, start, end, showCcy]);

  const cashSeriesByCcy = useMemo(() => {
    const byKey = new Map<string, number>();
    for (const row of filteredCash) {
      const key = `${row.currency}::${row.as_of_date}`;
      byKey.set(key, (byKey.get(key) || 0) + (row.closing_balance || 0));
    }
    const out = new Map<string, { x: string[]; y: number[] }>();
    for (const [key, value] of Array.from(byKey.entries()).sort()) {
      const [currency, asOfDate] = key.split("::");
      const series = out.get(currency) || { x: [], y: [] };
      series.x.push(asOfDate);
      series.y.push(value);
      out.set(currency, series);
    }
    return out;
  }, [filteredCash]);

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
        <span className="segmented-control" role="group" aria-label="Currency filter">
          {(["both", "CAD", "USD"] as const).map((ccy) => (
            <button key={ccy} className={showCcy === ccy ? "active" : ""}
                    onClick={() => setShowCcy(ccy)}>
              {ccy === "both" ? "Both" : ccy}
            </button>
          ))}
        </span>
        <label>
          <input type="checkbox" checked={normalize || hideMoney}
                 disabled={hideMoney}
                 onChange={(e) => setNormalize(e.target.checked)} />
          &nbsp;Show as % from start{hideMoney ? " (forced by config)" : ""}
        </label>
      </div>

      <div className="card">
        <h3>Total portfolio value{(normalize || hideMoney) ? " — % change" : ""}</h3>
        <Plot
          data={Array.from(seriesByCcy.entries()).map(([ccy, s]) => ({
            type: "scatter", name: ccy,
            x: s.x,
            y: (normalize || hideMoney) ? rebase(s.y) : s.y,
            mode: s.x.length <= 2 ? "lines+markers" : "lines",
            line: { width: 2 },
          }))}
          layout={{
            paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
            font: theme.font, margin: { t: 10, r: 10, b: 40, l: 60 },
            xaxis: { gridcolor: theme.xaxis_gridcolor, title: "Date", range: [chartStart, chartEnd] },
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
          Securities and cash are forward-filled from each account's latest
          statement checkpoints to remove the saw-tooth that arose from
          accounts publishing on different calendars.
        </p>
      </div>

      {!hideMoney && (
        <div className="card">
          <h3>Cash by currency</h3>
          <Plot
            data={Array.from(cashSeriesByCcy.entries()).map(([ccy, s]) => ({
              type: "scatter", mode: s.x.length <= 2 ? "lines+markers" : "lines", name: ccy,
              x: s.x, y: s.y,
            }))}
            layout={{
              paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
              font: theme.font, margin: { t: 10, r: 10, b: 40, l: 60 },
              xaxis: { gridcolor: theme.xaxis_gridcolor, range: [chartStart, chartEnd] },
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
