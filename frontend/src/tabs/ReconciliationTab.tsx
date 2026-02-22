import { Fragment, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchMonthlyReconciliation, fetchMonthlyReconciliationSnapshotLines } from "../api";
import { money, number } from "../format";
import type { Currency, MonthlyReconciliationRow, MonthlyReconciliationSnapshotLine } from "../types";

type Props = {
  displayCurrency: Currency;
  privacy: boolean;
};

type SortKey = "month" | "institution" | "account_id" | "txn_event_count" | "reconciliation_gap_display";

export function ReconciliationTab({ displayCurrency, privacy }: Props) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<MonthlyReconciliationRow[]>([]);
  const [detailsByKey, setDetailsByKey] = useState<Record<string, MonthlyReconciliationSnapshotLine[]>>({});
  const [detailLoadingByKey, setDetailLoadingByKey] = useState<Record<string, boolean>>({});
  const [detailErrorByKey, setDetailErrorByKey] = useState<Record<string, string | undefined>>({});
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [institutionFilter, setInstitutionFilter] = useState("");
  const [accountFilter, setAccountFilter] = useState("");
  const [sortBy, setSortBy] = useState<SortKey>("month");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");

  function rowKey(row: MonthlyReconciliationRow): string {
    return `${row.month}|${row.institution}|${row.account_id}|${row.currency_native}`;
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setExpandedKey(null);
    setDetailsByKey({});
    setDetailLoadingByKey({});
    setDetailErrorByKey({});
    fetchMonthlyReconciliation({
      displayCurrency,
      institution: institutionFilter || undefined,
      accountId: accountFilter || undefined
    })
      .then((items) => {
        if (!cancelled) {
          setRows(items);
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
  }, [displayCurrency, institutionFilter, accountFilter]);

  const sortedRows = useMemo(() => {
    const copy = [...rows];
    const direction = sortOrder === "asc" ? 1 : -1;
    copy.sort((a, b) => {
      const av = a[sortBy] ?? "";
      const bv = b[sortBy] ?? "";
      if (typeof av === "number" && typeof bv === "number") {
        return (av - bv) * direction;
      }
      return String(av).localeCompare(String(bv)) * direction;
    });
    return copy;
  }, [rows, sortBy, sortOrder]);

  const warningCount = useMemo(() => sortedRows.filter((r) => r.status === "warning").length, [sortedRows]);

  function handleSort(column: SortKey) {
    if (sortBy === column) {
      setSortOrder((prev) => (prev === "asc" ? "desc" : "asc"));
      return;
    }
    setSortBy(column);
    setSortOrder("asc");
  }

  async function toggleDetails(row: MonthlyReconciliationRow): Promise<void> {
    const key = rowKey(row);
    if (expandedKey === key) {
      setExpandedKey(null);
      return;
    }
    setExpandedKey(key);
    if (detailsByKey[key] || detailLoadingByKey[key]) {
      return;
    }

    setDetailLoadingByKey((prev) => ({ ...prev, [key]: true }));
    setDetailErrorByKey((prev) => ({ ...prev, [key]: undefined }));
    try {
      const items = await fetchMonthlyReconciliationSnapshotLines({
        month: row.month,
        accountId: row.account_id,
        currencyNative: row.currency_native,
        displayCurrency,
        institution: row.institution
      });
      setDetailsByKey((prev) => ({ ...prev, [key]: items }));
    } catch (err) {
      setDetailErrorByKey((prev) => ({ ...prev, [key]: String(err) }));
    } finally {
      setDetailLoadingByKey((prev) => ({ ...prev, [key]: false }));
    }
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
      </div>

      <div className="stat-cards">
        <article className="stat-card">
          <h4>{t("total")}</h4>
          <p>{number(sortedRows.length)}</p>
        </article>
        <article className="stat-card">
          <h4>{t("reconciliationWarnings")}</h4>
          <p>{number(warningCount)}</p>
        </article>
      </div>

      {loading ? <p>Loading...</p> : null}
      {error ? <p className="error-text">{error}</p> : null}
      {!loading && sortedRows.length === 0 ? <p>{t("noData")}</p> : null}

      {sortedRows.length > 0 ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>
                  <button type="button" onClick={() => handleSort("month")}>
                    {t("month")}
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
                <th>{t("currency")}</th>
                <th>
                  <button type="button" onClick={() => handleSort("txn_event_count")}>
                    {t("events")}
                  </button>
                </th>
                <th>{t("txnNetCash")}</th>
                <th>{t("statementOpeningCash")}</th>
                <th>{t("derivedClosingCash")}</th>
                <th>{t("statementClosingCash")}</th>
                <th>
                  <button type="button" onClick={() => handleSort("reconciliation_gap_display")}>
                    {t("reconciliationGap")}
                  </button>
                </th>
                <th>{t("statementPortfolio")}</th>
                <th>{t("status")}</th>
                <th>{t("details")}</th>
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((row) => {
                const key = rowKey(row);
                const detailRows = detailsByKey[key] || [];
                const detailLoading = detailLoadingByKey[key];
                const detailError = detailErrorByKey[key];
                const expanded = expandedKey === key;
                return (
                  <Fragment key={key}>
                    <tr key={key}>
                      <td>{row.month}</td>
                      <td>{row.institution}</td>
                      <td>{row.account_id}</td>
                      <td>{row.currency_native}</td>
                      <td>{number(row.txn_event_count)}</td>
                      <td>{money(row.txn_net_cash_flow_display, displayCurrency, privacy)}</td>
                      <td>{money(row.statement_cash_opening_display, displayCurrency, privacy)}</td>
                      <td>{money(row.derived_cash_closing_display, displayCurrency, privacy)}</td>
                      <td>{money(row.statement_cash_closing_display, displayCurrency, privacy)}</td>
                      <td>{money(row.reconciliation_gap_display, displayCurrency, privacy)}</td>
                      <td>{money(row.statement_portfolio_display, displayCurrency, privacy)}</td>
                      <td>{row.status}</td>
                      <td>
                        <button type="button" className="table-action-btn" onClick={() => toggleDetails(row)}>
                          {expanded ? t("hideDetails") : t("showDetails")}
                        </button>
                      </td>
                    </tr>
                    {expanded ? (
                      <tr className="recon-detail-row">
                        <td colSpan={13}>
                          {detailLoading ? <p>Loading...</p> : null}
                          {detailError ? <p className="error-text">{detailError}</p> : null}
                          {!detailLoading && !detailError && detailRows.length === 0 ? <p>{t("noData")}</p> : null}
                          {detailRows.length > 0 ? (
                            <div className="recon-detail-block">
                              <h4>{t("snapshotLines")}</h4>
                              <div className="table-wrap">
                                <table className="recon-detail-table">
                                  <thead>
                                    <tr>
                                      <th>{t("metric")}</th>
                                      <th>{t("snapshotDate")}</th>
                                      <th>{t("value")}</th>
                                      <th>{t("sourceFile")}</th>
                                      <th>{t("sourceLineRef")}</th>
                                      <th>{t("rawLine")}</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {detailRows.map((item) => (
                                      <tr key={item.id}>
                                        <td>{item.metric_code}</td>
                                        <td>{item.snapshot_date || "-"}</td>
                                        <td>{money(item.value_display, displayCurrency, privacy)}</td>
                                        <td>{item.file_name || item.file_path}</td>
                                        <td>{item.source_line_ref || "-"}</td>
                                        <td className="raw-line-cell">{item.raw_line || "-"}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            </div>
                          ) : null}
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
