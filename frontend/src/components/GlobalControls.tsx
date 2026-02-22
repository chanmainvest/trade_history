import { useTranslation } from "react-i18next";
import type { Currency, Language } from "../types";

type Props = {
  currency: Currency;
  onCurrencyChange: (value: Currency) => void;
  privacy: boolean;
  onPrivacyChange: (value: boolean) => void;
  language: Language;
  onLanguageChange: (value: Language) => void;
};

export function GlobalControls(props: Props) {
  const { t } = useTranslation();

  return (
    <div className="control-grid">
      <label className="control-item">
        <span>{t("currency")}</span>
        <select value={props.currency} onChange={(e) => props.onCurrencyChange(e.target.value as Currency)}>
          <option value="CAD">CAD</option>
          <option value="USD">USD</option>
        </select>
      </label>

      <label className="control-item">
        <span>{t("privacyMode")}</span>
        <button
          type="button"
          className={props.privacy ? "btn-active" : ""}
          onClick={() => props.onPrivacyChange(!props.privacy)}
        >
          {props.privacy ? t("privacyOn") : t("privacyOff")}
        </button>
      </label>

      <label className="control-item">
        <span>{t("language")}</span>
        <select value={props.language} onChange={(e) => props.onLanguageChange(e.target.value as Language)}>
          <option value="en">English</option>
          <option value="zh-Hant">繁體中文</option>
        </select>
      </label>
    </div>
  );
}

