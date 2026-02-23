import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchAssetValue } from "../api";
import { money, number } from "../format";
import type { AssetGroup, Currency } from "../types";

type Props = {
  displayCurrency: Currency;
  privacy: boolean;
};

export function AssetValueTab({ displayCurrency, privacy }: Props) {
  const { t } = useTranslation();
  const [groupBy, setGroupBy] = useState<"total" | "account" | "institution">("account");
  const [groups, setGroups] = useState<AssetGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [filterText, setFilterText] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchAssetValue({ displayCurrency, groupBy })
      .then((items) => {
        if (!cancelled) {
          setGroups(items);
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
  }, [displayCurrency, groupBy]);

  const filtered = useMemo(() => {
    if (!filterText.trim()) {
      return groups;
    }
    const needle = filterText.trim().toLowerCase();
    return groups.filter((g) => g.group_key.toLowerCase().includes(needle));
  }, [groups, filterText]);

  const grandTotal = useMemo(
    () => filtered.reduce((acc, item) => acc + (item.total_market_value_display || 0), 0),
    [filtered]
  );

  return (
    <section className="tab-panel">
      <div className="filter-row">
        <label className="inline-select">
          <span>{t("groupBy")}</span>
          <select value={groupBy} onChange={(e) => setGroupBy(e.target.value as "total" | "account" | "institution")}>
            <option value="total">{t("total")}</option>
            <option value="account">{t("account")}</option>
            <option value="institution">{t("institution")}</option>
          </select>
        </label>
        <input placeholder={`${t("filter")}...`} value={filterText} onChange={(e) => setFilterText(e.target.value)} />
      </div>

      <article className="stat-card stat-wide">
        <h4>{t("total")}</h4>
        <p>{money(grandTotal, displayCurrency, privacy)}</p>
      </article>

      {loading ? <p>Loading...</p> : null}
      {error ? <p className="error-text">{error}</p> : null}
      {!loading && filtered.length === 0 ? <p>{t("noData")}</p> : null}

      {filtered.map((group) => (
        <div key={group.group_key} className="group-card">
          <header>
            <h3>{group.group_key}</h3>
            <p>{money(group.total_market_value_display, displayCurrency, privacy)}</p>
          </header>
          {[
            { key: "stock", title: t("stocks"), rows: group.positions.filter((p) => p.asset_type !== "option") },
            { key: "option", title: t("options"), rows: group.positions.filter((p) => p.asset_type === "option") }
          ].map((section) => {
            if (section.rows.length === 0) {
              return null;
            }
            const isOptionSection = section.key === "option";
            return (
              <div key={`${group.group_key}-${section.key}`} className="asset-section">
                <header className="asset-section-header">
                  <h4>{section.title}</h4>
                  <p>
                    {money(
                      section.rows.reduce((acc, row) => acc + (row.market_value_display || 0), 0),
                      displayCurrency,
                      privacy
                    )}
                  </p>
                </header>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>{t("institution")}</th>
                        <th>{t("account")}</th>
                        <th>{t("symbol")}</th>
                        {isOptionSection ? <th>{t("optionType")}</th> : null}
                        {isOptionSection ? <th>{t("strike")}</th> : null}
                        {isOptionSection ? <th>{t("expiry")}</th> : null}
                        <th>{t("quantity")}</th>
                        <th>Native</th>
                        <th>{displayCurrency}</th>
                        <th>Cost</th>
                        <th>Unrealized</th>
                      </tr>
                    </thead>
                    <tbody>
                      {section.rows.map((p) => (
                        <tr
                          key={`${group.group_key}-${section.key}-${p.account_id}-${p.symbol}-${p.option_root || ""}-${p.put_call || ""}-${p.strike || ""}-${p.expiry || ""}`}
                        >
                          <td>{p.institution}</td>
                          <td>{p.account_id}</td>
                          <td>{isOptionSection ? p.option_root || p.symbol : p.symbol}</td>
                          {isOptionSection ? <td>{p.put_call || "-"}</td> : null}
                          {isOptionSection ? <td>{number(p.strike)}</td> : null}
                          {isOptionSection ? <td>{p.expiry || "-"}</td> : null}
                          <td>{number(p.quantity)}</td>
                          <td>{money(p.market_value_native, p.currency_native, privacy)}</td>
                          <td>{money(p.market_value_display, displayCurrency, privacy)}</td>
                          <td>{money(p.cost_native, p.currency_native, privacy)}</td>
                          <td>{money(p.unrealized_pl_native, p.currency_native, privacy)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </section>
  );
}
