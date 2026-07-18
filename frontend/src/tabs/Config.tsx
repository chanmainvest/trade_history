import { useState } from "react";
import { usePortfolio } from "../portfolio";
import { Portfolio } from "../api";
import { useI18n } from "../i18n";

function makeId(name: string, existing: string[]): string {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "p";
  let id = base;
  let i = 1;
  while (existing.includes(id)) id = `${base}-${++i}`;
  return id;
}

export default function Config() {
  const { config, accounts, savePortfolios, saveConfig } = usePortfolio();
  const { t } = useI18n();
  const [newName, setNewName] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<Portfolio | null>(null);

  if (!config) return <p className="muted">Loading…</p>;

  function startEdit(p: Portfolio) {
    setEditing(p.id);
    setDraft({ ...p, account_ids: [...p.account_ids] });
  }
  function cancelEdit() { setEditing(null); setDraft(null); }
  async function commitEdit() {
    if (!draft || !config) return;
    const next = config.portfolios.map((p) => (p.id === draft.id ? draft : p));
    await savePortfolios(next);
    cancelEdit();
  }
  async function addPortfolio() {
    if (!config || !newName.trim()) return;
    const id = makeId(newName.trim(), config.portfolios.map((p) => p.id));
    const next = [...config.portfolios, { id, name: newName.trim(), account_ids: [] }];
    await savePortfolios(next);
    setNewName("");
  }
  async function removePortfolio(id: string) {
    if (!config) return;
    if (id === "all") return;          // protect the catch-all
    if (!confirm(`Delete portfolio "${id}"?`)) return;
    const next = config.portfolios.filter((p) => p.id !== id);
    const active = config.active_portfolio === id ? "all" : config.active_portfolio;
    await savePortfolios(next, active);
  }
  function toggleAcct(id: number) {
    if (!draft) return;
    setDraft({
      ...draft,
      account_ids: draft.account_ids.includes(id)
        ? draft.account_ids.filter((x) => x !== id)
        : [...draft.account_ids, id],
    });
  }

  return (
    <>
      <h2>Portfolios</h2>

      <div className="card">
        <h3>{t("settings.extraction_links")}</h3>
        <label>
          <input
            type="checkbox"
            checked={config.show_source_links}
            onChange={(event) => saveConfig({ show_source_links: event.target.checked })}
          />&nbsp;{t("settings.show_source_links")}
        </label>
        <p className="muted">{t("settings.show_source_links_help")}</p>
      </div>

      <div className="card">
        <p className="muted" style={{ marginTop: 0 }}>
          A portfolio is a named set of accounts. Use them to view one
          family member at a time, or combine everything into a single
          household view. The <code>All accounts</code> portfolio is built
          in and cannot be deleted. Pick the active portfolio from the
          dropdown in the top bar; theme, language, and hide-$ live there too.
        </p>

        <div className="filters">
          <input value={newName}
                 onChange={(e) => setNewName(e.target.value)}
                 placeholder="New portfolio name (e.g. Dad, Mom, Kids, Household)" />
          <button onClick={addPortfolio} disabled={!newName.trim()}>Add</button>
        </div>

        <table>
          <thead>
            <tr>
              <th>Name</th><th>ID</th><th className="num">Accounts</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {config.portfolios.map((p) => (
              <tr key={p.id}>
                <td>{p.name}</td>
                <td className="muted">{p.id}</td>
                <td className="num">
                  {p.account_ids.length === 0 ? "All" : p.account_ids.length}
                </td>
                <td>
                  <button onClick={() => startEdit(p)}>Edit</button>
                  {p.id !== "all" && (
                    <button className="danger" style={{ marginLeft: 4 }}
                            onClick={() => removePortfolio(p.id)}>Delete</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {draft && (
        <div className="card">
          <h3>Edit portfolio: {draft.name}</h3>
          <div className="filters">
            <label>Name:&nbsp;
              <input value={draft.name}
                     onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </label>
            <button className="active" onClick={commitEdit}>Save</button>
            <button onClick={cancelEdit}>Cancel</button>
          </div>
          {draft.id === "all" && (
            <p className="muted">
              The catch-all portfolio always selects every account; the
              account list below is informational only.
            </p>
          )}
          <table>
            <thead>
              <tr>
                <th></th><th>Institution</th><th>Account #</th>
                <th>Type</th><th>Nickname</th><th>Ccy</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.account_id}>
                  <td>
                    <input type="checkbox"
                           disabled={draft.id === "all"}
                           checked={draft.account_ids.includes(a.account_id)}
                           onChange={() => toggleAcct(a.account_id)} />
                  </td>
                  <td>{a.institution_code}</td>
                  <td>{a.account_number}</td>
                  <td>{a.account_type || ""}</td>
                  <td>{a.nickname || ""}</td>
                  <td>{a.base_currency}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
