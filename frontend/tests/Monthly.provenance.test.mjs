import assert from "node:assert/strict";
import { after, before, test } from "node:test";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { createServer } from "vite";

let vite;
let HoldingQuality;
let HoldingSources;
let I18nProvider;

before(async () => {
  vite = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  ({ HoldingQuality, HoldingSources } = await vite.ssrLoadModule("/src/tabs/Monthly.tsx"));
  ({ I18nProvider } = await vite.ssrLoadModule("/src/i18n.tsx"));
});

after(async () => {
  await vite?.close();
});

function sourceRef(statementId, id, linkable) {
  return {
    statement_id: statementId,
    kind: "position",
    id,
    checkpoint: true,
    geometry_status: linkable ? "exact" : "unavailable",
    page_numbers: linkable ? [id] : [],
    linkable,
  };
}

function holding(overrides = {}) {
  return {
    as_of_date: "2024-02-28",
    account_id: 1,
    account_number: "A-1",
    nickname: null,
    institution_code: "TST",
    institution_name: "Test Broker",
    instrument_key: "equity|ABC|CAD",
    holding_key: "1|equity|ABC|CAD|CAD",
    symbol: "ABC",
    ticker_symbols: ["ABC"],
    market_symbol: "ABC.TO",
    asset_type: "equity",
    currency: "CAD",
    scope_key: null,
    option_expiry: null,
    option_strike: null,
    option_type: null,
    quantity: 30,
    source_ref: null,
    provenance: {
      type: "multiple_checkpoints",
      checkpoint: null,
      checkpoints: [
        {
          scope_key: "default",
          checkpoint_date: "2024-02-28",
          checkpoint_statement_id: 11,
          checkpoint_snapshot_set_id: 21,
          quantity: 10,
          source_ref: sourceRef(11, 101, true),
          movements: [],
        },
        {
          scope_key: "unlinked",
          checkpoint_date: "2024-02-28",
          checkpoint_statement_id: 12,
          checkpoint_snapshot_set_id: 22,
          quantity: 5,
          source_ref: sourceRef(12, 102, false),
          movements: [],
        },
        {
          scope_key: "secondary",
          checkpoint_date: "2024-02-28",
          checkpoint_statement_id: 13,
          checkpoint_snapshot_set_id: 23,
          quantity: 15,
          source_ref: sourceRef(13, 103, true),
          movements: [],
        },
      ],
      movements: [],
    },
    avg_cost: null,
    book_value: null,
    market_price: 12,
    market_value: 360,
    unrealized_pnl: null,
    checkpoint_date: null,
    checkpoint_statement_id: null,
    checkpoint_snapshot_set_id: null,
    is_reported: false,
    is_reconstructed: true,
    holding_state: "incomplete",
    reconciliation_status: null,
    reconciliation_reason: null,
    price_date: "2024-02-28",
    price_status: "market",
    quality_warnings: ["duplicate_complete_position_scopes"],
    ...overrides,
  };
}

function render(component, lang = "en") {
  return renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      { initialEntries: ["/"] },
      React.createElement(I18nProvider, { lang }, component),
    ),
  );
}

test("composite provenance renders every contributor without an authoritative source", () => {
  const html = render(React.createElement(HoldingSources, { row: holding() }));

  assert.match(html, /Sources 3/);
  assert.equal((html.match(/class="source-row-link"/g) || []).length, 2);
  assert.match(html, /statement=11&amp;ref=position%3A101/);
  assert.match(html, /statement=13&amp;ref=position%3A103/);
  assert.match(html, /class="multiple-source-unlinked"/);
  assert.doesNotMatch(html, /Open position in Verify extraction/);
});

test("single-source provenance keeps the existing source icon", () => {
  const source = { ...sourceRef(14, 104, true), checkpoint: false };
  const row = holding({
    scope_key: "default",
    source_ref: source,
    provenance: { type: "reported_row", checkpoint: source, movements: [] },
    checkpoint_date: "2024-02-28",
    checkpoint_statement_id: 14,
    checkpoint_snapshot_set_id: 24,
    is_reported: true,
    is_reconstructed: false,
    holding_state: "reported",
    price_status: "broker_reported",
    quality_warnings: [],
  });
  const html = render(React.createElement(HoldingSources, { row }));

  assert.equal((html.match(/class="source-row-link"/g) || []).length, 1);
  assert.match(html, /Open position in Verify extraction/);
  assert.match(html, /statement=14&amp;ref=position%3A104/);
  assert.doesNotMatch(html, /multiple-source-indicator/);
});

test("composite quality text is translated in every locale", () => {
  const expected = {
    en: "Multiple complete checkpoints",
    "zh-HK": "多個完整檢查點",
    "zh-TW": "多個完整檢查點",
    "zh-CN": "多个完整检查点",
  };
  for (const [lang, text] of Object.entries(expected)) {
    const html = render(React.createElement(HoldingQuality, { row: holding() }), lang);
    assert.match(html, new RegExp(text));
  }
});
