export type Currency = "CAD" | "USD";
export type Language = "en" | "zh-Hant";
export type TabKey = "trades" | "assets" | "sector" | "reconcile" | "symbols";

export type TradeRow = {
  event_id: number;
  trade_date: string;
  settle_date: string | null;
  account_id: string;
  institution: string;
  event_type: string;
  side: string | null;
  quantity: number | null;
  price: number | null;
  gross_amount: number | null;
  commission: number | null;
  fees: number | null;
  currency: Currency | null;
  symbol: string | null;
  asset_type: string | null;
  realized_pl_native: number | null;
};

export type ClosedPlRow = {
  id: number;
  close_event_id: number;
  close_date: string;
  account_id: string;
  institution: string;
  symbol: string;
  quantity_closed: number;
  proceeds_native: number;
  cost_native: number;
  realized_pl_native: number;
  currency: Currency;
};

export type AssetPosition = {
  group_key: string;
  account_id: string;
  institution: string;
  symbol: string;
  asset_type: string;
  sector: string;
  quantity: number;
  currency_native: Currency;
  price_native: number | null;
  market_value_native: number | null;
  market_value_display: number | null;
  cost_native: number;
  unrealized_pl_native: number | null;
};

export type AssetGroup = {
  group_key: string;
  display_currency: Currency;
  total_market_value_display: number;
  total_market_value_native: number;
  positions: AssetPosition[];
};

export type SectorRow = {
  sector: string;
  value: number;
  percentage: number;
  currency: Currency;
};

export type SymbolCatalogRow = {
  symbol_norm: string;
  sample_symbol_raw: string | null;
  event_count: number;
  account_count: number;
  default_market_symbol: string;
  resolved_market_symbol: string;
  override_market_symbol: string | null;
  override_sector: string | null;
  override_notes: string | null;
  override_active: boolean;
  provider_sector: string | null;
  provider_industry: string | null;
  provider_exchange: string | null;
  instrument_sector: string | null;
};

export type SymbolOverridePayload = {
  market_symbol?: string | null;
  sector_override?: string | null;
  notes?: string | null;
  is_active?: boolean;
};

export type MonthlyReconciliationRow = {
  month: string;
  institution: string;
  account_id: string;
  currency_native: Currency;
  statement_cash_opening_native: number | null;
  statement_cash_closing_native: number | null;
  statement_portfolio_native: number | null;
  statement_previous_value_native: number | null;
  txn_event_count: number;
  txn_net_cash_flow_native: number;
  txn_fee_total_native: number;
  derived_cash_closing_native: number | null;
  reconciliation_gap_native: number | null;
  statement_cash_opening_display: number | null;
  statement_cash_closing_display: number | null;
  statement_portfolio_display: number | null;
  statement_previous_value_display: number | null;
  txn_net_cash_flow_display: number | null;
  txn_fee_total_display: number | null;
  derived_cash_closing_display: number | null;
  reconciliation_gap_display: number | null;
  status: "ok" | "warning" | "missing_snapshot";
};

export type MonthlyReconciliationSnapshotLine = {
  id: number;
  institution: string;
  account_id: string;
  month: string;
  snapshot_date: string | null;
  metric_code: string;
  currency_native: Currency;
  value_native: number;
  value_display: number | null;
  file_path: string;
  file_name: string;
  source_line_ref: string | null;
  raw_line: string | null;
};
