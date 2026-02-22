import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchClosedPl, fetchTrades } from "../api";
import { money, number } from "../format";
import type { ClosedPlRow, Currency, TradeRow } from "../types";

type Props = {
  displayCurrency: Currency;
  privacy: boolean;
};

type SortKey = "trade_date" | "institution" | "account_id" | "symbol" | "quantity" | "price" | "gross_amount";

export function TradesTab({ displayCurrency, privacy }: Props) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<TradeRow[]>([]);
  const [closedRows, setClosedRows] = useState<ClosedPlRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [sortBy, setSortBy] = useState<SortKey>("trade_date");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [institutionFilter, setInstitutionFilter] = useState("");
  const [accountFilter, setAccountFilter] = useState("");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchTrades({
        sortBy,
        sortOrder,
        institution: institutionFilter || undefined,
        accountId: accountFilter || undefined,
        symbol: symbolFilter || undefined
      }),
      fetchClosedPl()
    ])
      .then(([tradeData, closed]) => {
        if (cancelled) {
          return;
        }
        setRows(tradeData);
        setClosedRows(closed);
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
  }, [sortBy, sortOrder, institutionFilter, accountFilter, symbolFilter]);

  const closedPlTotal = useMemo(() => {
    return closedRows.reduce((acc, item) => acc + (item.realized_pl_native || 0), 0);
  }, [closedRows]);

  function handleSort(column: SortKey) {
    if (sortBy === column) {
      setSortOrder((prev) => (prev === "asc" ? "desc" : "asc"));
      return;
    }
    setSortBy(column);
    setSortOrder("asc");
  }

  return (
    <section className="tab-panel">
      <div className="filter-row">
        <input
          placeholder={`${t("institution")}...`}
          value={institutionFilter}
          onChange={(e) => setInstitutionFilter(e.target.value)}
        />
        <input
          placeholder={`${t("account")}...`}
          value={accountFilter}
          onChange={(e) => setAccountFilter(e.target.value)}
        />
        <input placeholder={`${t("symbol")}...`} value={symbolFilter} onChange={(e) => setSymbolFilter(e.target.value)} />
      </div>

      <div className="stat-cards">
        <article className="stat-card">
          <h4>{t("pnlClosed")}</h4>
          <p>{money(closedPlTotal, displayCurrency, privacy)}</p>
        </article>
        <article className="stat-card">
          <h4>{t("total")}</h4>
          <p>{number(rows.length)}</p>
        </article>
      </div>

      {loading ? <p>Loading...</p> : null}
      {error ? <p className="error-text">{error}</p> : null}
      {!loading && rows.length === 0 ? <p>{t("noData")}</p> : null}

      {rows.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>
                  <button type="button" onClick={() => handleSort("trade_date")}>
                    Date
                  </button>
                </th>
                <th>
                  <button type="button" onClick={() => handleSort("institution")}>
                    {t("institution")}
                  </button>
                </th>
                <th>
                  <button type="button" onClick={() => handleSort("account_id")}>
                    {t("account")}
                  </button>
                </th>
                <th>
                  <button type="button" onClick={() => handleSort("symbol")}>
                    {t("symbol")}
                  </button>
                </th>
                <th>Type</th>
                <th>Side</th>
                <th>
                  <button type="button" onClick={() => handleSort("quantity")}>
                    {t("quantity")}
                  </button>
                </th>
                <th>
                  <button type="button" onClick={() => handleSort("price")}>
                    {t("price")}
                  </button>
                </th>
                <th>
                  <button type="button" onClick={() => handleSort("gross_amount")}>
                    {t("value")}
                  </button>
                </th>
                <th>{t("pnlClosed")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.event_id}>
                  <td>{row.trade_date}</td>
                  <td>{row.institution}</td>
                  <td>{row.account_id}</td>
                  <td>{row.symbol || "-"}</td>
                  <td>{row.event_type}</td>
                  <td>{row.side || "-"}</td>
                  <td>{number(row.quantity)}</td>
                  <td>{money(row.price, row.currency || displayCurrency, privacy)}</td>
                  <td>{money(row.gross_amount, row.currency || displayCurrency, privacy)}</td>
                  <td>{money(row.realized_pl_native, row.currency || displayCurrency, privacy)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

