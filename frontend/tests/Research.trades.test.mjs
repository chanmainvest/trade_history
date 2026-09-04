import assert from "node:assert/strict";
import { after, before, test } from "node:test";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";

let vite;
let CollapsibleCard;
let sortTrades;

before(async () => {
  vite = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ CollapsibleCard } = await vite.ssrLoadModule("/src/CollapsibleCard.tsx"));
  ({ sortTrades } = await vite.ssrLoadModule("/src/tradeSort.ts"));
});

after(async () => {
  await vite?.close();
});

function trade(overrides = {}) {
  return {
    trade_date: "2024-03-02",
    txn_type: "sell",
    quantity: 10,
    price: 5,
    net_amount: -500,
    currency: "CAD",
    institution_code: "TST",
    account_number: "A2",
    description: "desc",
    ...overrides,
  };
}

test("sortTrades orders by column in both directions", () => {
  const rows = [
    trade({ trade_date: "2024-03-02", account_number: "A2" }),
    trade({ trade_date: "2024-01-15", account_number: "A1", txn_type: "buy" }),
    trade({ trade_date: "2024-02-01", account_number: "B1", institution_code: "OTH" }),
  ];
  assert.deepEqual(
    sortTrades(rows, "trade_date", "asc").map((r) => r.trade_date),
    ["2024-01-15", "2024-02-01", "2024-03-02"],
  );
  assert.deepEqual(
    sortTrades(rows, "trade_date", "desc").map((r) => r.trade_date),
    ["2024-03-02", "2024-02-01", "2024-01-15"],
  );
  // account sorts by the displayed "institution • account" composite, so the
  // OTH account sorts before the TST ones
  assert.deepEqual(
    sortTrades(rows, "account", "asc").map((r) => r.account_number),
    ["B1", "A1", "A2"],
  );
});

test("sortTrades sinks rows without a value in every direction", () => {
  const rows = [
    trade({ quantity: null, price: null, net_amount: null }),
    trade({ quantity: 20 }),
    trade({ quantity: 10 }),
  ];
  assert.deepEqual(sortTrades(rows, "quantity", "asc").map((r) => r.quantity), [10, 20, null]);
  assert.deepEqual(sortTrades(rows, "quantity", "desc").map((r) => r.quantity), [20, 10, null]);
  assert.deepEqual(sortTrades(rows, "price", "asc").map((r) => r.price), [5, 5, null]);
});

test("sortTrades without a column leaves the source order untouched", () => {
  const rows = [trade(), trade({ trade_date: "2024-01-15" })];
  assert.equal(sortTrades(rows, null, "asc"), rows);
});

test("CollapsibleCard renders an expanded toggle header with its body", () => {
  const markup = renderToStaticMarkup(
    React.createElement(
      CollapsibleCard,
      { title: "Trade history" },
      React.createElement("p", null, "body"),
    ),
  );
  assert.match(markup, /card-collapse-header/);
  assert.match(markup, /aria-expanded="true"/);
  assert.match(markup, /Trade history/);
  assert.match(markup, /body/);
});
