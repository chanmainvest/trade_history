/**
 * Pure helpers behind the Verify-extraction page filter toolbar: month
 * options for the period picker (newest first, grouped by year) and the
 * locale-aware month labels used by the toolbar and the index drawer.
 */

export type MonthOption = { periodEnd: string; key: string; label: string };
export type YearGroup = { year: string; months: MonthOption[] };

/** "2026-03-31" → "2026-03". */
export function monthKey(periodEnd: string): string {
  return periodEnd.slice(0, 7);
}

/** "2026-03-31" → "March 2026" (locale-aware; falls back to the raw value). */
export function monthLabel(periodEnd: string, locale: string): string {
  const year = Number(periodEnd.slice(0, 4));
  const month = Number(periodEnd.slice(5, 7));
  if (!year || month < 1 || month > 12) return periodEnd;
  try {
    return new Intl.DateTimeFormat(locale, { year: "numeric", month: "long" })
      .format(new Date(year, month - 1, 1));
  } catch {
    return periodEnd;
  }
}

/**
 * Distinct month-ends as `<option>` data, newest first, grouped by year
 * newest first. When two period ends share a calendar month the later one
 * wins, so each month key maps to exactly one option.
 */
export function groupMonthsByYear(
  periodEnds: string[],
  locale: string,
): YearGroup[] {
  const byMonth = new Map<string, string>();
  for (const periodEnd of periodEnds) {
    const key = monthKey(periodEnd);
    const existing = byMonth.get(key);
    if (!existing || periodEnd > existing) byMonth.set(key, periodEnd);
  }
  const groups = new Map<string, MonthOption[]>();
  for (const key of [...byMonth.keys()].sort((a, b) => (a < b ? 1 : -1))) {
    const periodEnd = byMonth.get(key)!;
    const year = key.slice(0, 4);
    const group = groups.get(year) ?? [];
    group.push({ periodEnd, key, label: monthLabel(periodEnd, locale) });
    groups.set(year, group);
  }
  return [...groups.entries()]
    .sort((a, b) => (a[0] < b[0] ? 1 : -1))
    .map(([year, months]) => ({ year, months }));
}
