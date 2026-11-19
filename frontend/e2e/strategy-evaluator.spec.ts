import { expect, test } from "@playwright/test";

test("saves a canonical strategy decision, reconstructs, and opens exact chart evidence", async ({page},info) => {
  const templates = await (await page.request.get("/research/api/strategies/v1/templates")).json();
  const data = templates.find((item: {id: string}) => item.id === "bearish").definition;
  data.name = "Canonical strategy browser fixture";
  data.status = "READY_FOR_EVALUATION";
  data.instruments.calendar = {kind: "continuous"};
  data.instruments.comparison = null;
  data.indicators = data.indicators.slice(0,1);
  data.setup.operator = "ALL";
  data.setup.children = data.setup.children.slice(0,1);
  data.temporal = [];
  const expiry = {unit: "SECONDS", count: 9000, timeframe: null};
  data.expiry = expiry;
  data.trade_intent = {...data.trade_intent, entry: {kind: "MARKET"}, expiry};
  data.execution = {price_basis: "BID", spread_bps: 1, slippage_bps: 0, commission_bps: 0, intrabar: "REJECT_AMBIGUOUS"};
  data.risk = {account_risk_pct: 0.5, max_simultaneous_exposure_pct: 1, max_session_loss_pct: 2,
    min_reward_risk: 2, instrument_limits: [{role: "PRIMARY", max_risk_percent: 1}]};
  data.management = {initial_stop: {kind: "FIXED_PIPS", distance: 10}, target: {kind: "REWARD_MULTIPLE", multiple: 2}};
  const member = {...data.setup.children[0], alias: null, observation: "CRT_STATE"};
  data.trigger = {type: "GROUP", id: "trigger", operator: "ALL", children: [
    {...member, id: "expand", expected: "EXPANSION_OBSERVED"}]};
  data.invalidation = {type: "GROUP", id: "invalidation", operator: "ALL", children: [
    {...member, id: "completed", expected: "OPPOSITE_BOUNDARY_REACHED"}]};
  const saved = await page.request.post("/research/api/strategies/v1", {data});
  expect(saved.status(), await saved.text()).toBe(201);
  await page.goto("/");
  await page.getByRole("button", {name: "Strategy Builder", exact: true}).click();
  await page.getByRole("button", {name: data.name, exact: true}).click();
  await page.getByRole("button", {name: "Evaluate", exact: true}).click();
  const evaluation = page.getByRole("region", {name: "Strategy evaluation"});
  await evaluation.getByLabel("Evaluation starts (UTC)").fill("2020-01-02T11:05");
  await evaluation.getByLabel("Decision cutoff (UTC)").fill("2020-01-02T12:10");
  await evaluation.getByRole("button", {name: "Run evaluation"}).click();
  await expect(evaluation.getByRole("heading", {name: "waiting for trigger"})).toBeVisible();
  await evaluation.getByLabel("Decision cutoff (UTC)").fill("2020-01-02T12:20");
  await evaluation.getByRole("button", {name: "Advance and save"}).click();
  await expect(evaluation.getByText("Qualified entry intent", {exact: true})).toBeVisible();
  await evaluation.getByRole("button", {name: "Inspect expand"}).click();
  await expect(evaluation.locator('pre[aria-label="Exact evidence"]')).toContainText("EXPANSION_OBSERVED");
  await evaluation.screenshot({path: info.outputPath("strategy-evaluation.png")});
  await evaluation.getByLabel("Decision cutoff (UTC)").fill("2020-01-02T12:10");
  await evaluation.getByRole("button", {name: "Reconstruct cutoff"}).click();
  await expect(evaluation.getByText(/Reconstructed view/)).toBeVisible();
  await evaluation.getByRole("button", {name: "Reload saved decision"}).click();
  await expect(evaluation.getByText("Qualified entry intent", {exact: true})).toBeVisible();
  await evaluation.getByRole("button", {name: "Chart expand"}).click();
  await expect(page.getByText("FROZEN STRATEGY EVIDENCE", {exact: true})).toBeVisible();
  await expect(page.locator(".primary-chart canvas").first()).toBeVisible();
});
