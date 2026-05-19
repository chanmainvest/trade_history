import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import Plot from "react-plotly.js";
import { api } from "../api";
import { usePortfolio } from "../portfolio";
import { SmartSelect } from "../SmartSelect";
import { plotlyTheme } from "../theme";
import { useI18n } from "../i18n";

type View = "rrg" | "treemap" | "correlation";

function todayISO() { return new Date().toISOString().slice(0, 10); }
function isoMinusYears(years: number) {
  const d = new Date(); d.setFullYear(d.getFullYear() - years);
  return d.toISOString().slice(0, 10);
}

// Color scale matching portfolio_dashboard/build_portfolio_report.py::_corr_color
const CORR_COLORSCALE: [number, string][] = [
  [0.0,  "rgb(30,100,200)"],   // strong negative — deep blue
  [0.25, "rgb(50,200,180)"],   // moderate negative — teal
  [0.5,  "rgb(200,180,50)"],   // ~zero — warm/neutral
  [0.7,  "rgb(225,120,50)"],
  [1.0,  "rgb(200,50,50)"],    // strong positive — red
];

const SECTOR_COLORS: Record<string, string> = {
  "Technology": "#3A7BD5",
  "Healthcare": "#2BBF73",
  "Financial Services": "#8B5CF6",
  "Consumer Cyclical": "#F58518",
  "Consumer Defensive": "#6AA84F",
  "Communication Services": "#D65DB1",
  "Industrials": "#00A6A6",
  "Energy": "#C98C18",
  "Utilities": "#4B9CD3",
  "Real Estate": "#A97155",
  "Basic Materials": "#7A9E3D",
  "ETF": "#64748B",
  "Unknown": "#64748B",
};

function shade(hex: string, amount: number) {
  const n = parseInt(hex.slice(1), 16);
  const r = Math.max(0, Math.min(255, (n >> 16) + amount));
  const g = Math.max(0, Math.min(255, ((n >> 8) & 255) + amount));
  const b = Math.max(0, Math.min(255, (n & 255) + amount));
  return `#${[r, g, b].map((x) => x.toString(16).padStart(2, "0")).join("")}`;
}

function sectorColor(symbol: string, sector: string | null | undefined, symbols: string[]) {
  const group = sector || "Unknown";
  const base = SECTOR_COLORS[group] || SECTOR_COLORS.Unknown;
  const peers = symbols.filter((s) => s !== symbol).length;
  const idx = symbols.indexOf(symbol);
  if (idx < 0 || peers <= 0) return base;
  const offset = -22 + (44 * idx) / Math.max(1, peers);
  return shade(base, Math.round(offset));
}

