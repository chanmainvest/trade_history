import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Pie, PieChart, ResponsiveContainer, Tooltip, Cell } from "recharts";
import { fetchSector } from "../api";
import { money, pct } from "../format";
import type { Currency, SectorRow } from "../types";

type Props = {
  displayCurrency: Currency;
  privacy: boolean;
};

const COLORS = ["#ff7f50", "#00a878", "#f6aa1c", "#2a9d8f", "#264653", "#f28482", "#669bbc"];

export function SectorTab({ displayCurrency, privacy }: Props) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<SectorRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchSector(displayCurrency)
      .then((data) => {
        if (!cancelled) {
          setRows(data);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(String(err));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [displayCurrency]);

  const total = useMemo(() => rows.reduce((acc, item) => acc + item.value, 0), [rows]);

  return (
    <section className="tab-panel">
      <article className="stat-card stat-wide">
        <h4>{t("total")}</h4>
        <p>{money(total, displayCurrency, privacy)}</p>
      </article>

      {loading ? <p>Loading...</p> : null}
      {error ? <p className="error-text">{error}</p> : null}
      {!loading && rows.length === 0 ? <p>{t("noData")}</p> : null}

      {rows.length > 0 ? (
        <div className="sector-grid">
          <div className="chart-shell">
            <ResponsiveContainer width="100%" height={340}>
              <PieChart>
                <Pie data={rows} dataKey="value" nameKey="sector" innerRadius={70} outerRadius={130}>
                  {rows.map((entry, idx) => (
                    <Cell key={entry.sector} fill={COLORS[idx % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  formatter={(value: number) => money(value, displayCurrency, privacy)}
                  contentStyle={{ borderRadius: "12px", border: "1px solid #274060" }}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Sector</th>
                  <th>{t("value")}</th>
                  <th>%</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.sector}>
                    <td>{row.sector}</td>
                    <td>{money(row.value, displayCurrency, privacy)}</td>
                    <td>{pct(row.percentage, privacy)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}

