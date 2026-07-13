import { NavLink, Routes, Route, Navigate } from "react-router-dom";
import Transactions from "./tabs/Transactions";
import Monthly from "./tabs/Monthly";
import Performance from "./tabs/Performance";
import Research from "./tabs/Research";
import Viz from "./tabs/Viz";
import Verify from "./tabs/Verify";
import Config from "./tabs/Config";
import { PortfolioPicker, LanguagePicker, usePortfolio } from "./portfolio";
import { useI18n } from "./i18n";

function ThemeToggle() {
  const { config, saveConfig } = usePortfolio();
  const { t } = useI18n();
  const theme = config?.theme || "dark";
  const nextTheme = theme === "dark" ? "light" : "dark";
  return (
    <button
      className={`theme-switch ${theme === "dark" ? "is-dark" : "is-light"}`}
      title={`${t("cfg.theme")}: ${nextTheme === "dark" ? t("nav.theme.dark") : t("nav.theme.light")}`}
      aria-label={`${t("cfg.theme")}: ${nextTheme === "dark" ? t("nav.theme.dark") : t("nav.theme.light")}`}
      onClick={() => saveConfig({ theme: nextTheme })}
    >
      <span className="theme-switch-track" aria-hidden="true">
        <span className="theme-switch-glyph theme-switch-sun">☀</span>
        <span className="theme-switch-glyph theme-switch-moon">☾</span>
        <span className="theme-switch-knob" />
      </span>
    </button>
  );
}

function HideMoneyToggle() {
  const { config, saveConfig } = usePortfolio();
  const { t } = useI18n();
  const on = !!config?.hide_money;
  return (
    <button
      className={`hide-money-toggle${on ? " is-on" : ""}`}
      title={t("nav.hide_money")}
      aria-pressed={on}
      aria-label={t("nav.hide_money")}
      onClick={() => saveConfig({ hide_money: !on })}
    >
      <span className="hide-money-glyph" aria-hidden="true">$</span>
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
        <NavLink to="/verify"       className={({ isActive }) => isActive ? "active" : ""}>{t("nav.verify")}</NavLink>
        <NavLink to="/config"       className={({ isActive }) => isActive ? "active" : ""}>{t("nav.config")}</NavLink>
        <span className="spacer" />
        <span className="top-controls">
          <span className="portfolio-control"><span className="muted">{t("nav.portfolio")}:</span><PortfolioPicker /></span>
          <LanguagePicker />
          <HideMoneyToggle />
          <ThemeToggle />
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
          <Route path="/verify" element={<Verify />} />
          <Route path="/config" element={<Config />} />
        </Routes>
      </main>
    </>
  );
}
