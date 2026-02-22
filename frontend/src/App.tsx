import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { GlobalControls } from "./components/GlobalControls";
import { AssetValueTab } from "./tabs/AssetValueTab";
import { SectorTab } from "./tabs/SectorTab";
import { ReconciliationTab } from "./tabs/ReconciliationTab";
import { SymbolsTab } from "./tabs/SymbolsTab";
import { TradesTab } from "./tabs/TradesTab";
import type { Currency, Language, TabKey } from "./types";

const TAB_ORDER: Array<{ key: TabKey; labelKey: string }> = [
  { key: "trades", labelKey: "tradesTab" },
  { key: "assets", labelKey: "assetsTab" },
  { key: "sector", labelKey: "sectorTab" },
  { key: "reconcile", labelKey: "reconcileTab" },
  { key: "symbols", labelKey: "symbolsTab" }
];

export default function App() {
  const { t, i18n } = useTranslation();
  const [tab, setTab] = useState<TabKey>("trades");
  const [currency, setCurrency] = useState<Currency>("CAD");
  const [privacy, setPrivacy] = useState(false);
  const [language, setLanguage] = useState<Language>("en");

  function onLanguageChange(value: Language) {
    setLanguage(value);
    i18n.changeLanguage(value);
  }

  const tabPanel = useMemo(() => {
    if (tab === "trades") {
      return <TradesTab displayCurrency={currency} privacy={privacy} />;
    }
    if (tab === "assets") {
      return <AssetValueTab displayCurrency={currency} privacy={privacy} />;
    }
    if (tab === "sector") {
      return <SectorTab displayCurrency={currency} privacy={privacy} />;
    }
    if (tab === "reconcile") {
      return <ReconciliationTab displayCurrency={currency} privacy={privacy} />;
    }
    return <SymbolsTab />;
  }, [tab, currency, privacy]);

  return (
    <div className="page-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">Trade Ledger + Performance</p>
          <h1>{t("appTitle")}</h1>
        </div>
      </header>

      <GlobalControls
        currency={currency}
        onCurrencyChange={setCurrency}
        privacy={privacy}
        onPrivacyChange={setPrivacy}
        language={language}
        onLanguageChange={onLanguageChange}
      />

      <nav className="tab-row">
        {TAB_ORDER.map((item) => (
          <button
            key={item.key}
            type="button"
            className={tab === item.key ? "tab-btn active" : "tab-btn"}
            onClick={() => setTab(item.key)}
          >
            {t(item.labelKey)}
          </button>
        ))}
      </nav>

      <main>{tabPanel}</main>
    </div>
  );
}
