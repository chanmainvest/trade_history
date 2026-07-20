export const API_BASE = "/api";

async function getJSON<T>(path: string, params?: Record<string, any>): Promise<T> {
  const url = new URL(API_BASE + path, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null) continue;
      if (Array.isArray(v)) {
        if (v.length > 0) url.searchParams.set(k, v.join(","));
        continue;
      }
      if (v === "") continue;
      url.searchParams.set(k, String(v));
    }
  }
  const r = await fetch(url.toString());
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function putJSON<T>(path: string, body: any): Promise<T> {
  const url = new URL(API_BASE + path, window.location.origin);
  const r = await fetch(url.toString(), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export const api = {
  transactions: (p: Record<string, any> = {}) =>
    getJSON<{ rows: TxnRow[]; count: number; total_count: number; has_more: boolean }>("/transactions", p),
  accounts: () => getJSON<{ rows: Account[] }>("/transactions/accounts"),
  symbols: () => getJSON<{ rows: SymbolRow[] }>("/transactions/symbols"),
  txnTypes: () => getJSON<{ rows: string[] }>("/transactions/txn-types"),
  latestDate: () => getJSON<{ latest: string | null }>("/transactions/latest-date"),

  monthlySnapshot: (p: { month_end?: string; account_id?: number[] } = {}) =>
    getJSON<{ as_of_date: string; rows: HoldingRow[]; totals?: SnapshotTotals }>("/monthly/snapshot", p),
  monthlyDiff: (p: { a: string; b: string; account_id?: number[] }) =>
    getJSON<{ a: string; b: string; rows: DiffRow[] }>("/monthly/diff", p),

  perfTotal: (p: Record<string, any> = {}) =>
    getJSON<{ rows: { as_of_date: string; market_value: number; currency: string }[]; forward_fill_max_days: number | null }>(
      "/performance/total", p),
  perfCash: (p: Record<string, any> = {}) =>
    getJSON<{ rows: { as_of_date: string; currency: string; closing_balance: number }[] }>(
      "/performance/cash", p),

  prices: (symbol: string, freq: "D" | "W" | "M" = "D") =>
    getJSON<{ symbol: string; requested_symbol: string; symbols: string[]; ticker_changes: { from_symbol: string; to_symbol: string; effective_date: string }[]; freq: string; rows: any[] }>(
      "/research/prices", { symbol, freq }),
  trades: (symbol: string) =>
    getJSON<{ symbol: string; requested_symbol: string; symbols: string[]; ticker_changes: { from_symbol: string; to_symbol: string; effective_date: string }[]; rows: any[] }>("/research/trades", { symbol }),
  financials: (symbol: string, period: "quarterly" | "annual" = "quarterly") =>
    getJSON<{ symbol: string; period: string; rows: any[] }>(
      "/research/financials", { symbol, period }),

  vizSector: (p: { month_end?: string; account_id?: number[]; period?: string } = {}) =>
    getJSON<{ as_of_date: string | null; period?: string; rows: { account_id: number; account_number: string; institution_code: string; institution_name: string; symbol: string; asset_type: string; currency: string; market_value: number; sector?: string | null; industry?: string | null; performance_pct?: number | null }[] }>(
      "/viz/holdings_by_sector", p),
  vizCorrelation: (p: { start: string; end: string; account_id?: number[] }) =>
    getJSON<{ symbols: string[]; matrix: number[][]; profiles?: Record<string, { sector?: string | null; industry?: string | null }> }>("/viz/correlation", p),
  vizRRG: (p: { benchmark?: string; window_days?: number; start?: string; end?: string; account_id?: number[] } = {}) =>
    getJSON<{ frames: { date: string; points: { symbol: string; x: number; y: number; sector?: string | null }[] }[] }>(
      "/viz/rrg", p),

  config: () => getJSON<UserConfig>("/config"),
  saveConfig: (cfg: Partial<UserConfig>) => putJSON<UserConfig>("/config", cfg),

  statements: (limit = 200) => getJSON<{ rows: StatementRow[] }>("/statements", { limit }),
  statementBoxes: (statementId: number) => getJSON<StatementBoxes>(`/statements/${statementId}/boxes`),
  statementPdfUrl: (statementId: number) => `${API_BASE}/statements/${statementId}/pdf`,
};

export type TxnRow = {
  row_kind: "transaction" | "initial_position";
  row_id: string;
  transaction_id: number | null;
  initial_id: number | null;
  statement_id: number | null;
  trade_date: string;
  settle_date: string | null;
  txn_type: string;
  quantity: number | null;
  price: number | null;
  net_amount: number | null;
  currency: string | null;
  description: string | null;
  account_id: number;
  account_number: string;
  account_type: string | null;
  nickname: string | null;
  institution_code: string;
  institution_name: string;
  symbol: string | null;
  related_symbol: string | null;
  asset_type: string | null;
  option_expiry: string | null;
  option_strike: number | null;
  option_type: string | null;
  source_ref: SourceRef | null;
};

export type Account = {
  account_id: number;
  account_number: string;
  account_type: string | null;
  nickname: string | null;
  base_currency: string;
  institution_code: string;
  institution_name: string;
};

export type SymbolRow = { symbol: string; asset_type: string; currency: string };

export type HoldingRow = {
  as_of_date: string;
  account_id: number;
  account_number: string;
  nickname: string | null;
  institution_code: string;
  institution_name: string;
  instrument_key: string;
  holding_key: string;
  symbol: string;
  ticker_symbols: string[];
  asset_type: string;
  currency: string;
  option_expiry: string | null;
  option_strike: number | null;
  option_type: string | null;
  quantity: number;
  source_ref: SourceRef | null;
  provenance: {
    type: "reported_row" | "checkpoint_plus_movements" | "observed_incomplete" | "unavailable";
    checkpoint: SourceRef | null;
    movements: SourceRef[];
  };
  avg_cost: number | null;
  book_value: number | null;
  market_price: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
  checkpoint_date: string | null;
  checkpoint_statement_id: number | null;
  checkpoint_snapshot_set_id: number | null;
  is_reported: boolean;
  is_reconstructed: boolean;
  holding_state: "reported" | "reconstructed" | "incomplete";
  reconciliation_status: string | null;
  reconciliation_reason: string | null;
  price_date: string | null;
  price_status: string;
  quality_warnings: string[];
};

export type SnapshotTotals = {
  native?: Record<string, number>;
  combined?: {
    CAD?: number;
    USD?: number;
    usd_cad?: number;
    cad_usd?: number;
    cad_fx_date?: string | null;
    usd_fx_date?: string | null;
  };
};

export type DiffRow = {
  holding_key: string;
  account_id: number;
  account_number: string;
  institution_code: string;
  instrument_key: string;
  symbol: string;
  asset_type: string;
  currency: string;
  option_expiry: string | null;
  option_strike: number | null;
  option_type: string | null;
  qty_a: number;
  qty_b: number;
  qty_delta: number;
  mv_a: number | null;
  mv_b: number | null;
};

export type Portfolio = {
  id: string;
  name: string;
  account_ids: number[];
};

export type UserConfig = {
  portfolios: Portfolio[];
  active_portfolio: string;
  theme: "dark" | "light";
  hide_money: boolean;
  show_source_links: boolean;
  /** UI language: en, zh-HK (HK Trad.), zh-TW (TW Trad.), zh-CN (Simplified). */
  language?: "en" | "zh-HK" | "zh-TW" | "zh-CN";
};

export type StatementRow = {
  statement_id: number;
  period_start: string;
  period_end: string;
  statement_type: string;
  account_id: number;
  account_number: string;
  account_type: string | null;
  nickname: string | null;
  institution_code: string;
  institution_name: string;
  relpath: string;
  parser_name: string | null;
  parser_version: string | null;
  parse_status: string | null;
  active_ingestion_run_id: number | null;
  active_run_status: string | null;
  contract_version: string | null;
  run_schema_version: number | null;
  scope_count: number;
  complete_scope_count: number;
  incomplete_scope_count: number;
  unresolved_identity_count: number;
  quarantine_count: number;
  reconciliation_result_count: number;
  unreconciled_count: number;
  incomplete_reconciliation_count: number;
  quality_flags: StatementQualityFlag[];
};

export type SourceRef = {
  statement_id: number;
  kind: "transaction" | "position" | "cash" | "summary" | "scope_issue" | "quarantine";
  id: number;
  checkpoint?: boolean;
  geometry_status: string;
  page_numbers: number[];
  linkable: boolean;
};

export type StatementQualityFlag = "unresolved" | "incomplete" | "unreconciled";

export type StatementQuality = {
  scope_count: number;
  complete_scope_count: number;
  incomplete_scope_count: number;
  unresolved_identity_count: number;
  quarantine_count: number;
  reconciliation_result_count: number;
  unreconciled_count: number;
  incomplete_reconciliation_count: number;
  quality_flags: StatementQualityFlag[];
};

export type StatementScope = {
  snapshot_set_id: number;
  currency: string;
  section_type: "positions" | "cash" | "summary";
  scope_key: string;
  completeness: "complete" | "partial" | "absent" | "unknown";
  validation_status: string;
  reported_total: number | null;
  raw_line: string | null;
};

export type StatementReconciliation = {
  reconciliation_id: number;
  kind: "position" | "cash" | "statement_total" | "transfer";
  check_type: string | null;
  reason_code: string | null;
  currency: string;
  status: string;
  reason: string | null;
  residual: number | null;
  tolerance: number;
  opening_value: number | null;
  summed_deltas: number | null;
  expected_close: number | null;
  reported_close: number | null;
  snapshot_set_id: number | null;
  prior_snapshot_set_id: number | null;
  prior_checkpoint: string | null;
  current_checkpoint: string | null;
  instrument_id: number | null;
  symbol: string | null;
  instrument_name: string | null;
  component_count: number;
  section_type: string | null;
  scope_key: string | null;
};

/** A matched reference linking a PDF line box to a parsed item. */
export type BoxRef = {
  kind: "transaction" | "position" | "cash" | "summary" | "scope_issue" | "quarantine";
  id: number;
  label: string;
  match_status?: string;
  match_method?: string | null;
  match_confidence?: number | null;
};

/** A PDF text line with its bounding box (PDF user-space, top-left origin). */
export type LineBox = {
  /** [x0, top, x1, bottom] in PDF points. */
  bbox: [number, number, number, number];
  text: string;
  refs: BoxRef[];
};

/** Per-page line boxes response from /statements/{id}/boxes. */
export type StatementBoxes = {
  statement: {
    statement_id: number;
    account_id: number;
    period_start: string;
    period_end: string;
    source_file_id: number;
    quality: StatementQuality;
  };
  source_file: {
    relpath: string;
    sha256: string | null;
    parser_name: string | null;
    parser_version: string | null;
    parse_status: string | null;
    active_ingestion_run_id: number | null;
    active_run_status: string | null;
    contract_version: string | null;
    run_schema_version: number | null;
  } | null;
  page_numbers: number[];
  pages: {
    page_number: number;
    width: number;
    height: number;
    lines: LineBox[];
    boxes: EvidenceBox[];
  }[];
  transactions: any[];
  positions: any[];
  cash_balances: (any & { cash_balance_id: number; raw_line: string | null })[];
  summary_totals: StatementScope[];
  scopes: StatementScope[];
  scope_issues: StatementScopeIssue[];
  reconciliation_results: StatementReconciliation[];
  quarantine: any[];
};

export type EvidenceBox = {
  ref: BoxRef;
  evidence_id: number;
  rect: [number, number, number, number];
  ordinal: number;
  geometry_status: string;
  match_method: string | null;
  confidence: number | null;
};

export type StatementScopeIssue = {
  scope_issue_id: number;
  snapshot_set_id: number;
  issue_code: string;
  severity: string;
  detail: Record<string, unknown>;
  blocks_completeness: boolean;
  evidence_id: number | null;
  quarantine_id: number | null;
  currency: string;
  section_type: StatementScope["section_type"];
  scope_key: string;
  quarantine_reason: string | null;
  raw_text: string | null;
};
