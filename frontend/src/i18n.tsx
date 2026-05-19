/**
 * Minimal in-house i18n.
 *
 * Locales:
 *  - en     — English
 *  - zh-HK  — 繁體中文 (香港) — Cantonese-flavoured Traditional Chinese
 *  - zh-TW  — 繁體中文 (台灣) — Taiwan Traditional Chinese
 *  - zh-CN  — 简体中文 (中国大陆) — Mainland Simplified Chinese
 *
 * Word-choice notes:
 *  - "Account":      HK 戶口    / TW 帳戶    / CN 账户
 *  - "Holdings":     HK 持倉    / TW 持股    / CN 持仓
 *  - "Statement":    HK 月結單  / TW 對帳單  / CN 对账单
 *  - "Search":       HK 搜尋    / TW 搜尋    / CN 搜索
 *  - "Settings":     HK 設定    / TW 設定    / CN 设置
 *  - "Performance":  HK 表現    / TW 績效    / CN 业绩
 *  - "Visualisation":HK 視覺化  / TW 視覺化  / CN 可视化
 *  - "Buy / Sell":   HK 買 / 賣 / TW 買進/賣出 / CN 买入/卖出
 *  - "Dividend":     HK 股息    / TW 股利    / CN 分红
 */
import { createContext, useContext, useMemo } from "react";

export type Lang = "en" | "zh-HK" | "zh-TW" | "zh-CN";

export const LANGS: { code: Lang; label: string; flag: string }[] = [
  { code: "en",    label: "English",   flag: "🇺🇸" },
  { code: "zh-HK", label: "繁體 (HK)", flag: "🇭🇰" },
  { code: "zh-TW", label: "繁體 (TW)", flag: "🇹🇼" },
  { code: "zh-CN", label: "简体 (CN)", flag: "🇨🇳" },
];

type Dict = Record<string, string>;

const en: Dict = {
  // Nav
  "nav.transactions":   "Transactions",
  "nav.monthly":        "Monthly snapshot",
  "nav.performance":    "Performance",
  "nav.research":       "Research",
  "nav.viz":            "Visualisations",
  "nav.config":         "Settings",
  "nav.theme.light":    "Light",
  "nav.theme.dark":     "Dark",
  "nav.portfolio":      "Portfolio",
  "nav.language":       "Language",

  // Common filters
  "f.institution":      "Institution",
  "f.account":          "Account",
  "f.symbol":           "Symbol",
  "f.type":             "Type",
  "f.start":            "Start",
  "f.end":              "End",
  "f.as_of":            "As of",
  "f.compare_to":       "Compare to",
  "f.min_abs_amount":   "Min |amount|",
  "f.sort_by":          "Sort by",
  "f.search":           "Search…",
  "f.select_shown":     "Select shown",
  "f.clear":            "Clear",
  "f.period":           "Period",
  "f.benchmark":        "Benchmark",
  "f.window":           "Window",
  "f.tail":             "Tail",
  "f.play":             "Play",
  "f.pause":            "Pause",

  // Period
  "period.1d": "1D", "period.1w": "1W",
  "period.1m": "1M", "period.3m": "3M", "period.6m": "6M",
  "period.1y": "1Y", "period.3y": "3Y", "period.5y": "5Y",
  "period.10y": "10Y", "period.max": "Max", "period.custom": "Custom",

  // Monthly table
  "th.date":      "Date",
  "th.type":      "Type",
  "th.account":   "Account",
  "th.symbol":    "Symbol",
  "th.quantity":  "Qty",
  "th.price":     "Price",
  "th.amount":    "Amount",
  "th.currency":  "Ccy",
  "th.description":"Description",
  "th.market_value":"Market value",
  "th.delta":      "Δ",

  // Currency display
  "cfg.display_currency":  "Display currency",
  "cfg.hide_money":        "Hide $ values",
  "cfg.active_portfolio":  "Active portfolio",
  "cfg.theme":             "Theme",
  "cfg.language":          "Language",
  "cfg.portfolios":        "Portfolios",
  "cfg.add_portfolio":     "Add portfolio",
  "cfg.delete":            "Delete",
  "cfg.save":              "Save",
  "cfg.name":              "Name",
  "cfg.accounts_in_portfolio": "Accounts in this portfolio",

  // Viz
  "viz.rrg":           "RRG",
  "viz.treemap":       "Treemap",
  "viz.correlation":   "Correlation",
  "viz.no_data":       "No data for this selection.",
  "viz.loading":       "Loading…",
};

