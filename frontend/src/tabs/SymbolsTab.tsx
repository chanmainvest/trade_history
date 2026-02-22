import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  deleteSymbolOverride,
  fetchSymbolCatalog,
  refreshSectorMetadata,
  saveSymbolOverride
} from "../api";
import type { SymbolCatalogRow } from "../types";

export function SymbolsTab() {
  const { t } = useTranslation();
  const [rows, setRows] = useState<SymbolCatalogRow[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [marketSymbol, setMarketSymbol] = useState("");
  const [sectorOverride, setSectorOverride] = useState("");
  const [notes, setNotes] = useState("");
  const [statusText, setStatusText] = useState("");

  function loadSymbols(currentQuery?: string) {
    setLoading(true);
    setError(null);
    fetchSymbolCatalog(currentQuery)
      .then((items) => setRows(items))
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    loadSymbols();
  }, []);

  const selectedRow = useMemo(
    () => rows.find((row) => row.symbol_norm === selected) || null,
    [rows, selected]
  );

  useEffect(() => {
    if (!selectedRow) {
      return;
    }
    setMarketSymbol(selectedRow.override_market_symbol || selectedRow.resolved_market_symbol);
    setSectorOverride(selectedRow.override_sector || "");
    setNotes(selectedRow.override_notes || "");
  }, [selectedRow]);

  function handleSearch() {
    loadSymbols(query);
  }

  async function handleSave() {
    if (!selectedRow) {
      return;
    }
    setSaving(true);
    setStatusText("");
    setError(null);
    try {
      await saveSymbolOverride(selectedRow.symbol_norm, {
        market_symbol: marketSymbol,
        sector_override: sectorOverride || null,
        notes: notes || null,
        is_active: true
      });
      setStatusText(t("saved"));
      loadSymbols(query);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleClearOverride() {
    if (!selectedRow) {
      return;
    }
    setSaving(true);
    setStatusText("");
    setError(null);
    try {
      await deleteSymbolOverride(selectedRow.symbol_norm);
      setStatusText(t("overrideRemoved"));
      loadSymbols(query);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleRefreshSectors() {
    setSaving(true);
    setStatusText("");
    setError(null);
    try {
      const target = selected ? [selected] : [];
      const result = await refreshSectorMetadata(target);
      setStatusText(
        `${t("sectorRefreshDone")}: ${result.metadata_rows} / ${result.sectors_updated}`
      );
      loadSymbols(query);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="tab-panel">
      <div className="filter-row">
        <input
          placeholder={`${t("symbol")}...`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="button" onClick={handleSearch}>
          {t("filter")}
        </button>
        <button type="button" onClick={handleRefreshSectors} disabled={saving}>
          {t("refreshSectors")}
        </button>
      </div>

      {loading ? <p>Loading...</p> : null}
      {error ? <p className="error-text">{error}</p> : null}
      {statusText ? <p>{statusText}</p> : null}

      <div className="symbol-admin-grid">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("symbol")}</th>
                <th>{t("events")}</th>
                <th>{t("marketSymbol")}</th>
                <th>{t("sectorTab")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.symbol_norm}
                  className={selected === row.symbol_norm ? "row-selected" : ""}
                  onClick={() => setSelected(row.symbol_norm)}
                >
                  <td>{row.symbol_norm}</td>
                  <td>{row.event_count}</td>
                  <td>{row.resolved_market_symbol}</td>
                  <td>{row.override_sector || row.provider_sector || row.instrument_sector || "Unknown"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <aside className="symbol-editor">
          <h3>{t("symbolOverride")}</h3>
          {!selectedRow ? <p>{t("selectSymbol")}</p> : null}
          {selectedRow ? (
            <>
              <p>
                <strong>{selectedRow.symbol_norm}</strong>
              </p>
              <label>
                <span>{t("marketSymbol")}</span>
                <input value={marketSymbol} onChange={(e) => setMarketSymbol(e.target.value)} />
              </label>
              <label>
                <span>{t("sectorOverride")}</span>
                <input value={sectorOverride} onChange={(e) => setSectorOverride(e.target.value)} />
              </label>
              <label>
                <span>{t("notes")}</span>
                <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={4} />
              </label>
              <div className="symbol-editor-actions">
                <button type="button" onClick={handleSave} disabled={saving}>
                  {t("save")}
                </button>
                <button type="button" onClick={handleClearOverride} disabled={saving}>
                  {t("removeOverride")}
                </button>
              </div>
            </>
          ) : null}
        </aside>
      </div>
    </section>
  );
}

