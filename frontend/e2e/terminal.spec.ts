import { expect, test } from "@playwright/test";

test("generates a simulated dataset and reopens it after reload", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  await page.goto("http://127.0.0.1:4180/catalog.html");
  await page.getByRole("button", { name: "Generate EURUSD simulation" }).click();
  const chart = page.getByRole("img", { name: "EURUSD M15 candlestick chart" });
  await expect(chart.locator("canvas").first()).toBeVisible();
  await expect(page.getByText("simulator / deterministic-eurusd-v1", { exact: false })).toBeVisible();
  await page.reload();
  await expect(chart.locator("canvas").first()).toBeVisible();
  expect(pageErrors).toEqual([]);
});
