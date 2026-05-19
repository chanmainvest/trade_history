import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, TxnRow } from "../api";
import { SmartSelect } from "../SmartSelect";
import { usePortfolio } from "../portfolio";
import { useI18n } from "../i18n";

function fmtNum(n: number | null | undefined, dec = 2) {
  if (n === null || n === undefined) return "";
  return n.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

const MONEY_THRESHOLDS = [
  { value: 0, label: "Any amount" },
  { value: 100, label: "≥ $100" },
  { value: 1_000, label: "≥ $1k" },
  { value: 10_000, label: "≥ $10k" },
  { value: 100_000, label: "≥ $100k" },
  { value: 1_000_000, label: "≥ $1M" },
];

export default function Transactions() {
  const { activeAccountIds, accounts } = usePortfolio();
  const { t } = useI18n();

  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [institutions, setInstitutions] = useState<string[]>([]);
  const [accountIds, setAccountIds] = useState<string[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [minAbs, setMinAbs] = useState(0);

  const accountsQ = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const symbolsQ = useQuery({ queryKey: ["symbols"], queryFn: api.symbols });
  const typesQ = useQuery({ queryKey: ["txn-types"], queryFn: api.txnTypes });

  // If a portfolio is set AND the user hasn't manually picked accounts,
  // restrict the query to the portfolio's accounts.
  const effectiveAcctIds = accountIds.length > 0
    ? accountIds
    : activeAccountIds.length > 0
      ? activeAccountIds.map(String)
      : [];

  const txnsQ = useQuery({
    queryKey: ["txns", start, end, institutions, effectiveAcctIds, symbols, types, minAbs],
    queryFn: () =>
      api.transactions({
        start, end,
        institution: institutions,
        account_id: effectiveAcctIds,
        symbol: symbols,
        txn_type: types,
        min_abs_amount: minAbs > 0 ? minAbs : undefined,
        limit: 10_000,
      }),
  });

  const instOptions = useMemo(() => {
    const set = new Set<string>();
    for (const a of accountsQ.data?.rows ?? []) set.add(a.institution_code);
    return Array.from(set).sort().map((c) => ({ value: c, label: c }));
  }, [accountsQ.data]);

  const acctOptions = useMemo(() => {
    return (accountsQ.data?.rows ?? []).map((a) => ({
      value: String(a.account_id),
      label: `${a.institution_code} • ${a.account_number}`,
      hint: a.base_currency + (a.nickname ? ` · ${a.nickname}` : ""),
    }));
  }, [accountsQ.data]);

  const symOptions = useMemo(() => {
    const seen = new Set<string>();
    const out: { value: string; label: string; hint?: string }[] = [];
    for (const r of symbolsQ.data?.rows ?? []) {
      if (seen.has(r.symbol)) continue;
      seen.add(r.symbol);
      out.push({ value: r.symbol, label: r.symbol, hint: `${r.asset_type} · ${r.currency}` });
    }
    return out.sort((a, b) => a.label.localeCompare(b.label));
  }, [symbolsQ.data]);

  const typeOptions = useMemo(() =>
    (typesQ.data?.rows ?? []).map((t) => ({ value: t, label: t })),
    [typesQ.data]);

  const acctById = useMemo(() => {
    const m: Record<number, string> = {};
    for (const a of accounts) m[a.account_id] = a.account_number;
    return m;
  }, [accounts]);

  return (
    <>
      <h2>{t("nav.transactions")}</h2>
      <div className="filters">
        <input type="date" value={start} onChange={(e) => setStart(e.target.value)} title={t("f.start")} />
        <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} title={t("f.end")} />
        <SmartSelect label={t("f.institution")} options={instOptions} value={institutions} onChange={setInstitutions} />
        <SmartSelect label={t("f.account")} options={acctOptions} value={accountIds} onChange={setAccountIds} />
        <SmartSelect label={t("f.symbol")} options={symOptions} value={symbols} onChange={setSymbols} />
        <SmartSelect label={t("f.type")} options={typeOptions} value={types} onChange={setTypes} />
        <label>{t("f.min_abs_amount")}:&nbsp;
          <select value={minAbs} onChange={(e) => setMinAbs(parseFloat(e.target.value))}>
            {MONEY_THRESHOLDS.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </label>
        <span className="muted">{txnsQ.data?.count ?? 0} rows</span>
        {activeAccountIds.length > 0 && accountIds.length === 0 && (
          <span className="tag accent">portfolio filter on</span>
        )}
      </div>

      {txnsQ.isLoading && <p className="muted">Loading transactions…</p>}
      {txnsQ.isError && (
        <div className="card status-error">
          Transactions API is not responding. Start the backend with
          <code> uv run ledger serve </code> and reload this page.
        </div>
      )}
      {!txnsQ.isLoading && !txnsQ.isError && (txnsQ.data?.rows.length ?? 0) === 0 && (
        <div className="card muted">
          No transactions match the current filters.
        </div>
      )}

      <div className="card" style={{ overflow: "auto", maxHeight: "calc(100vh - 220px)" }}>
        <table>
          <thead>
            <tr>
              <th>{t("th.date")}</th><th>{t("f.institution")}</th><th>{t("th.account")}</th><th>{t("th.type")}</th>
              <th>{t("th.symbol")}</th><th>Option</th>
              <th className="num">{t("th.quantity")}</th><th className="num">{t("th.price")}</th>
              <th className="num">{t("th.amount")}</th><th>{t("th.currency")}</th><th>{t("th.description")}</th>
            </tr>
          </thead>
          <tbody>
            {txnsQ.data?.rows.map((t: TxnRow) => (
              <tr key={t.transaction_id}>
                <td>{t.trade_date}</td>
                <td>{t.institution_code}</td>
                <td>{acctById[t.account_id] || t.account_number}</td>
                <td>{t.txn_type}</td>
                <td>{t.symbol ? <Link to={`/research/${t.symbol}`}>{t.symbol}</Link> : ""}</td>
                <td>{t.option_type ? `${t.option_type} ${fmtNum(t.option_strike, 2)} ${t.option_expiry || ""}` : ""}</td>
                <td className="num">{fmtNum(t.quantity, 0)}</td>
                <td className="num">{fmtNum(t.price)}</td>
                <td className={"num " + ((t.net_amount ?? 0) < 0 ? "neg" : "pos")}>{fmtNum(t.net_amount)}</td>
                <td>{t.currency}</td>
                <td style={{ maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis" }}>
                  {t.description}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
