import { test, expect } from "@playwright/test";

test.describe("Smoke tests", () => {
  test("health endpoint returns ok", async ({ request }) => {
    const res = await request.get("/health");
    expect(res.ok()).toBeTruthy();
    const body = await res.json();
    expect(body.status).toBe("ok");
  });

  test("trades tab loads with table", async ({ page }) => {
    await page.goto("/");
    // Header should be visible
    await expect(page.locator("h1")).toContainText("Trade History");
    // Trades tab should be active by default
    await expect(page.locator("button.tab.active")).toContainText("Trades");
    // Wait for the table or a "no data" message to appear
    await page.waitForTimeout(2000);
    // The page should have loaded without errors
    await expect(page.locator(".content")).toBeVisible();
  });

  test("asset value tab loads", async ({ page }) => {
    await page.goto("/");
    // Click the Assets tab
    await page.locator("button.tab", { hasText: "Asset" }).click();
    await page.waitForTimeout(2000);
    // Content area should be visible
    await expect(page.locator(".content")).toBeVisible();
  });

  test("sectors tab loads", async ({ page }) => {
    await page.goto("/");
    // Click the Sectors tab
    await page.locator("button.tab", { hasText: "Sector" }).click();
    await page.waitForTimeout(2000);
    // Content area should be visible
    await expect(page.locator(".content")).toBeVisible();
  });
});
