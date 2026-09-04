import assert from "node:assert/strict";
import { after, before, test } from "node:test";

import { createServer } from "vite";

let vite;
let verifyFilters;

before(async () => {
  vite = await createServer({
    appType: "custom",
    logLevel: "silent",
    server: { middlewareMode: true },
  });
  verifyFilters = await vite.ssrLoadModule("/src/verifyFilters.ts");
});

after(async () => {
  await vite?.close();
});

test("monthKey keeps the year and month of a period end", () => {
  assert.equal(verifyFilters.monthKey("2026-03-31"), "2026-03");
});

test("monthLabel is locale-aware", () => {
  assert.equal(verifyFilters.monthLabel("2026-03-31", "en"), "March 2026");
  assert.equal(verifyFilters.monthLabel("2026-03-31", "zh-CN"), "2026年3月");
});

test("monthLabel falls back to the raw value for malformed input", () => {
  assert.equal(verifyFilters.monthLabel("garbage", "en"), "garbage");
});

test("groupMonthsByYear groups newest year first, months newest first", () => {
  const groups = verifyFilters.groupMonthsByYear(
    ["2025-12-31", "2026-01-31", "2026-03-31", "2026-01-31"],
    "en",
  );
  assert.deepEqual(
    groups.map((g) => g.year),
    ["2026", "2025"],
  );
  assert.deepEqual(
    groups[0].months.map((m) => m.key),
    ["2026-03", "2026-01"],
  );
  assert.equal(groups[0].months[0].periodEnd, "2026-03-31");
  assert.equal(groups[0].months[0].label, "March 2026");
});

test("groupMonthsByYear collapses duplicate month ends onto the later date", () => {
  const groups = verifyFilters.groupMonthsByYear(
    ["2026-03-15", "2026-03-31"],
    "en",
  );
  assert.equal(groups.length, 1);
  assert.equal(groups[0].months.length, 1);
  assert.equal(groups[0].months[0].periodEnd, "2026-03-31");
});
