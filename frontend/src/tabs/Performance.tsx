import { useQuery } from "@tanstack/react-query";
import Plot from "react-plotly.js";
import { api } from "../api";

export default function Performance() {
  const totalQ = useQuery({ queryKey: ["perfTotal"], queryFn: api.perfTotal });
  const cashQ = useQuery({ queryKey: ["perfCash"], queryFn: api.perfCash });

  const totalRows = totalQ.data?.rows ?? [];
  const cashRows = cashQ.data?.rows ?? [];

  return (
    <>
      <h2>Performance</h2>
      <div className="card">
        <h3>Total market value over time</h3>
        <Plot
          data={[{
            type: "scatter", mode: "lines", name: "Total MV",
            x: totalRows.map((r: any) => r.as_of_date),
            y: totalRows.map((r: any) => r.market_value),
            line: { color: "#4a8cff" },
          }]}
          layout={{
            paper_bgcolor: "#161a22", plot_bgcolor: "#161a22",
            font: { color: "#d6d8dc" }, margin: { t: 10, r: 10, b: 40, l: 60 },
            xaxis: { gridcolor: "#2a2f3a" }, yaxis: { gridcolor: "#2a2f3a" },
            height: 360,
          }}
          style={{ width: "100%" }}
          useResizeHandler
        />
      </div>

      <div className="card">
        <h3>Cash by currency</h3>
        <Plot
          data={[
            {
              type: "scatter", mode: "lines", name: "CAD",
              x: cashRows.filter((r: any) => r.currency === "CAD").map((r: any) => r.as_of_date),
              y: cashRows.filter((r: any) => r.currency === "CAD").map((r: any) => r.balance),
            },
            {
              type: "scatter", mode: "lines", name: "USD",
              x: cashRows.filter((r: any) => r.currency === "USD").map((r: any) => r.as_of_date),
              y: cashRows.filter((r: any) => r.currency === "USD").map((r: any) => r.balance),
            },
          ]}
          layout={{
            paper_bgcolor: "#161a22", plot_bgcolor: "#161a22",
            font: { color: "#d6d8dc" }, margin: { t: 10, r: 10, b: 40, l: 60 },
            xaxis: { gridcolor: "#2a2f3a" }, yaxis: { gridcolor: "#2a2f3a" },
            height: 320,
          }}
          style={{ width: "100%" }}
          useResizeHandler
        />
      </div>
    </>
  );
}
