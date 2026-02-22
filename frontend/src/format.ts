import type { Currency } from "./types";

export function money(value: number | null | undefined, currency: Currency, privacy: boolean): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  if (privacy) {
    return "***";
  }
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 2
  }).format(value);
}

export function number(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: 4
  }).format(value);
}

export function pct(value: number | null | undefined, privacy: boolean): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "-";
  }
  const formatted = `${value.toFixed(2)}%`;
  return privacy ? formatted : formatted;
}

