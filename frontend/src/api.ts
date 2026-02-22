import axios from "axios";
import type {
  AssetGroup,
  ClosedPlRow,
  Currency,
  MonthlyReconciliationRow,
  MonthlyReconciliationSnapshotLine,
  SectorRow,
  SymbolCatalogRow,
  SymbolOverridePayload,
  TradeRow
} from "./types";

const http = axios.create({
  baseURL: "/"
});

http.interceptors.request.use((config) => {
  if (typeof window !== "undefined") {
    const token = window.localStorage.getItem("th_access_token");
    if (token) {
      config.headers = config.headers ?? {};
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});

export async function fetchTrades(params: {
  sortBy: string;
  sortOrder: "asc" | "desc";
  accountId?: string;
  institution?: string;
  symbol?: string;
}): Promise<TradeRow[]> {
  const response = await http.get("/api/trades", {
    params: {
      page: 1,
      page_size: 1000,
      sort_by: params.sortBy,
      sort_order: params.sortOrder,
      account_id: params.accountId || undefined,
      institution: params.institution || undefined,
      symbol: params.symbol || undefined
    }
  });
  return response.data.items as TradeRow[];
}

export async function fetchClosedPl(): Promise<ClosedPlRow[]> {
  const response = await http.get("/api/positions/closed-pl", {
    params: { page: 1, page_size: 1000 }
  });
  return response.data.items as ClosedPlRow[];
}

export async function fetchAssetValue(params: {
  displayCurrency: Currency;
  groupBy: "total" | "account" | "institution";
}): Promise<AssetGroup[]> {
  const response = await http.get("/api/assets/value", {
    params: {
      display_currency: params.displayCurrency,
      group_by: params.groupBy
    }
  });
  return response.data.items as AssetGroup[];
}

export async function fetchSector(displayCurrency: Currency): Promise<SectorRow[]> {
  const response = await http.get("/api/assets/sector", {
    params: { display_currency: displayCurrency }
  });
  return response.data.items as SectorRow[];
}

export async function fetchMonthlyReconciliation(params: {
  displayCurrency: Currency;
  institution?: string;
  accountId?: string;
}): Promise<MonthlyReconciliationRow[]> {
  const response = await http.get("/api/reconciliation/monthly", {
    params: {
      display_currency: params.displayCurrency,
      institution: params.institution || undefined,
      account_id: params.accountId || undefined
    }
  });
  return response.data.items as MonthlyReconciliationRow[];
}

export async function fetchMonthlyReconciliationSnapshotLines(params: {
  month: string;
  accountId: string;
  currencyNative?: Currency;
  displayCurrency: Currency;
  institution?: string;
}): Promise<MonthlyReconciliationSnapshotLine[]> {
  const response = await http.get("/api/reconciliation/monthly/snapshot-lines", {
    params: {
      month: params.month,
      account_id: params.accountId,
      currency_native: params.currencyNative || undefined,
      display_currency: params.displayCurrency,
      institution: params.institution || undefined
    }
  });
  return response.data.items as MonthlyReconciliationSnapshotLine[];
}

export async function fetchAccounts(): Promise<Array<{ account_id: string; institution: string }>> {
  const response = await http.get("/api/meta/accounts");
  return response.data.items;
}

export async function fetchSymbolCatalog(q?: string): Promise<SymbolCatalogRow[]> {
  const response = await http.get("/api/symbols", {
    params: { q: q || undefined }
  });
  return response.data.items as SymbolCatalogRow[];
}

export async function saveSymbolOverride(symbolNorm: string, payload: SymbolOverridePayload): Promise<void> {
  await http.put(`/api/symbols/overrides/${encodeURIComponent(symbolNorm)}`, payload);
}

export async function deleteSymbolOverride(symbolNorm: string): Promise<void> {
  await http.delete(`/api/symbols/overrides/${encodeURIComponent(symbolNorm)}`);
}

export async function refreshSectorMetadata(symbols: string[]): Promise<{ metadata_rows: number; sectors_updated: number }> {
  const response = await http.post("/api/symbols/refresh-sectors", { symbols });
  return response.data as { metadata_rows: number; sectors_updated: number };
}