const zhHK: Dict = {
  "nav.transactions":   "交易紀錄",
  "nav.monthly":        "月度持倉",
  "nav.performance":    "表現",
  "nav.research":       "個股研究",
  "nav.viz":            "視覺化",
  "nav.config":         "設定",
  "nav.theme.light":    "淺色",
  "nav.theme.dark":     "深色",
  "nav.portfolio":      "投資組合",
  "nav.language":       "語言",

  "f.institution":      "金融機構",
  "f.account":          "戶口",
  "f.symbol":           "代號",
  "f.type":             "類型",
  "f.start":            "起始",
  "f.end":              "結束",
  "f.as_of":            "截至",
  "f.compare_to":       "對比",
  "f.min_abs_amount":   "最低金額",
  "f.sort_by":          "排序",
  "f.search":           "搜尋…",
  "f.select_shown":     "全選",
  "f.clear":            "清除",
  "f.period":           "時段",
  "f.benchmark":        "基準",
  "f.window":           "窗口",
  "f.tail":             "拖尾",
  "f.play":             "播放",
  "f.pause":            "暫停",

  "period.1d": "1日", "period.1w": "1週",
  "period.1m": "1月", "period.3m": "3月", "period.6m": "6月",
  "period.1y": "1年", "period.3y": "3年", "period.5y": "5年",
  "period.10y": "10年", "period.max": "全部", "period.custom": "自訂",

  "th.date":       "日期",
  "th.type":       "類型",
  "th.account":    "戶口",
  "th.symbol":     "代號",
  "th.quantity":   "數量",
  "th.price":      "價格",
  "th.amount":     "金額",
  "th.currency":   "貨幣",
  "th.description":"說明",
  "th.market_value":"市值",
  "th.delta":      "變動",

  "cfg.display_currency":  "顯示貨幣",
  "cfg.hide_money":        "隱藏金額",
  "cfg.active_portfolio":  "啟用組合",
  "cfg.theme":             "主題",
  "cfg.language":          "語言",
  "cfg.portfolios":        "投資組合",
  "cfg.add_portfolio":     "新增組合",
  "cfg.delete":            "刪除",
  "cfg.save":              "儲存",
  "cfg.name":              "名稱",
  "cfg.accounts_in_portfolio": "組合內的戶口",

  "viz.rrg":           "相對輪轉圖",
  "viz.treemap":       "樹狀圖",
  "viz.correlation":   "相關性",
  "viz.no_data":       "目前沒有可顯示的資料。",
  "viz.loading":       "載入中…",
};

const zhTW: Dict = {
  "nav.transactions":   "交易紀錄",
  "nav.monthly":        "每月持股",
  "nav.performance":    "績效",
  "nav.research":       "個股研究",
  "nav.viz":            "視覺化",
  "nav.config":         "設定",
  "nav.theme.light":    "淺色",
  "nav.theme.dark":     "深色",
  "nav.portfolio":      "投資組合",
  "nav.language":       "語言",

  "f.institution":      "金融機構",
  "f.account":          "帳戶",
  "f.symbol":           "股票代號",
  "f.type":             "類型",
  "f.start":            "起始",
  "f.end":              "結束",
  "f.as_of":            "截至",
  "f.compare_to":       "比較",
  "f.min_abs_amount":   "最低金額",
  "f.sort_by":          "排序依據",
  "f.search":           "搜尋…",
  "f.select_shown":     "全選",
  "f.clear":            "清除",
  "f.period":           "期間",
  "f.benchmark":        "基準指數",
  "f.window":           "視窗",
  "f.tail":             "拖尾",
  "f.play":             "播放",
  "f.pause":            "暫停",

  "period.1d": "1日", "period.1w": "1週",
  "period.1m": "1月", "period.3m": "3月", "period.6m": "6月",
  "period.1y": "1年", "period.3y": "3年", "period.5y": "5年",
  "period.10y": "10年", "period.max": "全部", "period.custom": "自訂",

  "th.date":       "日期",
  "th.type":       "類型",
  "th.account":    "帳戶",
  "th.symbol":     "代號",
  "th.quantity":   "張數",
  "th.price":      "價格",
  "th.amount":     "金額",
  "th.currency":   "幣別",
  "th.description":"說明",
  "th.market_value":"市值",
  "th.delta":      "變動",

  "cfg.display_currency":  "顯示幣別",
  "cfg.hide_money":        "隱藏金額",
  "cfg.active_portfolio":  "目前組合",
  "cfg.theme":             "主題",
  "cfg.language":          "語言",
  "cfg.portfolios":        "投資組合",
  "cfg.add_portfolio":     "新增組合",
  "cfg.delete":            "刪除",
  "cfg.save":              "儲存",
  "cfg.name":              "名稱",
  "cfg.accounts_in_portfolio": "組合內的帳戶",

  "viz.rrg":           "相對輪動圖",
  "viz.treemap":       "樹狀圖",
  "viz.correlation":   "相關性矩陣",
  "viz.no_data":       "此區間沒有資料。",
  "viz.loading":       "載入中…",
};

