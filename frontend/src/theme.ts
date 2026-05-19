import { useEffect, useState } from "react";

export type Theme = "dark" | "light";

const KEY = "ledger.theme";

export function useTheme(): [Theme, (t: Theme) => void] {
  const initial = (typeof localStorage !== "undefined" && (localStorage.getItem(KEY) as Theme)) || "dark";
  const [theme, setThemeState] = useState<Theme>(initial);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem(KEY, theme); } catch { /* ignore */ }
  }, [theme]);
  return [theme, setThemeState];
}

export function plotlyTheme() {
  const cs = getComputedStyle(document.documentElement);
  return {
    paper_bgcolor: cs.getPropertyValue("--plotly-paper").trim() || "#1C2541",
    plot_bgcolor: cs.getPropertyValue("--plotly-plot").trim() || "#131C2E",
    font: { color: cs.getPropertyValue("--fg").trim() || "#E0E0E0" },
    xaxis_gridcolor: cs.getPropertyValue("--plotly-grid").trim() || "#2A3F5F",
    yaxis_gridcolor: cs.getPropertyValue("--plotly-grid").trim() || "#2A3F5F",
    pos: cs.getPropertyValue("--pos").trim() || "#2BBF73",
    neg: cs.getPropertyValue("--neg").trim() || "#E35D6A",
    accent: cs.getPropertyValue("--accent").trim() || "#3A7BD5",
  };
}
