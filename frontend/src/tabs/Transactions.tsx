import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, TxnRow } from "../api";

function fmtNum(n: number | null, dec = 2) {
  if (n === null || n === undefined) return "";
  return n.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

export default function Transactions() {
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [institution, setInstitution] = useState("");
  const [accountId, setAccountId] = useState("");
  const [symbol, setSymbol] = useState("");
  const [txnType, setTxnType] = useState("");

  const accountsQ = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const symbolsQ = useQuery({ queryKey: ["symbols"], queryFn: api.symbols });

  const txnsQ = useQuery({
    queryKey: ["txns", start, end, institution, accountId, symbol, txnType],
    queryFn: () =>
      api.transactions({
        start, end, institution,
        account_id: accountId || undefined,
        symbol, txn_type: txnType, limit: 5000,
      }),
  });

  const TXN_TYPES = [
    "buy", "sell", "dividend", "distribution", "interest_income",
    "tax_withholding", "deposit", "withdrawal", "transfer_in", "transfer_out",
    "journal", "fee", "commission",
    "option_buy_to_open", "option_sell_to_open",
    "option_buy_to_close", "option_sell_to_close",
    "option_assignment", "option_exercise", "option_expiration",
    "return_of_capital", "split", "adjustment",
  ];

  return (
    <>
      <h2>Transactions</h2>
      <div className="filters">
        <input type="date" value={start} onChange={(e) => setStart(e.target.value)} placeholder="Start" />
        <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} placeholder="End" />
        <select value={institution} onChange={(e) => setInstitution(e.target.value)}>
          <option value="">All institutions</option>
          {[...new Set(accountsQ.data?.rows.map((a) => a.institution_code))].map((c) =>
            <option key={c} value={c}>{c}</option>
          )}
        </select>
        <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
          <option value="">All accounts</option>
          {accountsQ.data?.rows.map((a) =>
            <option key={a.account_id} value={a.account_id}>
              {a.institution_code} • {a.account_number} ({a.base_currency})
            </option>
          )}
        </select>
        <input list="symbols" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="Symbol" />
        <datalist id="symbols">
          {symbolsQ.data?.rows.map((s) => <option key={s.symbol + s.currency} value={s.symbol} />)}
        </datalist>
        <select value={txnType} onChange={(e) => setTxnType(e.target.value)}>
          <option value="">All types</option>
          {TXN_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <span style={{ alignSelf: "center", color: "var(--fg-dim)" }}>
          {txnsQ.data?.count ?? 0} rows
        </span>
      </div>

      <div className="card" style={{ overflow: "auto", maxHeight: "calc(100vh - 220px)" }}>
        <table>
          <thead>
            <tr>
              <th>Date</th><th>Institution</th><th>Account</th><th>Type</th>
              <th>Symbol</th><th>Option</th>
              <th className="num">Qty</th><th className="num">Price</th>
              <th className="num">Net</th><th>Ccy</th><th>Description</th>
            </tr>
          </thead>
          <tbody>
            {txnsQ.data?.rows.map((t: TxnRow) => (
              <tr key={t.transaction_id}>
                <td>{t.trade_date}</td>
                <td>{t.institution_code}</td>
                <td>{t.account_number}</td>
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
