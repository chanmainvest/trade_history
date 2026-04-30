export const API_BASE = "/api";

async function get<T>(path: string, params?: Record<string, any>): Promise<T> {
  const url = new URL(API_BASE + path, window.location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    }
  }
  const r = await fetch(url.toString());
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export const api = {
  transactions: (p: Record<string, any> = {}) =>
    get<{ rows: TxnRow[]; count: number }>("/transactions", p),
  accounts: () => get<{ rows: Account[] }>("/transactions/accounts"),
  symbols: () => get<{ rows: SymbolRow[] }>("/transactions/symbols"),

  monthlySnapshot: (month_end: string) =>
    get<{ as_of_date: string; rows: HoldingRow[] }>("/monthly/snapshot", { month_end }),
  monthlyDiff: (a: string, b: string) =>
    get<{ a: string; b: string; rows: any[] }>("/monthly/diff", { a, b }),

  perfTotal: () => get<any>("/performance/total"),
  perfCash: () => get<any>("/performance/cash"),

  prices: (symbol: string, freq: "D" | "W" | "M" = "D") =>
    get<{ symbol: string; freq: string; rows: any[] }>("/research/prices", { symbol, freq }),
  trades: (symbol: string) =>
    get<{ symbol: string; rows: any[] }>("/research/trades", { symbol }),
  financials: (symbol: string, period: "quarterly" | "annual" = "quarterly") =>
    get<{ symbol: string; period: string; rows: any[] }>("/research/financials", { symbol, period }),

  vizSector: (month_end: string) =>
    get<any>("/viz/holdings_by_sector", { month_end }),
  vizCorrelation: (start: string, end: string) =>
    get<any>("/viz/correlation", { start, end }),
  vizRRG: (benchmark = "SPY", window_days = 60, start?: string, end?: string) =>
    get<any>("/viz/rrg", { benchmark, window_days, start, end }),
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

