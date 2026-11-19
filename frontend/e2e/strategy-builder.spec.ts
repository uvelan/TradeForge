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
  await nested.getByLabel("Rule dependency").selectOption("smt");
  await nested.getByLabel("Observation selection").selectOption("EARLIEST_ELIGIBLE");
  await page.getByRole("button", { name: "Check readiness" }).click();
  await expect(page.getByLabel("Definition status")).toHaveText("DRAFT");
  await expect(validation.getByRole("alert")).toBeVisible();
  await expect(validation.getByRole("heading", { name: "Unresolved settings" })).toBeVisible();
  await validation.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("strategy-validation.png") });
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
test("requires an explicit fixed price and preserves CRT family binding", async ({ page }, info) => {
  const templates = await (await page.request.get(endpoint + "/templates")).json();
  const definition = templates.find((item: { id: string }) => item.id === "bearish").definition;
  const rule = { ...definition.setup.children[1], alias: null };
  const group = (id: string, child: object) => ({ type: "GROUP", id, operator: "ALL", children: [child] });
  const expiry = { unit: "SECONDS", count: 300, timeframe: null };
  const reply = await page.request.post(endpoint, { data: {
    ...definition, name: "Explicit fixed entry", expiry,
    trigger: group("trigger", { ...rule, id: "trigger_evidence" }),
    invalidation: group("invalidation", { ...rule, id: "invalid_evidence", expected: "bullish" }),
    trade_intent: { ...definition.trade_intent, expiry },
    risk: { account_risk_pct: 0.5, max_simultaneous_exposure_pct: 1, max_session_loss_pct: 2,
      min_reward_risk: 2, instrument_limits: [
        { role: "PRIMARY", max_risk_percent: 1 }, { role: "COMPARISON", max_risk_percent: 1 }
      ] },
    execution: { price_basis: "BID", spread_bps: 1, slippage_bps: 0, commission_bps: 0, intrabar: "REJECT_AMBIGUOUS" },
    management: { initial_stop: { kind: "FIXED_PIPS", distance: 10 }, target: { kind: "REWARD_MULTIPLE", multiple: 2 } }
  } });
  expect(reply.status()).toBe(201);

  await page.goto("/");
  await page.getByRole("button", { name: "Strategy Builder", exact: true }).click();
  await page.getByRole("button", { name: "Explicit fixed entry", exact: true }).click();
  await expect(page.getByLabel("Trade direction")).toHaveValue("SHORT");
  await expect(page.getByLabel("Traded instrument")).toHaveValue("PRIMARY");
  await expect(page.getByLabel("Context activation")).toHaveValue("LATCHED");
  await expect(page.getByLabel("Same family as")).toHaveValue("crt_reference");
  await page.getByLabel("Entry intent").selectOption("STOP");
  await page.getByRole("button", { name: "Check readiness" }).click();
  const validation = page.getByRole("region", { name: "Definition validation" });
  await expect(validation.getByRole("alert")).toContainText("trade_intent.entry.price");
  await expect(page.getByLabel("Definition status")).toHaveText("DRAFT");
  await page.getByLabel("Fixed absolute entry price").fill("1.205");
  await page.getByRole("button", { name: "Check readiness" }).click();
  await expect(page.getByLabel("Definition status")).toHaveText("READY_FOR_EVALUATION");
  await page.getByLabel("Fixed absolute entry price").scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("strategy-intent.png") });
  await page.getByRole("button", { name: "Save revision" }).click();
  await expect(page.getByText("Definition saved as version 2", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Explicit fixed entry", exact: true }).click();
  await expect(page.getByLabel("Entry intent")).toHaveValue("STOP");
  await expect(page.getByLabel("Fixed absolute entry price")).toHaveValue("1.205");
  await expect(page.getByLabel("Definition status")).toHaveText("READY_FOR_EVALUATION");
});

test("requires the tracked CRT and confirmed SMT aliases for dependent triggers", async ({ page }, info) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Strategy Builder", exact: true }).click();
  await page.getByRole("button", { name: "Use Bearish CRT / SMT template" }).click();
  await page.getByLabel("Strategy name").fill("Bound trigger definition");
  const trigger = page.getByRole("region", { name: "trigger phase" });
  const validation = page.getByRole("region", { name: "Definition validation" });
  const validate = page.getByRole("button", { name: "Validate definition" });
  await trigger.getByRole("button", { name: "Add trigger group" }).click();
  await trigger.getByRole("button", { name: "Add condition" }).click();
  await trigger.getByLabel("Observation").selectOption("CRT_STATE");
  await validate.click();
  await expect(validation.getByRole("alert")).toContainText("Use BOUND_FAMILY with tracked CRT alias crt_reference");
  const crt = trigger.locator(".strategy-node").first();
  await crt.getByLabel("Same family as").selectOption("crt_reference");

  await trigger.getByRole("button", { name: "Add condition" }).click();
  const smt = trigger.locator(".strategy-node").last();
  await smt.getByLabel("Rule dependency").selectOption("smt");
  await smt.locator("select").filter({ has: page.locator('option[value="SMT_LEADER"]') }).selectOption("SMT_LEADER");
  await validate.click();
  await expect(validation.getByRole("alert")).toContainText("Use BOUND_FAMILY with a prior SMT_CONFIRMED alias");
  await smt.getByLabel("Same family as").selectOption("smt_confirmation");
  await validate.click();
  await expect(validation.getByText("VALID_STRATEGY_DEFINITION", { exact: true })).toBeVisible();
  await trigger.screenshot({ path: info.outputPath("strategy-bound-trigger.png") });
  await page.getByRole("button", { name: "Save strategy" }).click();
  await expect(page.getByText("Definition saved as version 1", { exact: true })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Bound trigger definition", exact: true }).click();
  await expect(crt.getByLabel("Same family as")).toHaveValue("crt_reference");
  await expect(smt.getByLabel("Same family as")).toHaveValue("smt_confirmation");
});
