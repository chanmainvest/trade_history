import { useQuery } from "@tanstack/react-query";
import { useState, useEffect, useRef } from "react";
import Plot from "react-plotly.js";
import { api } from "../api";

type View = "rrg" | "treemap" | "correlation";

function todayISO() { return new Date().toISOString().slice(0, 10); }
function isoMinusYears(years: number) {
  const d = new Date(); d.setFullYear(d.getFullYear() - years);
  return d.toISOString().slice(0, 10);
}

export default function Viz() {
  const [view, setView] = useState<View>("rrg");
  const [benchmark, setBenchmark] = useState("SPY");
  const [windowDays, setWindowDays] = useState(60);
  const [monthEnd, setMonthEnd] = useState(todayISO());
  const [corrStart, setCorrStart] = useState(isoMinusYears(1));
  const [corrEnd, setCorrEnd] = useState(todayISO());

  const sectorQ = useQuery({
    queryKey: ["sector", monthEnd],
    queryFn: () => api.vizSector(monthEnd),
    enabled: view === "treemap",
  });
  const corrQ = useQuery({
    queryKey: ["corr", corrStart, corrEnd],
    queryFn: () => api.vizCorrelation(corrStart, corrEnd),
    enabled: view === "correlation",
  });
  const rrgQ = useQuery({
    queryKey: ["rrg", benchmark, windowDays],
    queryFn: () => api.vizRRG(benchmark, windowDays),
    enabled: view === "rrg",
  });

  return (
    <>
      <h2>Visualisations</h2>
      <div className="filters">
        {(["rrg", "treemap", "correlation"] as View[]).map((v) =>
          <button key={v} className={v === view ? "active" : ""} onClick={() => setView(v)}>{v}</button>
        )}
      </div>

      {view === "rrg" && (
        <RRG benchmark={benchmark} setBenchmark={setBenchmark}
             windowDays={windowDays} setWindowDays={setWindowDays}
             frames={rrgQ.data?.frames ?? []} />
      )}

      {view === "treemap" && (
        <Treemap monthEnd={monthEnd} setMonthEnd={setMonthEnd}
                 rows={sectorQ.data?.rows ?? []} />
      )}

      {view === "correlation" && (
        <div className="card">
          <div className="filters">
            <h3 style={{ marginRight: 12 }}>Correlation matrix</h3>
            <label>Start: <input type="date" value={corrStart}
                                 onChange={(e) => setCorrStart(e.target.value)} /></label>
            <label>End: <input type="date" value={corrEnd}
                               onChange={(e) => setCorrEnd(e.target.value)} /></label>
          </div>
          <Plot
            data={[{
              type: "heatmap",
              z: corrQ.data?.matrix ?? [],
              x: corrQ.data?.symbols ?? [],
              y: corrQ.data?.symbols ?? [],
              colorscale: "RdBu", zmin: -1, zmax: 1,
            }]}
            layout={{
              paper_bgcolor: "#161a22", plot_bgcolor: "#161a22",
              font: { color: "#d6d8dc" }, height: 600,
              margin: { t: 10, r: 10, b: 80, l: 80 },
            }}
            style={{ width: "100%" }} useResizeHandler
          />
        </div>
      )}
    </>
  );
}

