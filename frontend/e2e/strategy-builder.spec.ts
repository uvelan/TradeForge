import { expect, test } from "@playwright/test";

const endpoint = "/research/api/strategies/v1";

test("validates nested rules and saves immutable strategy revisions", async ({ page }, info) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Strategy Builder", exact: true }).click();
  await page.getByRole("button", { name: "Use Bearish CRT / SMT template" }).click();
  const name = "Nested evidence strategy";
  await page.getByLabel("Strategy name").fill(name);
  const context = page.getByRole("region", { name: "context phase" });
  await context.getByRole("button", { name: "Add nested group" }).click();
  const nested = context.locator(".strategy-group").last();
  await nested.getByRole("button", { name: "Add condition" }).click();
  const age = nested.getByLabel("Evidence age in seconds");
  await age.fill("0");
  const validation = page.getByRole("region", { name: "Definition validation" });
  await page.getByRole("button", { name: "Validate definition" }).click();
  await expect(validation.getByRole("alert")).toContainText("max_age_seconds");
  await age.fill("3600");
  await page.getByLabel("Definition status").selectOption("READY_FOR_EVALUATION");
  await page.getByRole("button", { name: "Validate definition" }).click();
  await expect(validation.getByRole("alert")).toBeVisible();
  await expect(validation.getByRole("heading", { name: "Unresolved settings" })).toBeVisible();
  await validation.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("strategy-validation.png") });
  await page.getByLabel("Definition status").selectOption("DRAFT");
  await page.getByRole("button", { name: "Save strategy" }).click();
  await expect(page.getByText("Definition saved as version 1", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name, exact: true }).click();
  await expect(page.getByLabel("Strategy name")).toHaveValue(name);
  await expect(age).toHaveValue("3600");
  await page.getByLabel("Description").fill("Version two keeps the same mechanical rules.");
  await page.getByRole("button", { name: "Save revision" }).click();
  await expect(page.getByText("Definition saved as version 2", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Compare versions" }).click();
  await expect(page.getByText("Version two keeps the same mechanical rules.").last()).toBeVisible();
  await page.getByRole("button", { name: "Compare versions" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("strategy-versions.png") });
});

test("requires reloading before a stale editor can overwrite a concurrent revision", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Strategy Builder", exact: true }).click();
  await page.getByRole("button", { name: "Use Bullish CRT / SMT template" }).click();
  const name = "Concurrent evidence strategy";
  await page.getByLabel("Strategy name").fill(name);
  await page.getByRole("button", { name: "Save strategy" }).click();
  await expect(page.getByText("Definition saved as version 1", { exact: true })).toBeVisible();
  const listed = await (await page.request.get(endpoint)).json();
  const saved = listed.find((item: { name: string }) => item.name === name);
  const url = endpoint + "/" + saved.strategy_id;
  const record = await (await page.request.get(url)).json();
  const revision = await page.request.post(url + "/revisions", { data: {
    expected_version: record.version, definition: { ...record.definition, description: "Concurrent server revision" }
  } });
  expect(revision.status()).toBe(201);
  await page.getByLabel("Description").fill("Stale editor");
  await page.getByRole("button", { name: "Save revision" }).click();
  await expect(page.getByRole("alert")).toContainText("Another edit saved a newer version");
  await page.getByRole("button", { name: "Reload saved definition" }).click();
  await expect(page.getByLabel("Description")).toHaveValue("Concurrent server revision");
});
