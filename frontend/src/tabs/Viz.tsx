import { useQuery } from "@tanstack/react-query";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import Plot from "react-plotly.js";
import Plotly from "plotly.js/dist/plotly";
import { api } from "../api";
import type { AssetRow } from "../api";
import { usePortfolio } from "../portfolio";
import { SmartSelect } from "../SmartSelect";
import { plotlyTheme } from "../theme";
import { useI18n } from "../i18n";

type View = "rrg" | "treemap" | "correlation" | "assets";
type TreemapGroupBy = "account" | "asset_type" | "sector";
type TreemapPeriod = "1d" | "1w" | "1m" | "3m" | "6m" | "1y" | "ytd";
type SparkWindow = "1w" | "1mo" | "3mo" | "1y" | "3y";
type TreemapRow = {
  account_id: number;
  account_number: string;
  institution_code: string;
  institution_name: string;
  symbol: string;
  asset_type: string;
  currency: string;
  market_value: number;
  sector?: string | null;
  industry?: string | null;
  performance_pct?: number | null;
};

function todayISO() { return new Date().toISOString().slice(0, 10); }
function isoMinusDays(iso: string, days: number) {
  const d = new Date(`${iso}T00:00:00Z`);
  if (isNaN(d.getTime())) return iso;
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}
function isoMinusYears(years: number) {
  const d = new Date(); d.setFullYear(d.getFullYear() - years);
  return d.toISOString().slice(0, 10);
}
function interpolate(text: string, values: Record<string, string>): string {
  return Object.entries(values).reduce(
    (result, [key, value]) => result.replaceAll(`{${key}}`, value),
    text,
  );
}

const CORR_RGB_STOPS = [
  { position: 0.0, red: 30, green: 100, blue: 200 },
  { position: 0.25, red: 50, green: 200, blue: 180 },
  { position: 0.5, red: 200, green: 180, blue: 50 },
  { position: 0.7, red: 225, green: 120, blue: 50 },
  { position: 1.0, red: 200, green: 50, blue: 50 },
];

function corrColor(value: number) {
  const position = Math.max(0, Math.min(1, (value + 1) / 2));
  let lower = CORR_RGB_STOPS[0];
  let upper = CORR_RGB_STOPS[CORR_RGB_STOPS.length - 1];
  for (let index = 1; index < CORR_RGB_STOPS.length; index += 1) {
    if (position <= CORR_RGB_STOPS[index].position) {
      lower = CORR_RGB_STOPS[index - 1];
      upper = CORR_RGB_STOPS[index];
      break;
    }
  }
  const span = upper.position - lower.position || 1;
  const ratio = (position - lower.position) / span;
  const blend = (start: number, finish: number) => Math.round(start + (finish - start) * ratio);
  return `rgb(${blend(lower.red, upper.red)}, ${blend(lower.green, upper.green)}, ${blend(lower.blue, upper.blue)})`;
}

function corrTextColor(value: number) {
  return Math.abs(value) > 0.55 ? "#ffffff" : "#07111f";
}

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

function shade(hex: string, amount: number) {  const n = parseInt(hex.slice(1), 16);
  const r = Math.max(0, Math.min(255, (n >> 16) + amount));
  const g = Math.max(0, Math.min(255, ((n >> 8) & 255) + amount));
  const b = Math.max(0, Math.min(255, (n & 255) + amount));
  return `#${[r, g, b].map((x) => x.toString(16).padStart(2, "0")).join("")}`;
}

function hexToRgba(hex: string, alpha: number) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

// Stable colors for sector names that are not in SECTOR_COLORS (fund
// categories like "India Equity" arrive as free-form Yahoo strings).
const FALLBACK_SECTOR_COLORS = [
  "#3B82C4", "#9467BD", "#E377C2", "#2CA02C", "#FF7F0E",
  "#17BECF", "#BCBD22", "#8C564B", "#7F7FBF", "#C49BD3",
];

function stableSectorColor(name: string) {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return FALLBACK_SECTOR_COLORS[hash % FALLBACK_SECTOR_COLORS.length];
}

function sectorColor(symbol: string, sector: string | null | undefined, symbols: string[]) {
  const group = sector || "Unknown";
  const base = SECTOR_COLORS[group] || stableSectorColor(group);
  const peers = symbols.filter((s) => s !== symbol).length;
  const idx = symbols.indexOf(symbol);
  if (idx < 0 || peers <= 0) return base;
  const offset = -22 + (44 * idx) / Math.max(1, peers);
  return shade(base, Math.round(offset));
}

function formatPerf(value: number | null | undefined, perfNa: string, perfValue: string) {
  if (value == null || !Number.isFinite(value)) return perfNa;
  const sign = value > 0 ? "+" : "";
  return `${perfValue.replace("{value}", `${sign}${value.toFixed(2)}`)}`;
}

function performanceColor(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return SECTOR_COLORS.Unknown;
  const clamped = Math.max(-10, Math.min(10, value));
  if (Math.abs(clamped) < 0.05) return "#6b7280";
  const intensity = Math.round(70 + Math.min(1, Math.abs(clamped) / 10) * 90);
  return clamped > 0
    ? `rgb(20, ${intensity + 80}, 85)`
    : `rgb(${intensity + 80}, 55, 60)`;
}

