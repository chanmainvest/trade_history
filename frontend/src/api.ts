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

async function postJSON<T>(path: string, body: any): Promise<T> {
  const url = new URL(API_BASE + path, window.location.origin);
  const r = await fetch(url.toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

async function postForm<T>(path: string, body: FormData): Promise<T> {
  const url = new URL(API_BASE + path, window.location.origin);
  const r = await fetch(url.toString(), { method: "POST", body });
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
    getJSON<{ rows: { as_of_date: string; market_value: number; currency: string }[] }>(
      "/performance/total", p),
  perfCash: (p: Record<string, any> = {}) =>
    getJSON<{ rows: { as_of_date: string; currency: string; closing_balance: number }[] }>(
      "/performance/cash", p),

  prices: (symbol: string, freq: "D" | "W" | "M" = "D") =>
    getJSON<{ symbol: string; freq: string; rows: any[] }>(
      "/research/prices", { symbol, freq }),
  trades: (symbol: string) =>
    getJSON<{ symbol: string; rows: any[] }>("/research/trades", { symbol }),
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
  statementExplain: (statementId: number) => getJSON<StatementExplain>(`/statements/explain/${statementId}`),
  statementBoxes: (statementId: number) => getJSON<StatementBoxes>(`/statements/${statementId}/boxes`),
  statementPdfUrl: (statementId: number) => `${API_BASE}/statements/${statementId}/pdf`,
  uploadStatement: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return postForm<StatementUploadResult>("/statements/upload", form);
  },
  importStatement: (payload: { sha256: string; institution_folder: string; force?: boolean }) =>
    postJSON<any>("/statements/import", payload),
  draftParser: (payload: { sha256: string; institution_folder?: string; provider?: string; send_to_provider?: boolean; model?: string }) =>
    postJSON<any>("/statements/draft-parser", payload),
  reconciliationSummary: () => getJSON<any>("/statements/reconciliation/summary"),
  rebuildReconciliation: () => postJSON<any>("/statements/reconciliation/rebuild", {}),
};

export type TxnRow = {
  transaction_id: number;
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
  asset_type: string | null;
  option_expiry: string | null;
  option_strike: number | null;
  option_type: string | null;
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
  symbol: string;
  asset_type: string;
  currency: string;
  option_expiry: string | null;
  option_strike: number | null;
  option_type: string | null;
  quantity: number;
  avg_cost: number | null;
  book_value: number | null;
  market_price: number | null;
  market_value: number | null;
  unrealized_pnl: number | null;
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
  account_id: number;
  account_number: string;
  institution_code: string;
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
  /** UI language: en, zh-HK (HK Trad.), zh-TW (TW Trad.), zh-CN (Simplified). */
  language?: "en" | "zh-HK" | "zh-TW" | "zh-CN";
  /** Optional parser-draft provider keys. Stored locally only. */
  llm_keys?: { openai?: string; anthropic?: string; google?: string };
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
  parse_status: string;
};

export type StatementUploadResult = {
  status: string;
  path: string;
  sha256: string;
  already_ingested: boolean;
  parse_status: string | null;
  review: {
    parser: { name: string; version: string } | null;
    parse_status: string;
    statements: any[];
    errors: string[];
  };
  institutions: { folder: string; code: string }[];
};

export type StatementExplain = {
  statement: any;
  source_file: any;
  pages: { page_number: number; lines: { line_number: number; text: string; refs: any[] }[] }[];
  transactions: any[];
  positions: any[];
  cash_balances: any[];
  annual_performance: any[];
  quarantine: any[];
};

/** A matched reference linking a PDF line box to a parsed item. */
export type BoxRef = {
  kind: "transaction" | "position" | "quarantine";
  id: number;
  label: string;
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
  statement: { statement_id: number; account_id: number; period_start: string; period_end: string; source_file_id: number };
  source_file: { relpath: string; sha256: string | null; parser_name: string | null; parser_version: string | null; parse_status: string } | null;
  pages: { page_number: number; width: number; height: number; lines: LineBox[] }[];
  transactions: any[];
  positions: any[];
  cash_balances: any[];
  quarantine: any[];
};
