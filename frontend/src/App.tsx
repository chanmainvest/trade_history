import { NavLink, Routes, Route, Navigate } from "react-router-dom";
import Transactions from "./tabs/Transactions";
import Monthly from "./tabs/Monthly";
import Performance from "./tabs/Performance";
import Research from "./tabs/Research";
import Viz from "./tabs/Viz";
import Config from "./tabs/Config";
import { PortfolioPicker, LanguagePicker, usePortfolio } from "./portfolio";
import { useI18n } from "./i18n";

function ThemeToggle() {
  const { config, saveConfig } = usePortfolio();
  const { t } = useI18n();
  const theme = config?.theme || "dark";
  return (
    <button className="theme-toggle"
            title={`${t("cfg.theme")}: ${theme === "dark" ? t("nav.theme.light") : t("nav.theme.dark")}`}
            onClick={() => saveConfig({ theme: theme === "dark" ? "light" : "dark" })}>
      {theme === "dark" ? `☀ ${t("nav.theme.light")}` : `🌙 ${t("nav.theme.dark")}`}
    </button>
  );
}

export default function App() {
  const { t } = useI18n();
  return (
    <>
      <nav className="tabs">
        <NavLink to="/transactions" className={({ isActive }) => isActive ? "active" : ""}>{t("nav.transactions")}</NavLink>
        <NavLink to="/monthly"      className={({ isActive }) => isActive ? "active" : ""}>{t("nav.monthly")}</NavLink>
        <NavLink to="/performance"  className={({ isActive }) => isActive ? "active" : ""}>{t("nav.performance")}</NavLink>
        <NavLink to="/research"     className={({ isActive }) => isActive ? "active" : ""}>{t("nav.research")}</NavLink>
        <NavLink to="/viz"          className={({ isActive }) => isActive ? "active" : ""}>{t("nav.viz")}</NavLink>
        <NavLink to="/config"       className={({ isActive }) => isActive ? "active" : ""}>{t("nav.config")}</NavLink>
        <span className="spacer" />
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "6px 12px" }}>
          <span className="muted">{t("nav.portfolio")}:</span><PortfolioPicker />
          <ThemeToggle />
          <LanguagePicker />
        </span>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/transactions" replace />} />
          <Route path="/transactions" element={<Transactions />} />
          <Route path="/monthly" element={<Monthly />} />
          <Route path="/performance" element={<Performance />} />
          <Route path="/research" element={<Research />} />
          <Route path="/research/:symbol" element={<Research />} />
          <Route path="/viz" element={<Viz />} />
          <Route path="/config" element={<Config />} />
        </Routes>
      </main>
    </>
  );
}