export default function Viz() {
  const { activeAccountIds, accounts } = usePortfolio();
  const { t } = useI18n();
  const [view, setView] = useState<View>("rrg");
  const [benchmark, setBenchmark] = useState("SPY");
  const [windowDays, setWindowDays] = useState(60);
  const [institutions, setInstitutions] = useState<string[]>([]);
  const [accountIds, setAccountIds] = useState<string[]>([]);
  // "All accounts" bypasses the top-bar portfolio scope, matching Transactions.
  const [showAllAccounts, setShowAllAccounts] = useState(false);

  const latestQ = useQuery({ queryKey: ["latest-date"], queryFn: api.latestDate });
  const latest = latestQ.data?.latest || todayISO();
  const [monthEnd, setMonthEnd] = useState<string>("");
  const [treemapPeriod, setTreemapPeriod] = useState<TreemapPeriod>("1m");
  const [displayCcy, setDisplayCcy] = useState<"CAD" | "USD">("CAD");
  const [rrgEnd, setRrgEnd] = useState(todayISO());

  const effectiveMonthEnd = monthEnd || latest;

  const [corrStart, setCorrStart] = useState(isoMinusYears(1));
  const [corrEnd, setCorrEnd] = useState(todayISO());

  // The top-bar portfolio scopes the institution/account dropdown options the
  // same way it scopes the queries; "All accounts" restores the full lists.
  const portfolioScoped = activeAccountIds.length > 0 && !showAllAccounts;
  const scopedAccounts = useMemo(() => {
    if (!portfolioScoped) return accounts;
    const ids = new Set(activeAccountIds);
    return accounts.filter((a) => ids.has(a.account_id));
  }, [accounts, portfolioScoped, activeAccountIds]);

  const instOpts = useMemo(() => Array.from(new Set(scopedAccounts.map((a) => a.institution_code)))
    .sort().map((c) => ({ value: c, label: c })), [scopedAccounts]);
  const acctOpts = useMemo(() => scopedAccounts.map((a) => ({
    value: String(a.account_id),
    label: `${a.institution_code} • ${a.account_number}`,
    hint: a.base_currency,
  })), [scopedAccounts]);

  // Drop selected filter values that the active portfolio excludes.
  useEffect(() => {
    if (!portfolioScoped) return;
    const validInst = new Set(scopedAccounts.map((a) => a.institution_code));
    const validAcct = new Set(scopedAccounts.map((a) => String(a.account_id)));
    setInstitutions((prev) => {
      const next = prev.filter((v) => validInst.has(v));
      return next.length === prev.length ? prev : next;
    });
    setAccountIds((prev) => {
      const next = prev.filter((v) => validAcct.has(v));
      return next.length === prev.length ? prev : next;
    });
  }, [portfolioScoped, scopedAccounts]);

  const acctFilter = useMemo(() => {
    if (accountIds.length > 0) return accountIds.map((x) => parseInt(x, 10));
    let ids = activeAccountIds.length > 0 && !showAllAccounts
      ? activeAccountIds
      : accounts.map((a) => a.account_id);
    if (institutions.length > 0) {
      const allowed = new Set(institutions);
      ids = ids.filter((id) => allowed.has(accounts.find((a) => a.account_id === id)?.institution_code || ""));
    }
    if (ids.length === 0) return undefined;
    if (institutions.length === 0 && ids.length === accounts.length) return undefined;
    return ids;
  }, [accountIds, activeAccountIds, showAllAccounts, accounts, institutions]);

  const sectorQ = useQuery({
    queryKey: ["sector", effectiveMonthEnd, treemapPeriod, displayCcy, acctFilter],
    queryFn: () => api.vizSector({ month_end: effectiveMonthEnd, period: treemapPeriod,
                                   currency: displayCcy, account_id: acctFilter }),
    enabled: view === "treemap",
  });
  const corrQ = useQuery({
    queryKey: ["corr", corrStart, corrEnd, acctFilter],
    queryFn: () => api.vizCorrelation({ start: corrStart, end: corrEnd, account_id: acctFilter }),
    enabled: view === "correlation",
  });
  const effectiveRrgEnd = rrgEnd || todayISO();
  // One year of frames, plus enough prior data for the rolling RS window.
  const rrgStart = isoMinusDays(effectiveRrgEnd, 365 + Math.ceil(windowDays * 1.5));
  const rrgQ = useQuery({
    queryKey: ["rrg", benchmark, windowDays, rrgStart, effectiveRrgEnd, acctFilter],
    queryFn: () => api.vizRRG({ benchmark, window_days: windowDays,
                               start: rrgStart, end: effectiveRrgEnd, account_id: acctFilter }),
    enabled: view === "rrg",
  });

  const [assetsEnd, setAssetsEnd] = useState<string>("");
  const effectiveAssetsEnd = assetsEnd || todayISO();
  const assetsQ = useQuery({
    queryKey: ["assets", effectiveAssetsEnd, acctFilter],
    queryFn: () => api.vizAssets({ month_end: effectiveAssetsEnd, account_id: acctFilter }),
    enabled: view === "assets",
  });

  return (
    <>
      <div className="filters">
        {(["rrg", "treemap", "correlation", "assets"] as View[]).map((v) =>
          <button key={v} className={v === view ? "active" : ""} onClick={() => setView(v)}>{t(`viz.${v}`)}</button>
        )}
        <SmartSelect label={t("f.institution")} options={instOpts} value={institutions} onChange={setInstitutions} />
        <SmartSelect label={t("f.account")} options={acctOpts} value={accountIds} onChange={setAccountIds} />
        {activeAccountIds.length > 0 && !showAllAccounts && accountIds.length === 0 && (
          <span className="tag accent">{t("viz.portfolio_filter_on")}</span>
        )}
        {activeAccountIds.length > 0 && (
          <button
            type="button"
            className={showAllAccounts ? "tag accent" : "tag"}
            onClick={() => {
              setShowAllAccounts(true);
              setAccountIds([]);
            }}
          >
            {t("monthly.all_accounts")}
          </button>
        )}
      </div>

      {view === "rrg" && (
        <RRG benchmark={benchmark} setBenchmark={setBenchmark}
             windowDays={windowDays} setWindowDays={setWindowDays}
             end={effectiveRrgEnd} setEnd={setRrgEnd}
             frames={rrgQ.data?.frames ?? []} />
      )}

      {view === "treemap" && (
        <Treemap monthEnd={effectiveMonthEnd}
                 setMonthEnd={setMonthEnd}
                 period={treemapPeriod}
                 setPeriod={setTreemapPeriod}
                 displayCcy={displayCcy}
                 setDisplayCcy={setDisplayCcy}
                 fx={sectorQ.data?.fx}
                 unconverted={sectorQ.data?.unconverted_currencies ?? []}
                 actualDate={sectorQ.data?.as_of_date}
                 rows={sectorQ.data?.rows ?? []}
                 loading={sectorQ.isLoading}
                 priceDataThrough={sectorQ.data?.price_data_through} />
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

      {view === "assets" && (
        <AssetsView end={effectiveAssetsEnd} setEnd={setAssetsEnd}
                    actualDate={assetsQ.data?.as_of_date}
                    priceDataThrough={assetsQ.data?.price_data_through}
                    rows={assetsQ.data?.rows ?? []}
                    loading={assetsQ.isLoading} />
      )}
    </>
  );
}

// Excel-style sparkline: normalized polyline whose color/green-red hue and
// shade intensity encode the window's profit/loss direction and magnitude.
const SPARK_W = 92;
const SPARK_H = 24;
const SPARK_PCT_REF: Record<SparkWindow, number> = { "1w": 5, "1mo": 10, "3mo": 20, "1y": 40, "3y": 80 };

function Sparkline({ points, pct, window: win }: { points: number[]; pct: number | null; window: SparkWindow }) {
  if (!points || points.length < 2 || pct == null || !Number.isFinite(pct)) {
    return <span className="spark-none">—</span>;
  }
  const lo = Math.min(...points);
  const hi = Math.max(...points);
  const span = hi - lo || 1;
  const px = (i: number) => 2 + (i / (points.length - 1)) * (SPARK_W - 4);
  const py = (v: number) => 2 + (1 - (v - lo) / span) * (SPARK_H - 4);
  const path = points.map((v, i) => `${i ? "L" : "M"}${px(i).toFixed(1)},${py(v).toFixed(1)}`).join("");
  const gain = pct >= 0;
  const intensity = 0.5 + Math.min(1, Math.abs(pct) / SPARK_PCT_REF[win]) * 0.5;
  const color = gain
    ? `rgba(24, 152, 88, ${intensity.toFixed(2)})`
    : `rgba(206, 58, 58, ${intensity.toFixed(2)})`;
  const label = `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
  return (
    <span className="spark-cell" title={label}>
      <svg width={SPARK_W} height={SPARK_H} aria-hidden="true">
        <path d={path} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" />
        <circle cx={px(points.length - 1)} cy={py(points[points.length - 1])} r={2} fill={color} />
      </svg>
      <span className="spark-pct" style={{ color }}>{label}</span>
    </span>
  );
}

const ASSET_SPARKS: SparkWindow[] = ["1w", "1mo", "3mo", "1y", "3y"];

function fmtMoney(v: number | null | undefined, ccy: string | null): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const prefix = ccy === "USD" ? "US$" : ccy === "CAD" ? "C$" : "";
  if (Math.abs(v) >= 1e12) return `${prefix}${(v / 1e12).toFixed(2)}T`;
  if (Math.abs(v) >= 1e9) return `${prefix}${(v / 1e9).toFixed(2)}B`;
  if (Math.abs(v) >= 1e6) return `${prefix}${(v / 1e6).toFixed(1)}M`;
  return `${prefix}${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

type AssetSortKey = "symbol" | "price" | "market_cap" | "market_value" | "hv_30d" | "iv" | `spark:${SparkWindow}`;
type AssetSort = { key: AssetSortKey; dir: 1 | -1 };

function assetSortValue(r: AssetRow, key: AssetSortKey): number | string | null {
  if (key === "symbol") return r.symbol;
  if (key === "market_cap" || key === "market_value") {
    // market_value sorts the (hidden) exposure column: the default order.
    const v = key === "market_cap" ? r.market_cap : r.market_value;
    return v == null || !Number.isFinite(v) ? null : v;
  }
  if (key.startsWith("spark:")) return r.sparks?.[key.slice(6) as SparkWindow]?.pct ?? null;
  const v = r[key as "price" | "hv_30d" | "iv"];
  return v == null || !Number.isFinite(v) ? null : v;
}

function AssetsView({ end, setEnd, actualDate, priceDataThrough, rows, loading }: {
  end: string;
  setEnd: (s: string) => void;
  actualDate: string | null | undefined;
  priceDataThrough: string | null | undefined;
  rows: AssetRow[];
  loading: boolean;
}) {
  const { t } = useI18n();
  // Default: biggest positions first (the Value column is hidden but the
  // sort still reflects exposure until a header is clicked).
  const [sort, setSort] = useState<AssetSort>({ key: "market_value", dir: -1 });
  const sorted = useMemo(() => {
    const out = [...rows];
    out.sort((a, b) => {
      const av = assetSortValue(a, sort.key);
      const bv = assetSortValue(b, sort.key);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;   // nulls last in both directions
      if (bv == null) return -1;
      const cmp = typeof av === "string" || typeof bv === "string"
        ? String(av).localeCompare(String(bv))
        : (av as number) - (bv as number);
      return cmp * sort.dir;
    });
    return out;
  }, [rows, sort]);

  const header = (key: AssetSortKey, label: string, numeric = false) => {
    const active = sort.key === key;
    const arrow = active ? (sort.dir === 1 ? " ▲" : " ▼") : "";
    return (
      <th className={`sortable${numeric ? " num" : ""}${active ? " active" : ""}`}
          title={t("common.sort_hint")}
          aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
          onClick={() => setSort((s) => (
            s.key === key ? { key, dir: s.dir === 1 ? -1 : 1 } : { key, dir: 1 }
          ))}>
        {label}{arrow}
      </th>
    );
  };

  return (
    <div className="card">
      <div className="filters">
        <h3 style={{ marginRight: 12 }}>{t("viz.assets")}</h3>
        <label>{t("viz.as_of")}:&nbsp;
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        {actualDate && <span className="tag">{t("viz.table_as_of").replace("{date}", actualDate)}</span>}
        {priceDataThrough && <span className="tag">{t("viz.price_data_through").replace("{date}", priceDataThrough)}</span>}
        {loading && <span className="tag">{t("common.loading") || "…"}</span>}
      </div>
      <div className="table-scroll">
        <table className="assets-table">
          <thead>
            <tr>
              {header("symbol", t("col.symbol"))}
              {header("price", t("col.price"), true)}
              {header("market_cap", t("col.mktcap"), true)}
              {header("hv_30d", t("col.hv"), true)}
              {header("iv", t("col.iv"), true)}
              {ASSET_SPARKS.map((w) => header(`spark:${w}`, w.toUpperCase(), true))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={`${r.symbol}-${r.currency}`}>
                <td>
                  <span className="asset-sym">{r.symbol}</span>
                  {r.name && <span className="asset-name">{r.name}</span>}
                  <span className="asset-accounts" title={r.accounts.join(", ")}>{r.accounts.join(", ")}</span>
                </td>
                <td className={`num${r.price_source === "broker" ? " broker-price" : ""}`}
                    title={r.price_source === "broker" ? t("viz.broker_price_hint") : r.price_date || undefined}>
                  {r.price == null ? "—" : `${r.price_source === "broker" ? "~" : ""}${fmtMoney(r.price, r.currency)}`}
                </td>
                <td className="num">{fmtMoney(r.market_cap, r.currency)}</td>
                <td className="num">{r.hv_30d == null ? "—" : `${r.hv_30d.toFixed(1)}%`}</td>
                <td className="num" title={r.iv_date || undefined}>
                  {r.iv == null ? "—" : `${r.iv.toFixed(1)}%`}
                </td>
                {ASSET_SPARKS.map((w) => (
                  <td key={w} className="num">
                    <Sparkline points={r.sparks?.[w]?.points ?? []} pct={r.sparks?.[w]?.pct ?? null} window={w} />
                  </td>
                ))}
              </tr>
            ))}
            {rows.length === 0 && !loading && (
              <tr><td colSpan={5 + ASSET_SPARKS.length}>{t("common.no_data")}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Treemap({ monthEnd, setMonthEnd, period, setPeriod, displayCcy, setDisplayCcy,
                   fx, unconverted, actualDate, rows, loading, priceDataThrough }: {
  monthEnd: string;
  setMonthEnd: (s: string) => void;
  period: TreemapPeriod;
  setPeriod: (s: TreemapPeriod) => void;
  displayCcy: "CAD" | "USD";
  setDisplayCcy: (c: "CAD" | "USD") => void;
  fx: { usd_cad: number; rate_date: string } | null | undefined;
  unconverted: string[];
  actualDate: string | null | undefined;
  rows: TreemapRow[];
  loading: boolean;
  priceDataThrough: string | null | undefined;
}) {
  const theme = plotlyTheme();
  const { t } = useI18n();
  const [groupBy, setGroupBy] = useState<TreemapGroupBy>("sector");
  const perfLabel = useMemo(() => ({
    na: t("viz.perf_na"),
    value: t("viz.perf_value"),
  }), [t]);
  const { ids, labels, parents, values, colors, customdata } = useMemo(() => {
    const ids: string[] = [];
    const labels: string[] = [];
    const parents: string[] = [];
    const values: number[] = [];
    const colors: string[] = [];
    const customdata: string[] = [];
    const totals = new Map<string, number>();
    const leafTotals = new Map<string, { label: string; parent: string; group: string; symbol: string; value: number; detail: string }>();

    function addNode(id: string, label: string, parent: string, value: number, color: string, detail: string) {
      ids.push(id); labels.push(label); parents.push(parent); values.push(value); colors.push(color); customdata.push(detail);
    }
    function addTotal(key: string, amount: number) {
      totals.set(key, (totals.get(key) || 0) + amount);
    }
    function addLeaf(key: string, label: string, parent: string, group: string, symbol: string, amount: number, detail: string) {
      const current = leafTotals.get(key);
      if (current) current.value += amount;
      else leafTotals.set(key, { label, parent, group, symbol, value: amount, detail });
    }

    for (const row of rows) {
      const value = row.market_value || 0;
      if (value <= 0) continue;
      if (groupBy === "account") {
        const institutionId = `institution:${row.institution_code}`;
        const accountId = `account:${row.account_id}`;
        const accountLabel = `${row.institution_code} • ${row.account_number}`;
        addTotal(institutionId, value);
        addTotal(accountId, value);
        addLeaf(
          `holding:${row.account_id}:${row.symbol}:${row.currency}`,
          row.symbol,
          accountId,
          accountLabel,
          row.symbol,
          value,
          `${accountLabel}<br>${row.asset_type} • ${row.currency}<br>${formatPerf(row.performance_pct, perfLabel.na, perfLabel.value)}`,
        );
      } else {
        const group = groupBy === "sector" ? (row.sector || "Unknown") : row.asset_type;
        const groupId = `${groupBy}:${group}`;
        addTotal(groupId, value);
        addLeaf(
          `holding:${groupId}:${row.symbol}:${row.currency}`,
          row.symbol,
          groupId,
          group,
          row.symbol,
          value,
          `${group}<br>${row.asset_type} • ${row.currency}<br>${formatPerf(row.performance_pct, perfLabel.na, perfLabel.value)}`,
        );
      }
    }

    if (groupBy === "account") {
      const institutions = Array.from(new Set(rows.map((row) => row.institution_code))).sort();
      for (const institution of institutions) {
        const id = `institution:${institution}`;
        addNode(id, institution, "", totals.get(id) || 0, SECTOR_COLORS.Unknown, institution);
        const accounts = Array.from(new Map(
          rows.filter((row) => row.institution_code === institution)
            .map((row) => [row.account_id, `${row.institution_code} • ${row.account_number}`]),
        ).entries()).sort((a, b) => a[1].localeCompare(b[1]));
        for (const [accountId, label] of accounts) {
          const id = `account:${accountId}`;
          addNode(id, label, `institution:${institution}`, totals.get(id) || 0, sectorColor(label, institution, accounts.map(([, accountLabel]) => accountLabel)), label);
        }
      }
    } else {
      const groups = Array.from(new Set(Array.from(leafTotals.values()).map((leaf) => leaf.group))).sort();
      for (const group of groups) {
        const id = `${groupBy}:${group}`;
        addNode(id, group, "", totals.get(id) || 0, SECTOR_COLORS[group] || stableSectorColor(group), group);
      }
    }

    const symbolsByParent: Record<string, string[]> = {};
    for (const leaf of leafTotals.values()) {
      symbolsByParent[leaf.parent] = [...(symbolsByParent[leaf.parent] || []), leaf.symbol].sort();
    }
    for (const [id, leaf] of Array.from(leafTotals.entries()).sort((a, b) => a[1].label.localeCompare(b[1].label))) {
      const source = rows.find((row) => row.symbol === leaf.symbol);
      addNode(id, leaf.label, leaf.parent, leaf.value,
        performanceColor(source?.performance_pct), leaf.detail);
    }
    return { ids, labels, parents, values, colors, customdata };
  }, [rows, groupBy, perfLabel]);

  const staleDays = useMemo(() => {
    if (!priceDataThrough) return null;
    const days = Math.floor((Date.now() - new Date(priceDataThrough).getTime()) / 86400000);
    return days >= 7 ? days : null;
  }, [priceDataThrough]);

  return (
    <div className="card">
      <div className="filters">
        <h3 style={{ marginRight: 12 }}>{t("viz.holdings_treemap")}</h3>
        <label>{t("viz.as_of")}:&nbsp;
          <input type="date" value={monthEnd} onChange={(e) => setMonthEnd(e.target.value)} />
        </label>
        <label>{t("viz.group_by")}:&nbsp;
          <select value={groupBy} onChange={(e) => setGroupBy(e.target.value as TreemapGroupBy)}>
            <option value="account">{t("viz.group_account")}</option>
            <option value="asset_type">{t("viz.group_type")}</option>
            <option value="sector">{t("viz.group_sector")}</option>
          </select>
        </label>
        <label>{t("viz.performance_label")}:&nbsp;
          <select value={period} onChange={(e) => setPeriod(e.target.value as TreemapPeriod)}>
            <option value="1d">1D</option>
            <option value="1w">1W</option>
            <option value="1m">1M</option>
            <option value="3m">3M</option>
            <option value="6m">6M</option>
            <option value="1y">1Y</option>
            <option value="ytd">YTD</option>
          </select>
        </label>
        <span className="filters" style={{ gap: 0 }}>
          {(["CAD", "USD"] as const).map((ccy) => (
            <button key={ccy} className={ccy === displayCcy ? "active" : ""}
                    onClick={() => setDisplayCcy(ccy)}>{ccy}</button>
          ))}
        </span>
        {actualDate && actualDate !== monthEnd && (
          <span className="muted">{interpolate(t("viz.snapshot_from"), { date: actualDate })}</span>
        )}
        {priceDataThrough && (
          <span className="muted">
            {interpolate(t("viz.price_data_through"), { date: priceDataThrough })}
            {staleDays != null && interpolate(t("viz.data_stale"), { days: String(staleDays) })}
          </span>
        )}
      </div>
      <div className="filters">
        <span className="legend-chip" style={{ background: "rgb(20, 170, 85)" }} />
        <span className="muted">{t("viz.legend_gain")}</span>
        <span className="legend-chip" style={{ background: "rgb(170, 55, 60)" }} />
        <span className="muted">{t("viz.legend_loss")}</span>
        <span className="legend-chip" style={{ background: "#6b7280" }} />
        <span className="muted">{t("viz.legend_nodata")}</span>
        {unconverted.length > 0 ? (
          <span className="muted" style={{ marginLeft: "auto" }}>
            {interpolate(t("viz.unconverted_note"), { currencies: unconverted.join(", ") })}
          </span>
        ) : fx ? (
          <span className="muted" style={{ marginLeft: "auto" }}>
            {interpolate(t("viz.converted_note"), {
              currency: displayCcy,
              rate: fx.usd_cad.toFixed(4),
              date: fx.rate_date,
            })}
          </span>
        ) : null}
      </div>
      {loading && <p className="muted">{t("viz.loading")}</p>}
      {!loading && rows.length === 0 && (
        <p className="muted">{t("viz.no_holdings")}</p>
      )}
      {rows.length > 0 && (
        <Plot
          data={[{
            type: "treemap", ids, labels, parents, values, customdata,
            branchvalues: "total",
            textinfo: "label+value+percent parent",
            marker: {
              colors, showscale: false,
              line: { width: 1, color: theme.paper_bgcolor },
            },
            hovertemplate: "<b>%{label}</b><br>%{customdata}<br>%{value:$,.0f}<br>%{percentParent:.1%} of parent<extra></extra>",
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
  const [sortBy, setSortBy] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const { t } = useI18n();

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
  const columnTemplate = `minmax(96px, max-content) repeat(${Math.max(sortedSymbols.length, 1)}, var(--corr-cell-size))`;

  return (
    <div className="card">
      <div className="filters">
        <h3 style={{ marginRight: 12 }}>{t("viz.correlation_matrix")}</h3>
        <label>{t("viz.start")}:&nbsp;<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label>{t("viz.end")}:&nbsp;<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <label>{t("viz.sort_by")}:&nbsp;
          <select value={sortBy || ""} onChange={(e) => setSortBy(e.target.value || null)}>
            <option value="">{t("viz.sort_alphabetical")}</option>
            {symbols.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <span className="muted">{interpolate(t("viz.symbols_count"), { count: String(symbols.length) })}</span>
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
        <p className="muted">{t("viz.no_corr_data")}</p>
      ) : sortedSymbols.length === 0 ? (
        <p className="muted">{t("viz.all_hidden")}</p>
      ) : (
        <div className="correlation-matrix-scroll">
          <div className="correlation-grid" style={{ gridTemplateColumns: columnTemplate }}>
            <div className="correlation-corner" />
            {sortedSymbols.map((symbolName) => (
              <button
                key={`col-${symbolName}`}
                type="button"
                className={`correlation-label correlation-label-top${sortBy === symbolName ? " active" : ""}`}
                title={interpolate(t("viz.sort_by_symbol"), { symbol: symbolName })}
                onClick={() => setSortBy(symbolName)}
              >
                {symbolName}
              </button>
            ))}
            {sortedSymbols.map((rowSymbol, rowIndex) => (
              <Fragment key={`row-group-${rowSymbol}`}>
                <button
                  key={`row-${rowSymbol}`}
                  type="button"
                  className={`correlation-label correlation-label-side${sortBy === rowSymbol ? " active" : ""}`}
                  title={interpolate(t("viz.sort_by_symbol"), { symbol: rowSymbol })}
                  onClick={() => setSortBy(rowSymbol)}
                >
                  {rowSymbol}
                </button>
                {sortedSymbols.map((columnSymbol, columnIndex) => {
                  const value = sortedMatrix[rowIndex]?.[columnIndex] ?? 0;
                  return (
                    <button
                      key={`${rowSymbol}-${columnSymbol}`}
                      type="button"
                      className="correlation-cell"
                      title={`${rowSymbol} vs ${columnSymbol}: ${value.toFixed(2)}`}
                      style={{ background: corrColor(value), color: corrTextColor(value) }}
                      onClick={() => setSortBy(columnSymbol)}
                    >
                      {value.toFixed(2)}
                    </button>
                  );
                })}
              </Fragment>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const RRG_X_RANGE: [number, number] = [90, 110];
const RRG_Y_RANGE: [number, number] = [-10, 10];
// Tapered trail: line width/opacity fall off from the dot toward the tail,
// bucketed into a few traces per symbol (Plotly cannot vary width within one trace).
const TAPER_BUCKETS = 6;
// One animation frame advances the trail by one date; dots glide between dates.
const FRAME_MS = 200;

function RRG({ benchmark, setBenchmark, windowDays, setWindowDays, end, setEnd, frames }: {
  benchmark: string; setBenchmark: (s: string) => void;
  windowDays: number; setWindowDays: (n: number) => void;
  end: string; setEnd: (s: string) => void;
  frames: { date: string; points: { symbol: string; x: number; y: number; sector?: string | null }[] }[];
}) {
  const theme = plotlyTheme();
  const { t } = useI18n();
  const [idx, setIdx] = useState(0);
  // 0 = paused, 1 = playing forward, -1 = playing backward.
  const [playDir, setPlayDir] = useState<0 | 1 | -1>(0);
  const [tailDays, setTailDays] = useState(20);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [showHelp, setShowHelp] = useState(false);
  // Movement filter: when active, Show all / Hide all apply to matching symbols only.
  // Thresholds are quintile cut points (20/40/60/80 pct of the movement
  // distribution), so each option sits 20% of the symbols apart.
  const [movOp, setMovOp] = useState<"<" | ">">(">");
  const [movPct, setMovPct] = useState<number | null>(null);
  const initialized = useRef(false);
  const graphDivRef = useRef<any>(null);
  const framesRef = useRef(frames);
  framesRef.current = frames;
  const hiddenRef = useRef(hidden);
  hiddenRef.current = hidden;
  const idxRef = useRef(idx);
  const applyIdx = (v: number) => { idxRef.current = v; setIdx(v); };

  useEffect(() => {
    if (idx >= frames.length) applyIdx(Math.max(0, frames.length - 1));
  }, [frames.length, idx]);
  // Jump to the latest frame when data first arrives so the trail is visible.
  useEffect(() => {
    if (!initialized.current && frames.length > 0) {
      initialized.current = true;
      applyIdx(frames.length - 1);
    }
  }, [frames.length]);
  // Play: advance one date per FRAME_MS via rAF, forward or backward; while
  // the trails step per date, the dots are restyled every screen frame to
  // positions interpolated between the two dates, so they glide instead of
  // jumping. The axis scale is intentionally untouched while playing.
  useEffect(() => {
    if (playDir === 0) return;
    const dir = playDir;
    let raf = 0;
    let last = performance.now();
    let pos = idxRef.current;
    let started = false;
    const step = (now: number) => {
      const fr = framesRef.current;
      const n = fr.length;
      if (n > 1) {
        if (!started) {
          // Backward playback from the first frame wraps around to the end.
          if (dir === -1 && pos <= 0) pos = n - 1;
          started = true;
        }
        pos += dir * (now - last) / FRAME_MS;
        if (pos >= n - 1) pos = 0;
        if (pos < 0) pos = n - 1;
        last = now;
        const i = Math.floor(pos);
        if (i !== idxRef.current) applyIdx(i);
        const gd = graphDivRef.current;
        const f0 = fr[i];
        const f1 = fr[i + 1];
        if (gd && gd.data && gd.data.length && f0 && f1) {
          const traceIdx = gd.data.length - 1;
          const trace = gd.data[traceIdx];
          if (trace && trace.mode === "markers+text") {
            const frac = pos - i;
            const xs: number[] = [];
            const ys: number[] = [];
            for (const p of f0.points) {
              if (hiddenRef.current.has(p.symbol)) continue;
              const q = f1.points.find((z: any) => z.symbol === p.symbol);
              if (!q) continue;
              xs.push(p.x + (q.x - p.x) * frac);
              ys.push(p.y + (q.y - p.y) * frac);
            }
            if (xs.length) Plotly.restyle(gd, { x: [xs], y: [ys] }, [traceIdx]);
          }
        }
      }
      last = now;
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playDir]);

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

  const sortedSectors = useMemo(() => {
    return Object.keys(symbolsBySector).sort((a, b) => {
      if (a === "Unknown") return 1;
      if (b === "Unknown") return -1;
      return a.localeCompare(b);
    });
  }, [symbolsBySector]);

  // Movement per symbol: total path length travelled on the chart over the frames.
  const movements = useMemo(() => {
    let prev: Record<string, { x: number; y: number }> = {};
    const dist: Record<string, number> = {};
    for (const fr of frames) {
      const cur: Record<string, { x: number; y: number }> = {};
      for (const p of fr.points) cur[p.symbol] = { x: p.x, y: p.y };
      for (const s of Object.keys(cur)) {
        const a = prev[s];
        if (a) dist[s] = (dist[s] || 0) + Math.hypot(cur[s].x - a.x, cur[s].y - a.y);
      }
      prev = cur;
    }
    return dist;
  }, [frames]);

  const MOV_PCTS = [0.2, 0.4, 0.6, 0.8];
  // Quintile cut points of the movement distribution (20% of symbols per band).
  const movQuantiles = useMemo(() => {
    const vals = allSymbols.map((s) => movements[s] || 0).sort((a, b) => a - b);
    if (!vals.length) return [];
    return MOV_PCTS.map((p) => vals[Math.min(vals.length - 1, Math.round((vals.length - 1) * p))]);
  }, [allSymbols, movements]);
  const movNum = movPct == null ? NaN : movQuantiles[MOV_PCTS.indexOf(movPct)];
  const movActive = movPct != null && isFinite(movNum);
  const movMatched = useMemo(() => {
    if (!movActive) return null;
    const out = new Set<string>();
    for (const s of allSymbols) {
      const m = movements[s] || 0;
      if (movOp === "<" ? m < movNum : m > movNum) out.add(s);
    }
    return out;
  }, [movActive, movOp, movNum, allSymbols, movements]);

  function toggleSym(s: string) {
    const next = new Set(hidden);
    if (next.has(s)) next.delete(s); else next.add(s);
    setHidden(next);
  }

  function toggleSector(syms: string[]) {
    const anyVisible = syms.some((s) => !hidden.has(s));
    const next = new Set(hidden);
    for (const s of syms) {
      if (anyVisible) next.add(s); else next.delete(s);
    }
    setHidden(next);
  }

  function setAllVisible(visible: boolean) {
    if (movMatched) {
      const next = new Set(hidden);
      for (const s of movMatched) {
        if (visible) next.delete(s); else next.add(s);
      }
      setHidden(next);
      return;
    }
    setHidden(visible ? new Set() : new Set(allSymbols));
  }

  const colorOf = (s: string) => {
    const sector = sectorBySymbol[s] || "Unknown";
    return sectorColor(s, sector, symbolsBySector[sector] || allSymbols);
  };

  // Tapered trail segments: drawn back from the dot, newest (fatter, brighter) on top.
  const tailTraces = useMemo(() => {
    if (!frames.length || tailDays < 1) return [];
    const out: any[] = [];
    for (const sym of allSymbols) {
      if (hidden.has(sym)) continue;
      const color = colorOf(sym);
      // Only the tail window matters; scanning all frames per symbol is too
      // slow to redo on every animation tick.
      const start = Math.max(0, idx - tailDays);
      const pts: ([number, number] | null)[] = [];
      for (let i = start; i <= idx; i++) {
        const p = frames[i].points.find((q) => q.symbol === sym);
        pts.push(p ? [p.x, p.y] : null);
      }
      const buckets = Array.from({ length: TAPER_BUCKETS },
        () => ({ xs: [] as (number | null)[], ys: [] as (number | null)[] }));
      for (let j = 1; j < pts.length; j++) {
        const a = pts[j - 1];
        const b = pts[j];
        if (!a || !b) continue;
        const k = idx - (start + j); // 0 = segment adjacent to the dot
        const t01 = Math.max(0, 1 - (k + 1) / (tailDays + 1));
        const bi = Math.min(TAPER_BUCKETS - 1, Math.floor((1 - t01) * TAPER_BUCKETS));
        const bucket = buckets[bi];
        if (bucket.xs.length) { bucket.xs.push(null); bucket.ys.push(null); }
        bucket.xs.push(a[0], b[0]);
        bucket.ys.push(a[1], b[1]);
      }
      buckets.forEach((bucket, b) => {
        if (!bucket.xs.length) return;
        const t01 = 1 - (b + 0.5) / TAPER_BUCKETS;
        out.push({
          type: "scatter", mode: "lines",
          x: bucket.xs, y: bucket.ys,
          line: { color: hexToRgba(color, 0.10 + 0.85 * t01), width: 0.6 + 3.0 * t01 },
          showlegend: false, hoverinfo: "skip",
        });
      });
    }
    return out;
  }, [frames, idx, tailDays, hidden, allSymbols, sectorBySymbol, symbolsBySector]);

  const visiblePoints = (f?.points ?? []).filter((p) => !hidden.has(p.symbol));

  // Axes span the biggest movement of every visible symbol across the whole
  // loaded period, kept symmetric around the center (100, 0) like the
  // dashboard RRG. Deliberately independent of the play position, so the
  // scale never shifts during playback; it only updates when the frames
  // change (date / benchmark / window) or the symbol selection changes.
  const bounds = useMemo(() => {
    let xLo = Infinity, xHi = -Infinity, yLo = Infinity, yHi = -Infinity;
    for (const fr of frames) {
      for (const p of fr.points) {
        if (hidden.has(p.symbol)) continue;
        if (p.x < xLo) xLo = p.x;
        if (p.x > xHi) xHi = p.x;
        if (p.y < yLo) yLo = p.y;
        if (p.y > yHi) yHi = p.y;
      }
    }
    if (!isFinite(xLo)) return { xMin: RRG_X_RANGE[0], xMax: RRG_X_RANGE[1],
                                 yMin: RRG_Y_RANGE[0], yMax: RRG_Y_RANGE[1] };
    const spanX = Math.max(Math.abs(xHi - 100), Math.abs(100 - xLo), 0.6) * 1.10;
    const spanY = Math.max(Math.abs(yHi), Math.abs(yLo), 0.6) * 1.10;
    return { xMin: 100 - spanX, xMax: 100 + spanX, yMin: -spanY, yMax: spanY };
  }, [frames, hidden]);

  return (
    <>
      <div className="filters">
        <label>{t("viz.benchmark")}:&nbsp;<input value={benchmark}
                          onChange={(e) => setBenchmark(e.target.value.toUpperCase())} /></label>
        <label>{t("viz.window")}:&nbsp;<input type="number" value={windowDays} min={20} max={252}
                          onChange={(e) => setWindowDays(parseInt(e.target.value || "60", 10))} /></label>
        <label>{t("viz.tail")}:&nbsp;<input type="number" value={tailDays} min={0} max={120}
                          onChange={(e) => setTailDays(parseInt(e.target.value || "0", 10))} /></label>
        <button type="button" className={`rrg-play-btn${playDir === -1 ? " active" : ""}`}
                title={playDir === -1 ? t("viz.pause") : t("viz.play_back")}
                aria-label={playDir === -1 ? t("viz.pause") : t("viz.play_back")}
                onClick={() => setPlayDir(playDir === -1 ? 0 : -1)}>
          {playDir === -1 ? "❚❚" : "◀"}
        </button>
        <button type="button" className={`rrg-play-btn${playDir === 1 ? " active" : ""}`}
                title={playDir === 1 ? t("viz.pause") : t("viz.play")}
                aria-label={playDir === 1 ? t("viz.pause") : t("viz.play")}
                onClick={() => setPlayDir(playDir === 1 ? 0 : 1)}>
          {playDir === 1 ? "❚❚" : "▶"}
        </button>
        <input type="range" className="rrg-range" min={0} max={Math.max(0, frames.length - 1)} value={idx}
               onChange={(e) => applyIdx(parseInt(e.target.value, 10))}
               style={{ flex: 1, minWidth: 200 }} />
        <label>{t("viz.end")}:&nbsp;<input type="date" value={end}
                          onChange={(e) => setEnd(e.target.value)} /></label>
        <button type="button" className="rrg-help-btn" title={t("viz.help_title")}
                aria-label={t("viz.help_title")}
                onClick={() => setShowHelp(true)}>?</button>
      </div>

      {showHelp && (
        <div className="rrg-help-overlay" onClick={() => setShowHelp(false)}>
          <div className="rrg-help-dialog" onClick={(e) => e.stopPropagation()} role="dialog"
               aria-label={t("viz.help_title")}>
            <div className="rrg-help-head">
              <h3>{t("viz.help_title")}</h3>
              <button type="button" className="rrg-help-close" aria-label="Close"
                      onClick={() => setShowHelp(false)}>×</button>
            </div>
            <div className="rrg-help-body">
              <p><b>What it plots.</b> Each symbol is compared against the benchmark
              (default SPY). The x-axis, <i>RS-Ratio</i>, is relative strength
              normalized so that <b>100</b> means "moving exactly with the benchmark" —
              further right = stronger. The y-axis, <i>RS-Momentum</i>, is how fast that
              strength is changing — <b>0</b> means flat, higher = improving.</p>
              <p><b>The four quadrants.</b> A symbol in the <b style={{ color: theme.pos }}>Leading</b> quadrant
              (top right) is outperforming the benchmark and still improving.
              <b style={{ color: "#f0a020" }}> Weakening</b> (bottom right) is outperforming but losing steam.
              <b style={{ color: theme.neg }}> Lagging</b> (bottom left) is underperforming and still getting worse.
              <b style={{ color: theme.accent }}> Improving</b> (top left) is underperforming but turning up.</p>
              <p><b>How symbols move.</b> Watch for clockwise rotation:
              Lagging → Improving → Leading → Weakening → back to Lagging. Entering
              <b> Leading</b> from <b>Improving</b> is the classic buy-side signal; dropping
              from <b>Leading</b> into <b>Weakening</b> warns that leadership is fading.
              Symbols hugging the center (100, 0) are just tracking the benchmark;
              distance from the center is the size of the out- or underperformance.</p>
              <p><b>Dots and tails.</b> Each dot is one symbol's position on the date
              shown at the top of the chart; its tail shows where it came from over the
              last <i>Trail</i> days — bright and thick near the dot, fading into the past.
              Long straight tails mean a steady trend; loops and hooks mean the symbol
              changed direction.</p>
              <p><b>Controls.</b> <i>Benchmark</i> sets what everything is measured against.
              <i> Window</i> is the lookback used to compute the metrics. <i>Trail</i> sets
              the tail length. <i>Play</i> animates day by day (dots glide; the scale stays
              fixed). The <i>End</i> date picks where the animation ends. The toggle card
              below the chart shows or hides symbols, whole sectors, or everything.</p>
            </div>
          </div>
        </div>
      )}

      <div className="card">
        <Plot
          data={[
            ...tailTraces,
            {
              type: "scatter", mode: "markers+text",
              x: visiblePoints.map((p) => p.x),
              y: visiblePoints.map((p) => p.y),
              text: visiblePoints.map((p) => p.symbol),
              textposition: "middle right",
              textfont: { size: 11, color: theme.font.color, weight: 700 },
              marker: {
                size: 12,
                color: visiblePoints.map((p) => colorOf(p.symbol)),
                line: { color: theme.plot_bgcolor, width: 2 },
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
                     zerolinecolor: "#666", range: [bounds.xMin, bounds.xMax] },
            yaxis: { title: "RS-Momentum", gridcolor: theme.yaxis_gridcolor, zeroline: true,
                     zerolinecolor: "#666", range: [bounds.yMin, bounds.yMax] },
            shapes: [
              { type: "rect", x0: 100, x1: bounds.xMax, y0: 0, y1: bounds.yMax, fillcolor: theme.pos, opacity: 0.08, line: { width: 0 } },
              { type: "rect", x0: 100, x1: bounds.xMax, y0: bounds.yMin, y1: 0, fillcolor: "#f0a020", opacity: 0.08, line: { width: 0 } },
              { type: "rect", x0: bounds.xMin, x1: 100, y0: bounds.yMin, y1: 0, fillcolor: theme.neg, opacity: 0.08, line: { width: 0 } },
              { type: "rect", x0: bounds.xMin, x1: 100, y0: 0, y1: bounds.yMax, fillcolor: theme.accent, opacity: 0.08, line: { width: 0 } },
            ],
            annotations: [
              { x: 100 + (bounds.xMax - 100) / 2, y: bounds.yMax * 0.92, text: "Leading", showarrow: false, font: { color: theme.pos } },
              { x: 100 + (bounds.xMax - 100) / 2, y: bounds.yMin * 0.92, text: "Weakening", showarrow: false, font: { color: "#f0a020" } },
              { x: bounds.xMin + (100 - bounds.xMin) / 2, y: bounds.yMin * 0.92, text: "Lagging", showarrow: false, font: { color: theme.neg } },
              { x: bounds.xMin + (100 - bounds.xMin) / 2, y: bounds.yMax * 0.92, text: "Improving", showarrow: false, font: { color: theme.accent } },
              // Current frame date (the play bar now hosts the end-date picker).
              ...(f ? [{
                xref: "paper", yref: "paper", x: 0.5, y: 1, xanchor: "center", yanchor: "top",
                text: f.date, showarrow: false,
                font: { size: 12, color: theme.font.color },
              }] : []),
            ],
          }}
          style={{ width: "100%" }} useResizeHandler
          onInitialized={(_figure, graphDiv) => { graphDivRef.current = graphDiv; }}
        />
      </div>

      <div className={`card${movMatched ? " rrg-filtering" : ""}`}>
        <div className="filters" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>{t("viz.toggle_symbols")}</h3>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <span className="rrg-mov-filter" title={t("viz.movement_hint")}>
              <label>{t("viz.movement")}</label>
              <button type="button" className="rrg-mov-op" title={t("viz.movement_hint")}
                      onClick={() => setMovOp(movOp === "<" ? ">" : "<")}>
                {movOp === "<" ? "<" : ">"}
              </button>
              <select value={movPct ?? ""} style={{ width: 110 }}
                      onChange={(e) => setMovPct(e.target.value === "" ? null : Number(e.target.value))}>
                <option value="">—</option>
                {movQuantiles.map((v, i) => (
                  <option key={i} value={MOV_PCTS[i]}>
                    {`${Math.round(MOV_PCTS[i] * 100)}% (${v.toFixed(1)})`}
                  </option>
                ))}
              </select>
              {movMatched && (
                <span className="rrg-mov-count">{movMatched.size}/{allSymbols.length}</span>
              )}
            </span>
            <span>
              <button onClick={() => setAllVisible(true)}>{t("viz.show_all")}</button>
              <button onClick={() => setAllVisible(false)} style={{ marginLeft: 8 }}>{t("viz.hide_all")}</button>
            </span>
          </span>
        </div>
        <div className="rrg-sector-list">
          {sortedSectors.map((sector) => {
            const syms = symbolsBySector[sector];
            const visibleCount = syms.filter((s) => !hidden.has(s)).length;
            const color = SECTOR_COLORS[sector] || stableSectorColor(sector);
            return (
              <div key={sector}
                   className={`rrg-sector-group${visibleCount === 0 ? " off" : ""}`}
                   style={{ borderColor: color }}>
                <label className="rrg-sector-header">
                  <input
                    type="checkbox"
                    checked={visibleCount === syms.length}
                    ref={(el) => { if (el) el.indeterminate = visibleCount > 0 && visibleCount < syms.length; }}
                    onChange={() => toggleSector(syms)}
                  />
                  <span className="rrg-sector-chip" style={{ background: color }} />
                  {sector}
                </label>
                <div className="checkbox-row">
                  {syms.map((s) => (
                    <label key={s} className={movMatched?.has(s) ? "mov-match" : ""}
                           style={{ borderBottom: `2px solid ${colorOf(s)}` }}>
                      <input type="checkbox" checked={!hidden.has(s)} onChange={() => toggleSym(s)} />
                      {s}
                    </label>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </>
  );
}
