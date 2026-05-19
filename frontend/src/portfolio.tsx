import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Account, Portfolio, UserConfig } from "./api";
import { I18nProvider, Lang, LANGS } from "./i18n";

type Ctx = {
  config: UserConfig | null;
  setActivePortfolio: (id: string) => void;
  savePortfolios: (portfolios: Portfolio[], active?: string) => Promise<void>;
  saveConfig: (patch: Partial<UserConfig>) => Promise<void>;
  activePortfolio: Portfolio | null;
  /** account_ids selected by the active portfolio; [] = ALL accounts. */
  activeAccountIds: number[];
  accounts: Account[];
};

const PortfolioCtx = createContext<Ctx | null>(null);

const DEFAULT_CFG: UserConfig = {
  portfolios: [{ id: "all", name: "All accounts", account_ids: [] }],
  active_portfolio: "all",
  theme: "dark",
  display_currency: "CAD",
  hide_money: false,
  language: "en",
};

export function PortfolioProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  const cfgQ = useQuery({ queryKey: ["config"], queryFn: api.config });
  const acctsQ = useQuery({ queryKey: ["accounts"], queryFn: api.accounts });
  const [optimistic, setOptimistic] = useState<UserConfig | null>(null);

  const config = optimistic ?? cfgQ.data ?? null;
  const accounts = acctsQ.data?.rows ?? [];

  const activePortfolio = useMemo(() => {
    if (!config) return null;
    return config.portfolios.find((p) => p.id === config.active_portfolio)
      || config.portfolios[0] || null;
  }, [config]);

  const activeAccountIds = activePortfolio?.account_ids ?? [];

  useEffect(() => {
    if (!config) return;
    document.documentElement.setAttribute("data-theme", config.theme);
  }, [config?.theme]);

  async function saveConfig(patch: Partial<UserConfig>) {
    const base = config ?? DEFAULT_CFG;
    const next = { ...base, ...patch };
    setOptimistic(next);
    const saved = await api.saveConfig(next);
    setOptimistic(null);
    qc.setQueryData(["config"], saved);
  }
  async function savePortfolios(portfolios: Portfolio[], active?: string) {
    await saveConfig({
      portfolios,
      active_portfolio: active ?? config?.active_portfolio ?? "all",
    });
  }
  function setActivePortfolio(id: string) {
    saveConfig({ active_portfolio: id });
  }

  return (
    <PortfolioCtx.Provider value={{
      config, accounts, activePortfolio, activeAccountIds,
      setActivePortfolio, savePortfolios, saveConfig,
    }}>
      <I18nProvider lang={(config?.language ?? "en") as Lang}>
        {children}
      </I18nProvider>
    </PortfolioCtx.Provider>
  );
}

export function usePortfolio() {
  const v = useContext(PortfolioCtx);
  if (!v) throw new Error("usePortfolio must be inside PortfolioProvider");
  return v;
}

export function PortfolioPicker() {
  const { config, activePortfolio, setActivePortfolio } = usePortfolio();
  if (!config) return null;
  return (
    <select
      value={activePortfolio?.id || ""}
      onChange={(e) => setActivePortfolio(e.target.value)}
      title="Active portfolio"
    >
      {config.portfolios.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}{p.account_ids.length ? ` (${p.account_ids.length})` : ""}
        </option>
      ))}
    </select>
  );
}

export function LanguagePicker() {
  const { config, saveConfig } = usePortfolio();
  const current = (config?.language ?? "en") as Lang;
  return (
    <select
      value={current}
      onChange={(e) => saveConfig({ language: e.target.value as Lang })}
      title="Language / 語言 / 语言"
      style={{ minWidth: 110 }}
    >
      {LANGS.map((l) => (
        <option key={l.code} value={l.code}>{l.flag} {l.label}</option>
      ))}
    </select>
  );
}