function Treemap({ monthEnd, setMonthEnd, rows }: {
  monthEnd: string; setMonthEnd: (s: string) => void; rows: any[];
}) {
  const labels: string[] = [];
  const parents: string[] = [];
  const values: number[] = [];
  const groupTotals: Record<string, number> = {};
  for (const r of rows) {
    groupTotals[r.asset_type] = (groupTotals[r.asset_type] || 0) + (r.market_value || 0);
  }
  for (const g of Object.keys(groupTotals)) {
    labels.push(g); parents.push(""); values.push(groupTotals[g]);
  }
  for (const r of rows) {
    labels.push(r.symbol);
    parents.push(r.asset_type);
    values.push(r.market_value || 0);
  }
  return (
    <div className="card">
      <div className="filters">
        <h3 style={{ marginRight: 12 }}>Holdings treemap</h3>
        <label>As of: <input type="date" value={monthEnd}
                             onChange={(e) => setMonthEnd(e.target.value)} /></label>
      </div>
      <Plot
        data={[{
          type: "treemap", labels, parents, values,
          branchvalues: "total",
          textinfo: "label+value+percent parent",
        }]}
        layout={{
          paper_bgcolor: "#161a22", font: { color: "#d6d8dc" },
          height: 600, margin: { t: 10, r: 10, b: 10, l: 10 },
        }}
        style={{ width: "100%" }} useResizeHandler
      />
    </div>
  );
}

function RRG({ benchmark, setBenchmark, windowDays, setWindowDays, frames }: {
  benchmark: string; setBenchmark: (s: string) => void;
  windowDays: number; setWindowDays: (n: number) => void;
  frames: any[];
}) {
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
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

  return (
    <>
      <div className="filters">
        <label>Benchmark: <input value={benchmark} onChange={(e) => setBenchmark(e.target.value.toUpperCase())} /></label>
        <label>Window: <input type="number" value={windowDays} min={20} max={252}
                              onChange={(e) => setWindowDays(parseInt(e.target.value || "60", 10))} /></label>
        <button onClick={() => setPlaying(!playing)}>{playing ? "Pause" : "Play"}</button>
        <input type="range" min={0} max={Math.max(0, frames.length - 1)} value={idx}
               onChange={(e) => setIdx(parseInt(e.target.value, 10))}
               style={{ flex: 1 }} />
        <span>{f?.date ?? ""}</span>
      </div>
      <div className="card">
        <Plot
          data={[{
            type: "scatter", mode: "markers+text",
            x: (f?.points ?? []).map((p: any) => p.x),
            y: (f?.points ?? []).map((p: any) => p.y),
            text: (f?.points ?? []).map((p: any) => p.symbol),
            textposition: "top center",
            marker: { size: 12, color: "#4a8cff" },
          }]}
          layout={{
            paper_bgcolor: "#161a22", plot_bgcolor: "#161a22",
            font: { color: "#d6d8dc" }, height: 600,
            margin: { t: 10, r: 10, b: 40, l: 60 },
            xaxis: { title: "RS-Ratio", gridcolor: "#2a2f3a", zeroline: true,
                     zerolinecolor: "#666", range: [90, 110] },
            yaxis: { title: "RS-Momentum", gridcolor: "#2a2f3a", zeroline: true,
                     zerolinecolor: "#666", range: [-10, 10] },
            shapes: [
              { type: "rect", x0: 100, x1: 110, y0: 0, y1: 10, fillcolor: "#2bbf73", opacity: 0.08, line: { width: 0 } },
              { type: "rect", x0: 100, x1: 110, y0: -10, y1: 0, fillcolor: "#f0a020", opacity: 0.08, line: { width: 0 } },
              { type: "rect", x0: 90, x1: 100, y0: -10, y1: 0, fillcolor: "#e35d6a", opacity: 0.08, line: { width: 0 } },
              { type: "rect", x0: 90, x1: 100, y0: 0, y1: 10, fillcolor: "#4a8cff", opacity: 0.08, line: { width: 0 } },
            ],
            annotations: [
              { x: 105, y: 9, text: "Leading", showarrow: false, font: { color: "#2bbf73" } },
              { x: 105, y: -9, text: "Weakening", showarrow: false, font: { color: "#f0a020" } },
              { x: 95, y: -9, text: "Lagging", showarrow: false, font: { color: "#e35d6a" } },
              { x: 95, y: 9, text: "Improving", showarrow: false, font: { color: "#4a8cff" } },
            ],
          }}
          style={{ width: "100%" }} useResizeHandler
        />
      </div>
    </>
  );
}
