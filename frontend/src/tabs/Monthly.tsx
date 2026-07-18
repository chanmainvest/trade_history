import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, HoldingRow } from "../api";
import { SourceLink } from "../SourceLink";
import { usePortfolio } from "../portfolio";
import { SmartSelect } from "../SmartSelect";
import { useI18n } from "../i18n";

function fmtNum(n: number | null | undefined, dec = 2) {
  if (n === null || n === undefined) return "";
  return n.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function interpolate(text: string, values: Record<string, string>): string {
  return Object.entries(values).reduce(
    (result, [key, value]) => result.replaceAll(`{${key}}`, value),
    text,
  );
}

function translatedStatus(t: (key: string) => string, status: string): string {
  const key = `quality.status.${status}`;
  const translated = t(key);
  return translated === key ? status.replaceAll("_", " ") : translated;
}

function translatedState(t: (key: string) => string, state: HoldingRow["holding_state"]): string {
  return t(`quality.state.${state}`);
}

function hasScopeWarning(row: HoldingRow): boolean {
  return row.holding_state === "incomplete" || row.quality_warnings.some((warning) =>
    warning.startsWith("incomplete_") || warning.startsWith("missing_complete_") ||
    warning.startsWith("duplicate_complete_"),
  );
}

function hasReconciliationWarning(row: HoldingRow): boolean {
  if (row.reconciliation_status && !["reconciled", "within_rounding", "not_applicable"].includes(row.reconciliation_status)) {
    return true;
  }
  return row.quality_warnings.some((warning) => warning.startsWith("reconciliation_"));
}

function hasPriceWarning(row: HoldingRow): boolean {
  return row.quality_warnings.some((warning) =>
    ["market_price_stale", "stale_checkpoint_price", "stale_checkpoint_value", "unpriced"].includes(warning),
  );
}

function HoldingQuality({ row }: { row: HoldingRow }) {
  const { t } = useI18n();
  const warnings: string[] = [];
  if (hasScopeWarning(row)) warnings.push(t("quality.warning.scope"));
  if (hasReconciliationWarning(row)) warnings.push(t("quality.warning.reconciliation"));
  if (hasPriceWarning(row)) warnings.push(t("quality.warning.price"));
  const title = [
    ...warnings,
    row.reconciliation_reason || "",
  ].filter(Boolean).join(" · ");
  return (
    <div className="monthly-quality" title={title}>
      <span className={`quality-tag state-${row.holding_state}`}>{translatedState(t, row.holding_state)}</span>
      {row.reconciliation_status ? (
        <span className={`quality-tag reconciliation-${row.reconciliation_status}`}>
          {translatedStatus(t, row.reconciliation_status)}
        </span>
      ) : <span className="quality-tag unavailable">{t("quality.unavailable")}</span>}
      {warnings.map((warning) => <span key={warning} className="quality-tag warning">{warning}</span>)}
    </div>
  );
}

type Col =
  | "institution_code" | "account_number" | "symbol" | "asset_type"
  | "quantity" | "market_price" | "market_value" | "unrealized_pnl" | "currency";

export default function Monthly() {
  const { activeAccountIds, config } = usePortfolio();
  const { t } = useI18n();
  const showSourceLinks = config?.show_source_links ?? true;

  const latestQ = useQuery({ queryKey: ["latest-date"], queryFn: api.latestDate });
  const latest = latestQ.data?.latest || "";

  // Default both dates to the most recent snapshot.
  const [b, setB] = useState<string>("");
  const [a, setA] = useState<string>("");
  const effectiveB = b || latest;
  const effectiveA = a || effectiveB;

  const [instFilter, setInstFilter] = useState<string[]>([]);
  const [acctFilter, setAcctFilter] = useState<string[]>([]);
  const [hideZero, setHideZero] = useState(true);
  const [sortCol, setSortCol] = useState<Col>("market_value");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const snapQ = useQuery({
    queryKey: ["snap", effectiveB, activeAccountIds],
    queryFn: () => api.monthlySnapshot({
      month_end: effectiveB,
      account_id: activeAccountIds.length > 0 ? activeAccountIds : undefined,
    }),
    enabled: !!effectiveB,
  });
  const diffQ = useQuery({
    queryKey: ["diff", effectiveA, effectiveB, activeAccountIds],
    queryFn: () => api.monthlyDiff({
      a: effectiveA, b: effectiveB,
      account_id: activeAccountIds.length > 0 ? activeAccountIds : undefined,
    }),
    enabled: !!effectiveA && !!effectiveB && effectiveA !== effectiveB,
  });

  // Local filters + sort
  const allRows = snapQ.data?.rows ?? [];
  const instOpts = useMemo(() => {
    const s = new Set<string>();
    for (const r of allRows) s.add(r.institution_code);
    return Array.from(s).sort().map((v) => ({ value: v, label: v }));
  }, [allRows]);
  const acctOpts = useMemo(() => {
    const s = new Map<string, string>();
    for (const r of allRows) {
      s.set(`${r.institution_code}::${r.account_number}`,
            `${r.institution_code} • ${r.account_number}`);
    }
    return Array.from(s.entries()).map(([v, l]) => ({ value: v, label: l }));
  }, [allRows]);

  const filtered = useMemo(() => {
    let rows = [...allRows];
    if (instFilter.length) rows = rows.filter((r) => instFilter.includes(r.institution_code));
    if (acctFilter.length) {
      rows = rows.filter((r) =>
        acctFilter.includes(`${r.institution_code}::${r.account_number}`));
    }
    if (hideZero) rows = rows.filter((r) => Math.abs(r.quantity) > 1e-9);
    rows.sort((x, y) => {
      const xv = (x as any)[sortCol];
      const yv = (y as any)[sortCol];
      if (xv == null && yv == null) return 0;
      if (xv == null) return 1;
      if (yv == null) return -1;
      if (typeof xv === "number" && typeof yv === "number") {
        return sortDir === "asc" ? xv - yv : yv - xv;
      }
      return sortDir === "asc"
        ? String(xv).localeCompare(String(yv))
        : String(yv).localeCompare(String(xv));
    });
    return rows;
  }, [allRows, instFilter, acctFilter, hideZero, sortCol, sortDir]);

  const totalsByCurrency: Record<string, number> = {};
  for (const r of filtered) {
    totalsByCurrency[r.currency] = (totalsByCurrency[r.currency] || 0) + (r.market_value || 0);
  }
  const snapshotTotals = snapQ.data?.totals;
  const fxTotals = snapshotTotals?.combined;
  const combinedTotals: { CAD?: number; USD?: number } = {};
  if (Object.keys(totalsByCurrency).length > 0) {
    const cad = totalsByCurrency.CAD || 0;
    const usd = totalsByCurrency.USD || 0;
    if (usd === 0 || fxTotals?.usd_cad !== undefined) {
      combinedTotals.CAD = cad + usd * (fxTotals?.usd_cad || 0);
    }
    if (cad === 0 || fxTotals?.cad_usd !== undefined) {
      combinedTotals.USD = usd + cad * (fxTotals?.cad_usd || 0);
    }
  }

  function toggleSort(c: Col) {
    if (c === sortCol) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortCol(c); setSortDir("desc"); }
  }
  function arrow(c: Col) { return c === sortCol ? (sortDir === "asc" ? " ▲" : " ▼") : ""; }

  // Diff key lookup so we can colour rows in the holdings table
  const diffMap = useMemo(() => {
    const m = new Map<string, number>();
    for (const d of diffQ.data?.rows ?? []) {
      m.set(d.holding_key, d.qty_delta);
    }
    return m;
  }, [diffQ.data]);

  return (
    <>
      <h2>{t("nav.monthly")}</h2>
      <div className="filters">
        <label>{t("f.as_of")}:&nbsp;
          <input type="date" value={effectiveB} onChange={(e) => setB(e.target.value)} />
        </label>
        <label>{t("f.compare_to")}:&nbsp;
          <input type="date" value={effectiveA} onChange={(e) => setA(e.target.value)} />
        </label>
        <button type="button" onClick={() => setA(effectiveB)} disabled={!effectiveB || effectiveA === effectiveB}>
          {t("monthly.sync_compare")}
        </button>
        <SmartSelect label={t("f.institution")} options={instOpts} value={instFilter} onChange={setInstFilter} />
        <SmartSelect label={t("f.account")} options={acctOpts} value={acctFilter} onChange={setAcctFilter} />
        <label><input type="checkbox" checked={hideZero}
                      onChange={(e) => setHideZero(e.target.checked)} />&nbsp;{t("monthly.hide_zero")}</label>
        <span className="muted">{filtered.length} {t("monthly.rows")}</span>
      </div>

      <div className="card">
        <h3>{t("monthly.totals_as_of")} {effectiveB || `(${t("monthly.no_data")})`}</h3>
        <div className="kv">
          {Object.entries(totalsByCurrency).map(([c, v]) => (
            <span key={c} className="tag accent"><strong>{c}</strong>&nbsp;{fmtNum(v)}</span>
          ))}
          {combinedTotals.CAD !== undefined && (
            <span className="tag"><strong>{t("monthly.total_cad")}</strong>&nbsp;{fmtNum(combinedTotals.CAD)}</span>
          )}
          {combinedTotals.USD !== undefined && (
            <span className="tag"><strong>{t("monthly.total_usd")}</strong>&nbsp;{fmtNum(combinedTotals.USD)}</span>
          )}
          {fxTotals?.usd_cad !== undefined && (
            <span className="tag muted">
              {interpolate(t("monthly.fx_usd_cad"), { rate: fmtNum(fxTotals.usd_cad) })}
              {fxTotals.cad_fx_date ? ` · ${interpolate(t("monthly.fx_date"), { date: fxTotals.cad_fx_date })}` : ""}
            </span>
          )}
          {fxTotals?.cad_usd !== undefined && (
            <span className="tag muted">
              {interpolate(t("monthly.fx_cad_usd"), { rate: fmtNum(fxTotals.cad_usd) })}
              {fxTotals.usd_fx_date ? ` · ${interpolate(t("monthly.fx_date"), { date: fxTotals.usd_fx_date })}` : ""}
            </span>
          )}
          {effectiveA !== effectiveB && (
            <span className="tag">{interpolate(t("monthly.diff_legend"), { date: effectiveA })}</span>
          )}
        </div>
      </div>

      <div className="card" style={{ overflow: "auto", maxHeight: "calc(100vh - 280px)" }}>
        <table>
          <thead>
            <tr>
              {showSourceLinks && <th aria-label={t("source.column")} />}
              <th onClick={() => toggleSort("institution_code")}>{t("f.institution")}{arrow("institution_code")}</th>
              <th onClick={() => toggleSort("account_number")}>{t("th.account")}{arrow("account_number")}</th>
              <th onClick={() => toggleSort("symbol")}>{t("th.symbol")}{arrow("symbol")}</th>
              <th onClick={() => toggleSort("asset_type")}>{t("th.type")}{arrow("asset_type")}</th>
              <th>{t("quality.checkpoint")}</th>
              <th>{t("verify.quality")}</th>
              <th className="num" onClick={() => toggleSort("quantity")}>{t("th.quantity")}{arrow("quantity")}</th>
              <th className="num" onClick={() => toggleSort("market_price")}>{t("th.price")}{arrow("market_price")}</th>
              <th className="num" onClick={() => toggleSort("market_value")}>{t("th.market_value")}{arrow("market_value")}</th>
              <th className="num">{t("th.delta")} {t("th.quantity")}</th>
              <th className="num" onClick={() => toggleSort("unrealized_pnl")}>P/L{arrow("unrealized_pnl")}</th>
              <th onClick={() => toggleSort("currency")}>{t("th.currency")}{arrow("currency")}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r: HoldingRow) => {
              const k = r.holding_key;
              const delta = diffMap.get(k);
              const showDelta = effectiveA !== effectiveB;
              const rowClass = showDelta && delta != null
                ? (delta > 0 ? "diff-add" : delta < 0 ? "diff-del" : "")
                : "";
              return (
                <tr key={r.holding_key} className={rowClass}>
                  {showSourceLinks && (
                    <td>
                      {r.source_ref ? (
                        <SourceLink
                          source={r.source_ref}
                          title={r.source_ref.checkpoint
                            ? t("source.open_checkpoint")
                            : t("source.open_position")}
                        />
                      ) : null}
                    </td>
                  )}
                  <td>{r.institution_code}</td>
                  <td>{r.account_number}</td>
                  <td>{r.asset_type === "cash" ? r.symbol : <Link to={`/research/${r.symbol}`}>{r.symbol}</Link>}</td>
                  <td>{r.asset_type}{r.option_type ? ` ${r.option_type} ${fmtNum(r.option_strike, 2)} ${r.option_expiry || ""}` : ""}</td>
                  <td>{r.checkpoint_date || t("quality.unavailable")}</td>
                  <td><HoldingQuality row={r} /></td>
                  <td className="num">{fmtNum(r.quantity, 0)}</td>
                  <td className="num">{fmtNum(r.market_price)}</td>
                  <td className="num">{fmtNum(r.market_value)}</td>
                  <td className={"num " + (delta == null ? "" : delta > 0 ? "pos" : delta < 0 ? "neg" : "")}>
                    {showDelta && delta != null && delta !== 0 ? fmtNum(delta, 0) : ""}
                  </td>
                  <td className={"num " + ((r.unrealized_pnl ?? 0) < 0 ? "neg" : "pos")}>{fmtNum(r.unrealized_pnl)}</td>
                  <td>{r.currency}</td>
                </tr>
              );
            })}
            {/* Rows present at A but missing at B (sold) */}
            {effectiveA !== effectiveB && (diffQ.data?.rows ?? []).filter((d) =>
              d.qty_b === 0 && d.qty_a !== 0).map((d) => (
              <tr key={`gone-${d.holding_key}`} className="diff-del">
                {showSourceLinks && <td />}
                <td>{d.institution_code}</td>
                <td>{d.account_number}</td>
                <td>{d.asset_type === "cash" ? d.symbol : <Link to={`/research/${d.symbol}`}>{d.symbol}</Link>}</td>
                <td>{d.asset_type}</td>
                <td></td>
                <td></td>
                <td className="num">{fmtNum(d.qty_a, 0)}</td>
                <td className="num"></td>
                <td className="num">{fmtNum(d.mv_a)}</td>
                <td className="num neg">{fmtNum(-d.qty_a, 0)}</td>
                <td className="num"></td>
                <td>{d.currency}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
