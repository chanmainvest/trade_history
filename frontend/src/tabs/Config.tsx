import { useEffect, useState } from "react";
import { usePortfolio } from "../portfolio";
import { api, Portfolio, StatementExplain, StatementRow, StatementUploadResult } from "../api";
import { LANGS, useI18n } from "../i18n";

function makeId(name: string, existing: string[]): string {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "p";
  let id = base;
  let i = 1;
  while (existing.includes(id)) id = `${base}-${++i}`;
  return id;
}

export default function Config() {
  const { config, accounts, activePortfolio, saveConfig, savePortfolios } = usePortfolio();
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
      <h2>{t("nav.config")}</h2>

      <div className="card">
        <h3>Display</h3>
        <div className="filters">
          <label>{t("cfg.theme")}:&nbsp;
            <select value={config.theme}
                    onChange={(e) => saveConfig({ theme: e.target.value as "dark" | "light" })}>
              <option value="dark">{t("nav.theme.dark")}</option>
              <option value="light">{t("nav.theme.light")}</option>
            </select>
          </label>
          <label>
            <input type="checkbox" checked={config.hide_money}
                   onChange={(e) => saveConfig({ hide_money: e.target.checked })} />
            &nbsp;{t("cfg.hide_money")}
          </label>
          <label>{t("cfg.active_portfolio")}:&nbsp;
            <select value={config.active_portfolio}
                    onChange={(e) => saveConfig({ active_portfolio: e.target.value })}>
              {config.portfolios.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </label>
          <label>{t("cfg.language")}:&nbsp;
            <select value={config.language || "en"}
                    onChange={(e) => saveConfig({ language: e.target.value as any })}>
              {LANGS.map((l) => (
                <option key={l.code} value={l.code}>{l.flag} {l.label}</option>
              ))}
            </select>
          </label>
        </div>
        <p className="muted">
          Active portfolio: <strong>{activePortfolio?.name}</strong>
          {activePortfolio && activePortfolio.account_ids.length === 0 && " — includes all accounts"}
        </p>
      </div>

      <div className="card">
        <h3>{t("cfg.portfolios")}</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          A portfolio is a named set of accounts. Use them to view one
          family member at a time, or combine everything into a single
          household view. The <code>All accounts</code> portfolio is built
          in and cannot be deleted.
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

      <div className="card">
        <h3>LLM API keys <span className="muted">(beta)</span></h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Stored locally in <code>data/config.json</code>, never sent off
          your machine except when you explicitly ask the parser-draft
          workflow to call a provider.
        </p>
        <ApiKeyRow label="OpenAI" field="openai" />
        <ApiKeyRow label="Anthropic" field="anthropic" />
        <ApiKeyRow label="Google" field="google" />
      </div>

      <div className="card">
        <h3>Upload a statement PDF <span className="muted">(beta)</span></h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Uploads are saved under <code>Statements/uploads/</code>, parsed
          into a review preview, then imported only after you choose the
          target institution folder.
        </p>
        <UploadWorkflow />
      </div>

      <div className="card">
        <h3>How each statement was extracted</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Pick a statement to see PDF text lines annotated with the parsed
          transactions, holdings, and quarantined rows that came from them.
        </p>
        <StatementExplainer />
      </div>

      <div className="card">
        <h3>Transfer and position reconciliation</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Ingest runs this automatically. You can rebuild the links here
          after manual data edits.
        </p>
        <ReconciliationPanel />
      </div>
    </>
  );
}

function ApiKeyRow({ label, field }: { label: string; field: "openai" | "anthropic" | "google" }) {
  const { config, saveConfig } = usePortfolio();
  const [v, setV] = useState<string>("");
  const current = (config?.llm_keys?.[field] ?? "") as string;
  const masked = current ? current.slice(0, 6) + "…" + current.slice(-4) : "(unset)";
  return (
    <div className="filters" style={{ marginBottom: 8 }}>
      <span style={{ minWidth: 90 }}>{label}:</span>
      <span className="muted" style={{ minWidth: 200 }}>{masked}</span>
      <input type="password" value={v} placeholder="paste key here"
             onChange={(e) => setV(e.target.value)} style={{ minWidth: 240 }} />
      <button onClick={async () => {
        const next = { ...(config?.llm_keys ?? {}), [field]: v };
        await saveConfig({ llm_keys: next });
        setV("");
      }}>Save</button>
      {current && (
        <button onClick={async () => {
          const next = { ...(config?.llm_keys ?? {}), [field]: "" };
          await saveConfig({ llm_keys: next });
        }}>Clear</button>
      )}
    </div>
  );
}

