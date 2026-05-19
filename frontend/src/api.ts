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
    getJSON<{ rows: TxnRow[]; count: number }>("/transactions", p),
  accounts: () => getJSON<{ rows: Account[] }>("/transactions/accounts"),
  symbols: () => getJSON<{ rows: SymbolRow[] }>("/transactions/symbols"),
  txnTypes: () => getJSON<{ rows: string[] }>("/transactions/txn-types"),
  latestDate: () => getJSON<{ latest: string | null }>("/transactions/latest-date"),

  monthlySnapshot: (p: { month_end?: string; account_id?: number[] } = {}) =>
    getJSON<{ as_of_date: string; rows: HoldingRow[] }>("/monthly/snapshot", p),
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

  vizSector: (p: { month_end?: string; account_id?: number[] } = {}) =>
    getJSON<{ as_of_date: string | null; rows: { symbol: string; asset_type: string; currency: string; market_value: number }[] }>(
      "/viz/holdings_by_sector", p),
  vizCorrelation: (p: { start: string; end: string; account_id?: number[] }) =>
    getJSON<{ symbols: string[]; matrix: number[][] }>("/viz/correlation", p),
  vizRRG: (p: { benchmark?: string; window_days?: number; start?: string; end?: string; account_id?: number[] } = {}) =>
    getJSON<{ frames: { date: string; points: { symbol: string; x: number; y: number }[] }[] }>(
      "/viz/rrg", p),

  config: () => getJSON<UserConfig>("/config"),
  saveConfig: (cfg: Partial<UserConfig>) => putJSON<UserConfig>("/config", cfg),
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
  display_currency: "CAD" | "USD";
  hide_money: boolean;
  /** UI language: en, zh-HK (HK Trad.), zh-TW (TW Trad.), zh-CN (Simplified). */
  language?: "en" | "zh-HK" | "zh-TW" | "zh-CN";
  /** Placeholder slots for LLM-assisted features. Stored locally only. */
  llm_keys?: { openai?: string; anthropic?: string; google?: string };
};