export default function Viz() {
  const { activeAccountIds, activePortfolio, accounts } = usePortfolio();
  const { t } = useI18n();
  const [view, setView] = useState<View>("rrg");
  const [benchmark, setBenchmark] = useState("SPY");
  const [windowDays, setWindowDays] = useState(60);
  const [institutions, setInstitutions] = useState<string[]>([]);
  const [accountIds, setAccountIds] = useState<string[]>([]);

  const latestQ = useQuery({ queryKey: ["latest-date"], queryFn: api.latestDate });
  const latest = latestQ.data?.latest || todayISO();
  const [monthEnd, setMonthEnd] = useState<string>("");

  const effectiveMonthEnd = monthEnd || latest;

  const [corrStart, setCorrStart] = useState(isoMinusYears(1));
  const [corrEnd, setCorrEnd] = useState(todayISO());

  const instOpts = useMemo(() => Array.from(new Set(accounts.map((a) => a.institution_code)))
    .sort().map((c) => ({ value: c, label: c })), [accounts]);
  const acctOpts = useMemo(() => accounts.map((a) => ({
    value: String(a.account_id),
    label: `${a.institution_code} • ${a.account_number}`,
    hint: a.base_currency,
  })), [accounts]);

  const acctFilter = useMemo(() => {
    if (accountIds.length > 0) return accountIds.map((x) => parseInt(x, 10));
    let ids = activeAccountIds.length > 0
      ? activeAccountIds
      : accounts.map((a) => a.account_id);
    if (institutions.length > 0) {
      const allowed = new Set(institutions);
      ids = ids.filter((id) => allowed.has(accounts.find((a) => a.account_id === id)?.institution_code || ""));
    }
    if (ids.length === 0) return undefined;
    if (activeAccountIds.length === 0 && institutions.length === 0 && ids.length === accounts.length) return undefined;
    return ids;
  }, [accountIds, activeAccountIds, accounts, institutions]);

  const sectorQ = useQuery({
    queryKey: ["sector", effectiveMonthEnd, acctFilter],
    queryFn: () => api.vizSector({ month_end: effectiveMonthEnd, account_id: acctFilter }),
    enabled: view === "treemap",
  });
  const corrQ = useQuery({
    queryKey: ["corr", corrStart, corrEnd, acctFilter],
    queryFn: () => api.vizCorrelation({ start: corrStart, end: corrEnd, account_id: acctFilter }),
    enabled: view === "correlation",
  });
  const rrgQ = useQuery({
    queryKey: ["rrg", benchmark, windowDays, acctFilter],
    queryFn: () => api.vizRRG({ benchmark, window_days: windowDays, account_id: acctFilter }),
    enabled: view === "rrg",
  });

  return (
    <>
      <h2>{t("nav.viz")} <span className="tag">{activePortfolio?.name}</span></h2>
      <div className="filters">
        {(["rrg", "treemap", "correlation"] as View[]).map((v) =>
          <button key={v} className={v === view ? "active" : ""} onClick={() => setView(v)}>{t(`viz.${v}`)}</button>
        )}
        <SmartSelect label={t("f.institution")} options={instOpts} value={institutions} onChange={setInstitutions} />
        <SmartSelect label={t("f.account")} options={acctOpts} value={accountIds} onChange={setAccountIds} />
      </div>

      {view === "rrg" && (
        <RRG benchmark={benchmark} setBenchmark={setBenchmark}
             windowDays={windowDays} setWindowDays={setWindowDays}
             frames={rrgQ.data?.frames ?? []} />
      )}

      {view === "treemap" && (
        <Treemap monthEnd={effectiveMonthEnd}
                 setMonthEnd={setMonthEnd}
                 actualDate={sectorQ.data?.as_of_date}
                 rows={sectorQ.data?.rows ?? []}
                 loading={sectorQ.isLoading} />
      )}

      {view === "correlation" && (
        <CorrelationView
          start={corrStart} end={corrEnd}
          setStart={setCorrStart} setEnd={setCorrEnd}
          symbols={corrQ.data?.symbols ?? []}
          matrix={corrQ.data?.matrix ?? []}
          profiles={corrQ.data?.profiles ?? {}}
        />
      )}
    </>
  );
}