function UploadWorkflow() {
  const [result, setResult] = useState<StatementUploadResult | null>(null);
  const [institution, setInstitution] = useState("uploads");
  const [force, setForce] = useState(false);
  const [provider, setProvider] = useState("openai");
  const [sendToProvider, setSendToProvider] = useState(false);
  const [actionResult, setActionResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    setActionResult(null);
    try {
      const data = await api.uploadStatement(file);
      setResult(data);
      setInstitution(defaultInstitution(data));
    } catch (err: any) {
      setActionResult({ error: String(err) });
    } finally {
      setBusy(false);
    }
  }
  async function importIt() {
    if (!result) return;
    setBusy(true);
    try {
      setActionResult(await api.importStatement({
        sha256: result.sha256,
        institution_folder: institution,
        force,
      }));
    } catch (err: any) {
      setActionResult({ error: String(err) });
    } finally {
      setBusy(false);
    }
  }
  async function draftIt() {
    if (!result) return;
    setBusy(true);
    try {
      setActionResult(await api.draftParser({
        sha256: result.sha256,
        institution_folder: institution,
        provider,
        send_to_provider: sendToProvider,
      }));
    } catch (err: any) {
      setActionResult({ error: String(err) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="workflow-stack">
      <div className="filters">
        <input type="file" accept="application/pdf" onChange={onUpload} disabled={busy} />
        {busy && <span className="muted">Working…</span>}
      </div>
      {result && (
        <>
          <div className="kv">
            <span className="tag">{result.review.parse_status}</span>
            <span className="tag">{result.review.parser?.name || "no parser"}</span>
            <span className="tag">{result.path}</span>
            {result.already_ingested && <span className="tag accent">already ingested</span>}
          </div>
          {result.review.errors.length > 0 && (
            <p className="inline-status status-error">{result.review.errors.join("; ")}</p>
          )}
          <table>
            <thead>
              <tr><th>Account</th><th>Period</th><th className="num">Txns</th><th className="num">Positions</th><th className="num">Quarantine</th></tr>
            </thead>
            <tbody>
              {result.review.statements.map((statement) => (
                <tr key={statement.index}>
                  <td>{statement.account.account_number}</td>
                  <td>{statement.period_start} to {statement.period_end}</td>
                  <td className="num">{statement.transactions}</td>
                  <td className="num">{statement.positions}</td>
                  <td className="num">{statement.quarantine}</td>
                </tr>
              ))}
              {result.review.statements.length === 0 && (
                <tr><td colSpan={5} className="muted">No statements parsed from this PDF.</td></tr>
              )}
            </tbody>
          </table>
          <div className="filters">
            <label>Institution folder:&nbsp;
              <select value={institution} onChange={(e) => setInstitution(e.target.value)}>
                <option value="uploads">uploads</option>
                {result.institutions.map((item) => (
                  <option key={item.folder} value={item.folder}>{item.folder} ({item.code})</option>
                ))}
              </select>
            </label>
            <label>
              <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
              &nbsp;Force re-import
            </label>
            <button onClick={importIt} disabled={busy || !result.review.parser}>Import parsed statements</button>
          </div>
          <div className="filters">
            <label>Draft provider:&nbsp;
              <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="google">Google</option>
              </select>
            </label>
            <label>
              <input type="checkbox" checked={sendToProvider} onChange={(e) => setSendToProvider(e.target.checked)} />
              &nbsp;Send prompt to provider
            </label>
            <button onClick={draftIt} disabled={busy}>Create parser draft</button>
          </div>
        </>
      )}
      {actionResult && (
        <pre className="json-output">{JSON.stringify(actionResult, null, 2)}</pre>
      )}
    </div>
  );
}

function defaultInstitution(result: StatementUploadResult): string {
  const parserName = result.review.parser?.name;
  if (parserName === "td") return "TD Webbroker";
  if (parserName === "rbc") return "RBC Invest Direct";
  if (parserName === "hsbc") return "HSBC direct invest";
  if (parserName === "cibc") return "CIBC Invest Direct";
  return "uploads";
}

function StatementExplainer() {
  const [statements, setStatements] = useState<StatementRow[]>([]);
  const [id, setId] = useState("");
  const [data, setData] = useState<StatementExplain | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.statements(500).then((response) => {
      setStatements(response.rows);
      if (response.rows.length > 0) setId(String(response.rows[0].statement_id));
    }).catch((err) => setError(String(err)));
  }, []);
  async function fetchIt() {
    if (!id) return;
    setError("");
    try {
      setData(await api.statementExplain(Number(id)));
    } catch (err: any) {
      setError(String(err));
    }
  }
  return (
    <>
      <div className="filters">
        <select value={id} onChange={(e) => setId(e.target.value)} style={{ minWidth: 360 }}>
          {statements.map((statement) => (
            <option key={statement.statement_id} value={statement.statement_id}>
              #{statement.statement_id} {statement.period_end} {statement.institution_code} {statement.account_number}
            </option>
          ))}
        </select>
        <button onClick={fetchIt}>Fetch</button>
      </div>
      {error && <p className="inline-status status-error">{error}</p>}
      {data && (
        <div className="explainer-grid">
          <div className="explainer-source">
            {data.pages.length === 0 && <p className="muted">Source PDF text is not available on disk.</p>}
            {data.pages.map((page) => (
              <div key={page.page_number} className="explainer-page">
                <h4>Page {page.page_number}</h4>
                {page.lines.map((line) => (
                  <div key={`${page.page_number}-${line.line_number}`}
                       className={line.refs.length ? "explainer-line has-ref" : "explainer-line"}>
                    <span className="line-no">{line.line_number}</span>
                    <span className="line-text">{line.text || " "}</span>
                    {line.refs.length > 0 && (
                      <span className="line-refs">{line.refs.map((ref) => `${ref.kind} ${ref.id}`).join(", ")}</span>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
          <div className="explainer-parsed">
            <h4>Transactions</h4>
            <MiniRows rows={data.transactions} cols={["trade_date", "txn_type", "symbol", "quantity", "net_amount", "currency"]} />
            <h4>Positions</h4>
            <MiniRows rows={data.positions} cols={["as_of_date", "symbol", "quantity", "market_value", "currency"]} />
            <h4>Cash</h4>
            <MiniRows rows={data.cash_balances} cols={["as_of_date", "currency", "closing_balance"]} />
            <h4>Quarantine</h4>
            <MiniRows rows={data.quarantine} cols={["reason", "raw_line"]} />
          </div>
        </div>
      )}
    </>
  );
}

function MiniRows({ rows, cols }: { rows: any[]; cols: string[] }) {
  if (rows.length === 0) return <p className="muted">None</p>;
  return (
    <div className="table-scroll mini-table-scroll">
      <table>
        <thead><tr>{cols.map((col) => <th key={col}>{col}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>{cols.map((col) => <td key={col}>{String(row[col] ?? "")}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ReconciliationPanel() {
  const [summary, setSummary] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  async function refresh() {
    setSummary(await api.reconciliationSummary());
  }
  async function rebuild() {
    setBusy(true);
    try {
      setSummary(await api.rebuildReconciliation());
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => { refresh().catch(() => undefined); }, []);
  return (
    <>
      <div className="filters">
        <button onClick={rebuild} disabled={busy}>Rebuild links</button>
        <button onClick={refresh} disabled={busy}>Refresh summary</button>
      </div>
      {summary && <pre className="json-output">{JSON.stringify(summary, null, 2)}</pre>}
    </>
  );
}
