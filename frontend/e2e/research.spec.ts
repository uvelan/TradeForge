import { expect, test } from "@playwright/test";

test("opens the research dashboard, replays demo bars, and links to the preserved catalog", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("http://127.0.0.1:4180/");
  await page.getByRole("button", { name: "Market Terminal", exact: true }).click();
  const source = page.getByRole("combobox").filter({ has: page.getByRole("option", { name: "DEMO (synthetic)", exact: true }) }).last();
  await expect(source).toHaveValue("MT5");
  await source.selectOption("DEMO");
  const chart = page.locator('[aria-label="Interactive market candlestick chart"] canvas').first();
  await expect(chart).toBeVisible();
  await page.getByRole("button", { name: "Bar replay", exact: true }).click();
  await expect(page.getByRole("button", { name: "Exit replay", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Data Library", exact: true }).click();
  await page.getByRole("link", { name: "Open fixed-point catalog" }).click();
  await expect(page.getByRole("button", { name: "Generate EURUSD simulation" })).toBeVisible();
  expect(errors).toEqual([]);
});