function Treemap({ monthEnd, setMonthEnd, actualDate, rows, loading }: {
  monthEnd: string;
  setMonthEnd: (s: string) => void;
  actualDate: string | null | undefined;
  rows: { symbol: string; asset_type: string; market_value: number; sector?: string | null }[];
  loading: boolean;
}) {
  const theme = plotlyTheme();
  const { labels, parents, values, colors } = useMemo(() => {
    const labels: string[] = [];
    const parents: string[] = [];
    const values: number[] = [];
    const colors: string[] = [];
    const groupTotals: Record<string, number> = {};
    const symbolsByGroup: Record<string, string[]> = {};
    for (const r of rows) {
      const group = r.sector || r.asset_type;
      groupTotals[group] = (groupTotals[group] || 0) + (r.market_value || 0);
      symbolsByGroup[group] = [...(symbolsByGroup[group] || []), r.symbol].sort();
    }
    for (const g of Object.keys(groupTotals)) {
      labels.push(g); parents.push(""); values.push(groupTotals[g]);
      colors.push(SECTOR_COLORS[g] || SECTOR_COLORS.Unknown);
    }
    for (const r of rows) {
      const group = r.sector || r.asset_type;
      labels.push(r.symbol);
      parents.push(group);
      values.push(r.market_value || 0);
      colors.push(sectorColor(r.symbol, group, symbolsByGroup[group] || [r.symbol]));
    }
    return { labels, parents, values, colors };
  }, [rows]);

  return (
    <div className="card">
      <div className="filters">
        <h3 style={{ marginRight: 12 }}>Holdings treemap</h3>
        <label>As of:&nbsp;
          <input type="date" value={monthEnd} onChange={(e) => setMonthEnd(e.target.value)} />
        </label>
        {actualDate && actualDate !== monthEnd && (
          <span className="muted">(snapshot from {actualDate})</span>
        )}
      </div>
      {loading && <p className="muted">Loading…</p>}
      {!loading && rows.length === 0 && (
        <p className="muted">
          No holdings to display for this date.{" "}
          Try picking a date after your earliest statement.
        </p>
      )}
      {rows.length > 0 && (
        <Plot
          data={[{
            type: "treemap", labels, parents, values,
            branchvalues: "total",
            textinfo: "label+value+percent parent",
            marker: {
              colors, showscale: false,
              line: { width: 1, color: theme.paper_bgcolor },
            },
            hovertemplate: "<b>%{label}</b><br>%{value:$,.0f}<br>%{percentParent:.1%} of %{parent}<extra></extra>",
          }]}
          layout={{
            paper_bgcolor: theme.paper_bgcolor, font: theme.font,
            height: 620, margin: { t: 10, r: 10, b: 10, l: 10 },
          }}
          style={{ width: "100%" }} useResizeHandler
        />
      )}
    </div>
  );
}

