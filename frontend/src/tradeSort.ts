// Pure sorting for the Research trade-history table. Kept free of React and
// Plotly imports so the node --test suite can load it directly.

export type TradeCol =
  | "trade_date" | "txn_type" | "quantity" | "price"
  | "net_amount" | "currency" | "account" | "description";

export type SortDir = "asc" | "desc";

export function sortTrades(
  rows: Record<string, any>[],
  col: TradeCol | null,
  dir: SortDir,
): Record<string, any>[] {
  if (!col) return rows;
  const sign = dir === "asc" ? 1 : -1;
  const value = (row: Record<string, any>): string | number | null => {
    if (col === "account") return `${row.institution_code} ${row.account_number}`;
    return row[col];
  };
  return [...rows].sort((a, b) => {
    const av = value(a);
    const bv = value(b);
    // Rows without a value always sink to the bottom, whatever the direction.
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * sign;
    return String(av).localeCompare(String(bv)) * sign;
  });
}