const zhCN: Dict = {
  "nav.transactions":   "交易记录",
  "nav.monthly":        "每月持仓",
  "nav.performance":    "业绩",
  "nav.research":       "个股研究",
  "nav.viz":            "可视化",
  "nav.config":         "设置",
  "nav.theme.light":    "浅色",
  "nav.theme.dark":     "深色",
  "nav.portfolio":      "投资组合",
  "nav.language":       "语言",

  "f.institution":      "金融机构",
  "f.account":          "账户",
  "f.symbol":           "股票代码",
  "f.type":             "类型",
  "f.start":            "起始",
  "f.end":              "结束",
  "f.as_of":            "截至",
  "f.compare_to":       "对比",
  "f.min_abs_amount":   "最低金额",
  "f.sort_by":          "排序依据",
  "f.search":           "搜索…",
  "f.select_shown":     "全选",
  "f.clear":            "清除",
  "f.period":           "周期",
  "f.benchmark":        "基准",
  "f.window":           "窗口",
  "f.tail":             "拖尾",
  "f.play":             "播放",
  "f.pause":            "暂停",

  "period.1d": "1日", "period.1w": "1周",
  "period.1m": "1月", "period.3m": "3月", "period.6m": "6月",
  "period.1y": "1年", "period.3y": "3年", "period.5y": "5年",
  "period.10y": "10年", "period.max": "全部", "period.custom": "自定义",

  "th.date":       "日期",
  "th.type":       "类型",
  "th.account":    "账户",
  "th.symbol":     "代码",
  "th.quantity":   "数量",
  "th.price":      "价格",
  "th.amount":     "金额",
  "th.currency":   "币种",
  "th.description":"说明",
  "th.market_value":"市值",
  "th.delta":      "变动",

  "cfg.display_currency":  "显示币种",
  "cfg.hide_money":        "隐藏金额",
  "cfg.active_portfolio":  "当前组合",
  "cfg.theme":             "主题",
  "cfg.language":          "语言",
  "cfg.portfolios":        "投资组合",
  "cfg.add_portfolio":     "新增组合",
  "cfg.delete":            "删除",
  "cfg.save":              "保存",
  "cfg.name":              "名称",
  "cfg.accounts_in_portfolio": "组合中的账户",

  "viz.rrg":           "相对轮动图",
  "viz.treemap":       "树形图",
  "viz.correlation":   "相关性矩阵",
  "viz.no_data":       "此区间没有数据。",
  "viz.loading":       "加载中…",
};

const DICTS: Record<Lang, Dict> = {
  "en": en, "zh-HK": zhHK, "zh-TW": zhTW, "zh-CN": zhCN,
};

interface I18nCtx { lang: Lang; t: (key: string) => string; }
const Ctx = createContext<I18nCtx>({ lang: "en", t: (k) => k });

export function I18nProvider(
  { lang, children }: { lang: Lang; children: React.ReactNode },
) {
  const value = useMemo<I18nCtx>(() => {
    const dict = DICTS[lang] ?? en;
    return {
      lang,
      t: (k: string) => dict[k] ?? en[k] ?? k,
    };
  }, [lang]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n() { return useContext(Ctx); }