function CorrelationView({ start, end, setStart, setEnd, symbols, matrix, profiles }: {
  start: string; end: string;
  setStart: (s: string) => void; setEnd: (s: string) => void;
  symbols: string[]; matrix: number[][];
  profiles: Record<string, { sector?: string | null; industry?: string | null }>;
}) {
  const theme = plotlyTheme();
  const [sortBy, setSortBy] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  function toggleSym(sym: string) {
    const next = new Set(hidden);
    if (next.has(sym)) next.delete(sym); else next.add(sym);
    setHidden(next);
  }

  const order = useMemo(() => {
    const visible = symbols.map((_, i) => i).filter((i) => !hidden.has(symbols[i]));
    if (!sortBy || !symbols.includes(sortBy)) return visible;
    const idx = symbols.indexOf(sortBy);
    if (idx < 0) return visible;
    // Sort symbols by their correlation with the chosen one, descending
    return visible
      .sort((a, b) => (matrix[b]?.[idx] ?? 0) - (matrix[a]?.[idx] ?? 0));
  }, [sortBy, symbols, matrix, hidden]);

  const sortedSymbols = order.map((i) => symbols[i]);
  const sortedMatrix = order.map((i) => order.map((j) => matrix[i]?.[j] ?? 0));

  return (
    <div className="card">
      <div className="filters">
        <h3 style={{ marginRight: 12 }}>Correlation matrix</h3>
        <label>Start:&nbsp;<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label>End:&nbsp;<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <label>Sort by:&nbsp;
          <select value={sortBy || ""} onChange={(e) => setSortBy(e.target.value || null)}>
            <option value="">(alphabetical)</option>
            {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <span className="muted">{symbols.length} symbols</span>
      </div>
      {symbols.length > 0 && (
        <div className="checkbox-row">
          {symbols.map((s) => (
            <label key={s} style={{ borderBottom: `2px solid ${sectorColor(s, profiles[s]?.sector, symbols)}` }}>
              <input type="checkbox" checked={!hidden.has(s)} onChange={() => toggleSym(s)} />
              {s}
            </label>
          ))}
        </div>
      )}
      {symbols.length === 0 ? (
        <p className="muted">No correlation data for this range.</p>
      ) : sortedSymbols.length === 0 ? (
        <p className="muted">All symbols are hidden.</p>
      ) : (
        <Plot
          data={[{
            type: "heatmap",
            z: sortedMatrix, x: sortedSymbols, y: sortedSymbols,
            colorscale: CORR_COLORSCALE, zmin: -1, zmax: 1,
            hovertemplate: "%{y} vs %{x}<br>ρ = %{z:.2f}<extra></extra>",
          }]}
          layout={{
            paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
            font: theme.font,
            // Square chart: tie height to a per-symbol pixel size so each cell
            // is roughly square regardless of how many symbols are present.
            height: Math.max(420, Math.min(1100, 22 * symbols.length + 140)),
            width: Math.max(420, Math.min(1100, 22 * symbols.length + 140)),
            margin: { t: 10, r: 10, b: 100, l: 100 },
            xaxis: { tickangle: -60, scaleanchor: "y", scaleratio: 1 },
            yaxis: { autorange: "reversed" },
          }}
          config={{ responsive: false }}
          style={{ display: "block", margin: "0 auto" }}
          onClick={(e) => {
            const clicked = e?.points?.[0]?.x;
            if (clicked) setSortBy(String(clicked));
          }}
        />
      )}
    </div>
  );
}

function RRG({ benchmark, setBenchmark, windowDays, setWindowDays, frames }: {
  benchmark: string; setBenchmark: (s: string) => void;
  windowDays: number; setWindowDays: (n: number) => void;
  frames: { date: string; points: { symbol: string; x: number; y: number; sector?: string | null }[] }[];
}) {
  const theme = plotlyTheme();
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [tailDays, setTailDays] = useState(20);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (idx >= frames.length) setIdx(Math.max(0, frames.length - 1));
  }, [frames.length, idx]);
  useEffect(() => {
    if (!playing) { if (timer.current) window.clearInterval(timer.current); return; }
    timer.current = window.setInterval(() => {
      setIdx((i) => (i + 1 >= frames.length ? 0 : i + 1));
    }, 200);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [playing, frames.length]);

  const f = frames[idx];

  const allSymbols = useMemo(() => {
    const s = new Set<string>();
    for (const fr of frames) for (const p of fr.points) s.add(p.symbol);
    return Array.from(s).sort();
  }, [frames]);

  const sectorBySymbol = useMemo(() => {
    const out: Record<string, string | null> = {};
    for (const fr of frames) {
      for (const p of fr.points) {
        if (p.sector && !out[p.symbol]) out[p.symbol] = p.sector;
      }
    }
    return out;
  }, [frames]);

  const symbolsBySector = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const s of allSymbols) {
      const sector = sectorBySymbol[s] || "Unknown";
      out[sector] = [...(out[sector] || []), s].sort();
    }
    return out;
  }, [allSymbols, sectorBySymbol]);

  function toggleSym(s: string) {
    const next = new Set(hidden);
    if (next.has(s)) next.delete(s); else next.add(s);
    setHidden(next);
  }

  const colorOf = (s: string) => {
    const sector = sectorBySymbol[s] || "Unknown";
    return sectorColor(s, sector, symbolsBySector[sector] || allSymbols);
  };

  // Build tail traces: one trace per visible symbol with last N points up to idx
  const tailTraces = useMemo(() => {
    if (!frames.length) return [];
    const out: any[] = [];
    const from = Math.max(0, idx - tailDays);
    for (const sym of allSymbols) {
      if (hidden.has(sym)) continue;
      const xs: number[] = []; const ys: number[] = [];
      for (let i = from; i <= idx; i++) {
        const pt = frames[i]?.points.find((p) => p.symbol === sym);
        if (pt) { xs.push(pt.x); ys.push(pt.y); }
      }
      if (xs.length < 2) continue;
      out.push({
        type: "scatter", mode: "lines", name: sym + " trail",
        x: xs, y: ys, line: { color: colorOf(sym), width: 1 },
        opacity: 0.6, showlegend: false, hoverinfo: "skip",
      });
    }
    return out;
  }, [frames, idx, tailDays, hidden, allSymbols]);

  const visiblePoints = (f?.points ?? []).filter((p) => !hidden.has(p.symbol));

  return (
    <>
      <div className="filters">
        <label>Benchmark:&nbsp;<input value={benchmark}
                          onChange={(e) => setBenchmark(e.target.value.toUpperCase())} /></label>
        <label>Window:&nbsp;<input type="number" value={windowDays} min={20} max={252}
                          onChange={(e) => setWindowDays(parseInt(e.target.value || "60", 10))} /></label>
        <label>Tail:&nbsp;<input type="number" value={tailDays} min={0} max={120}
                          onChange={(e) => setTailDays(parseInt(e.target.value || "0", 10))} /></label>
        <button onClick={() => setPlaying(!playing)}>{playing ? "Pause" : "Play"}</button>
        <input type="range" min={0} max={Math.max(0, frames.length - 1)} value={idx}
               onChange={(e) => setIdx(parseInt(e.target.value, 10))}
               style={{ flex: 1, minWidth: 200 }} />
        <span>{f?.date ?? ""}</span>
      </div>

      <div className="card">
        <Plot
          data={[
            ...tailTraces,
            {
              type: "scatter", mode: "markers+text",
              x: visiblePoints.map((p) => p.x),
              y: visiblePoints.map((p) => p.y),
              text: visiblePoints.map((p) => p.symbol),
              textposition: "top center",
              marker: {
                size: 14,
                color: visiblePoints.map((p) => colorOf(p.symbol)),
                line: { color: "white", width: 1 },
              },
              hovertemplate: "%{text}<br>RS-Ratio %{x:.2f}<br>RS-Mom %{y:.2f}<extra></extra>",
              showlegend: false,
            },
          ]}
          layout={{
            paper_bgcolor: theme.paper_bgcolor, plot_bgcolor: theme.plot_bgcolor,
            font: theme.font, height: 600,
            margin: { t: 10, r: 10, b: 40, l: 60 },
            xaxis: { title: "RS-Ratio", gridcolor: theme.xaxis_gridcolor, zeroline: true,
                     zerolinecolor: "#666", range: [90, 110] },
            yaxis: { title: "RS-Momentum", gridcolor: theme.yaxis_gridcolor, zeroline: true,
                     zerolinecolor: "#666", range: [-10, 10] },
            shapes: [
              { type: "rect", x0: 100, x1: 110, y0: 0, y1: 10, fillcolor: theme.pos, opacity: 0.08, line: { width: 0 } },
              { type: "rect", x0: 100, x1: 110, y0: -10, y1: 0, fillcolor: "#f0a020", opacity: 0.08, line: { width: 0 } },
              { type: "rect", x0: 90, x1: 100, y0: -10, y1: 0, fillcolor: theme.neg, opacity: 0.08, line: { width: 0 } },
              { type: "rect", x0: 90, x1: 100, y0: 0, y1: 10, fillcolor: theme.accent, opacity: 0.08, line: { width: 0 } },
            ],
            annotations: [
              { x: 105, y: 9, text: "Leading", showarrow: false, font: { color: theme.pos } },
              { x: 105, y: -9, text: "Weakening", showarrow: false, font: { color: "#f0a020" } },
              { x: 95, y: -9, text: "Lagging", showarrow: false, font: { color: theme.neg } },
              { x: 95, y: 9, text: "Improving", showarrow: false, font: { color: theme.accent } },
            ],
          }}
          style={{ width: "100%" }} useResizeHandler
        />
      </div>

      <div className="card">
        <h3>Toggle symbols</h3>
        <div className="checkbox-row">
          {allSymbols.map((s) => (
            <label key={s} style={{ borderBottom: `2px solid ${colorOf(s)}` }}>
              <input type="checkbox" checked={!hidden.has(s)} onChange={() => toggleSym(s)} />
              {s}
            </label>
          ))}
        </div>
      </div>
    </>
  );
}
