import { useQuery } from "@tanstack/react-query";
import { useState, useEffect, useRef } from "react";
import Plot from "react-plotly.js";
import { api } from "../api";

type View = "rrg" | "treemap" | "correlation";

export default function Viz() {
  const [view, setView] = useState<View>("rrg");
  const [benchmark, setBenchmark] = useState("SPY");
  const [windowDays, setWindowDays] = useState(60);

  const sectorQ = useQuery({ queryKey: ["sector"], queryFn: api.vizSector });
  const corrQ = useQuery({ queryKey: ["corr"], queryFn: api.vizCorrelation });
  const rrgQ = useQuery({
    queryKey: ["rrg", benchmark, windowDays],
    queryFn: () => api.vizRRG(benchmark, windowDays),
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
        <div className="card">
          <h3>Holdings treemap (by sector)</h3>
          <Plot
            data={[{
              type: "treemap",
              labels: (sectorQ.data?.rows ?? []).map((r: any) => r.symbol),
              parents: (sectorQ.data?.rows ?? []).map((r: any) => r.sector || "Unknown"),
              values: (sectorQ.data?.rows ?? []).map((r: any) => r.market_value),
              textinfo: "label+value+percent parent",
            }]}
            layout={{
              paper_bgcolor: "#161a22", font: { color: "#d6d8dc" },
              height: 600, margin: { t: 10, r: 10, b: 10, l: 10 },
            }}
            style={{ width: "100%" }} useResizeHandler
          />
        </div>
      )}

      {view === "correlation" && (
        <div className="card">
          <h3>Correlation matrix</h3>
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

function RRG({ benchmark, setBenchmark, windowDays, setWindowDays, frames }: {
  benchmark: string; setBenchmark: (s: string) => void;
  windowDays: number; setWindowDays: (n: number) => void;
  frames: any[];
}) {
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => { if (idx >= frames.length) setIdx(Math.max(0, frames.length - 1)); }, [frames.length, idx]);
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
            x: (f?.points ?? []).map((p: any) => p.rs_ratio),
            y: (f?.points ?? []).map((p: any) => p.rs_momentum),
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
                     zerolinecolor: "#666", range: [90, 110] },
            shapes: [
              { type: "rect", x0: 100, x1: 110, y0: 100, y1: 110, fillcolor: "#2bbf73", opacity: 0.08, line: { width: 0 } },
              { type: "rect", x0: 100, x1: 110, y0: 90, y1: 100, fillcolor: "#f0a020", opacity: 0.08, line: { width: 0 } },
              { type: "rect", x0: 90, x1: 100, y0: 90, y1: 100, fillcolor: "#e35d6a", opacity: 0.08, line: { width: 0 } },
              { type: "rect", x0: 90, x1: 100, y0: 100, y1: 110, fillcolor: "#4a8cff", opacity: 0.08, line: { width: 0 } },
            ],
            annotations: [
              { x: 105, y: 109, text: "Leading", showarrow: false, font: { color: "#2bbf73" } },
              { x: 105, y: 91, text: "Weakening", showarrow: false, font: { color: "#f0a020" } },
              { x: 95, y: 91, text: "Lagging", showarrow: false, font: { color: "#e35d6a" } },
              { x: 95, y: 109, text: "Improving", showarrow: false, font: { color: "#4a8cff" } },
            ],
          }}
          style={{ width: "100%" }} useResizeHandler
        />
      </div>
    </>
  );
}
