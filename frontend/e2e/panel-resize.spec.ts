import { expect, test } from "@playwright/test";

test("desktop panels resize via drag and keyboard, dock left/right, collapse/restore/reset, clamp to viewport, and preserve chart on phone", async ({ page }) => {
  const start = 1700000400;
  const candles = Array.from({ length: 80 }, (_, i) => {
    const t = start + i * 300;
    const base = 1.08 + i * 0.0001;
    return { time: t, open: base, high: base + 0.0005, low: base - 0.0004, close: base + 0.0002, available_at: t + 300 };
  });
  const cutoffs = candles.map(b => b.time + 300);
  const inspectedIndex = 40;
  const inspectedIso = new Date(candles[inspectedIndex].time * 1000).toISOString();
  const cutoffIso = new Date(cutoffs[60] * 1000).toISOString();

  await page.route("**/research/api/**", async route => {
    const url = route.request().url();
    if (url.includes("/market/candles")) {
      return route.fulfill({
        json: { source: "DUKASCOPY_SAVED", synthetic: false, price_basis: "BID", feed_id: "duka", candles },
      });
    }
    if (url.includes("/drawings")) {
      return route.fulfill({
        json: {
          schema_version: 1,
          revision: 1,
          items: [
            {
              id: "00000000-0000-4000-8000-000000000123",
              kind: "horizontal",
              anchors: [{ time: candles[20].time, price: candles[20].close.toFixed(5) }],
              locked: false,
              visible: true,
              label: "Keep intact",
              color: "#55d9b5",
              created_at: 1,
              updated_at: 1,
            },
          ],
        },
      });
    }
    if (url.includes("/replay/sessions/panel-session/")) {
      return route.fulfill({ status: 404, json: { detail: "Skipped" } });
    }
    if (url.includes("/replay/sessions/panel-session")) {
      return route.fulfill({
        json: {
          id: "panel-session",
          market: {
            source: "DUKASCOPY_SAVED",
            synthetic: false,
            price_basis: "BID",
            feed_id: "duka",
            candles,
            replay_cutoffs: cutoffs,
            coverage: { start, end: start + 80 * 300, gaps: [], duplicate_bars: 0, truncated: false },
          },
          views: {},
          request: { start, end: start + 80 * 300, window_bars: 5000 },
          checkpoint: {
            position: 60,
            bookmarks: [],
            selected: { view: "primary", time: candles[inspectedIndex].time },
          },
        },
      });
    }
    if (url.includes("/replay/sessions")) {
      return route.fulfill({ json: [{ id: "panel-session", position: 60, updated_at: "2026-11-28T12:00:00Z" }] });
    }
    if (url.includes("/overview")) {
      return route.fulfill({
        json: { analytics: { trades: 0, active_plans: 0, closed: 0, win_rate: null, expectancy_r: null, net_pnl_usd: "0", profit_factor: null, note: "" }, mt5: { connected: false }, datasets: [], recent_trades: [], recent_reviews: [], journal_count: 0 },
      });
    }
    return route.fulfill({ json: [] });
  });

  await page.addInitScript(() => {
    localStorage.setItem("tradeforge.workspace.page", "terminal");
    localStorage.setItem("tradeforge.workspace.source", "DUKASCOPY_SAVED");
    localStorage.setItem("tradeforge.workspace.timeframe", "M5");
    localStorage.setItem("tradeforge.workspace.chart-comparison", "none");
  });

  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/");
  await page.getByLabel("Resume saved replay").selectOption("panel-session");

  const core = page.locator(".terminal-core");
  const dock = page.locator(".terminal-dock");
  const splitter = page.getByRole("separator", { name: "Resize inspector panel" });

  await expect(splitter).toBeVisible();
  await expect(page.locator(".cutoff-readout time")).toHaveAttribute("datetime", cutoffIso);
  await expect(page.locator(".archive-replay-readout time").first()).toHaveAttribute("datetime", inspectedIso);
  await expect(page.getByLabel("Select drawing").locator('option[value="00000000-0000-4000-8000-000000000123"]')).toHaveCount(1);

  const initialCore = (await core.boundingBox())!;
  const initialDock = (await dock.boundingBox())!;
  expect(initialDock.x).toBeGreaterThan(initialCore.x);

  // Drag splitter left by 120px to widen right-docked inspector
  const splitBox = (await splitter.boundingBox())!;
  const cx = splitBox.x + splitBox.width / 2;
  const cy = splitBox.y + splitBox.height / 2;
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  await page.mouse.move(cx - 120, cy, { steps: 6 });
  await page.mouse.up();

  await expect.poll(async () => (await dock.boundingBox())!.width).toBeGreaterThan(initialDock.width + 90);
  const draggedVal = Number(await splitter.getAttribute("aria-valuenow"));

  // Keyboard resize (+Shift+ArrowLeft -> wider by 48, ArrowRight -> narrower by 16) without stepping replay
  await splitter.focus();
  await page.keyboard.press("Shift+ArrowLeft");
  await expect(splitter).toHaveAttribute("aria-valuenow", String(Math.min(640, draggedVal + 48)));
  await expect(page.locator(".cutoff-readout time")).toHaveAttribute("datetime", cutoffIso);
  await page.keyboard.press("Home");
  await expect(splitter).toHaveAttribute("aria-valuenow", "220");
  await page.keyboard.press("End");
  const endVal = Number(await splitter.getAttribute("aria-valuenow"));
  expect(endVal).toBeGreaterThanOrEqual(480);

  // Switch dock to left and reload to check persistence
  await page.getByRole("button", { name: "Dock inspector left" }).click();
  await expect.poll(async () => {
    const c = (await core.boundingBox())!;
    const d = (await dock.boundingBox())!;
    return d.x + d.width <= c.x;
  }).toBe(true);

  await page.reload();
  await page.getByLabel("Resume saved replay").selectOption("panel-session");
  await expect.poll(async () => {
    const c = (await core.boundingBox())!;
    const d = (await dock.boundingBox())!;
    return d.x + d.width <= c.x;
  }).toBe(true);
  expect(Number(await splitter.getAttribute("aria-valuenow"))).toBe(endVal);

  // Resize desktop window narrower (860px) -> saved dock width clamps so chart core stays >= 360px and price scale fits
  await page.setViewportSize({ width: 860, height: 780 });
  await expect.poll(async () => (await core.boundingBox())!.width).toBeGreaterThanOrEqual(360);
  const clampedDock = (await dock.boundingBox())!;
  expect(clampedDock.width).toBeLessThan(endVal);
  const narrowCore = (await core.boundingBox())!;
  expect(narrowCore.x + narrowCore.width).toBeLessThanOrEqual(860);

  // Back to desktop 1440: test Collapse -> Restore -> Reset layout
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole("button", { name: "Collapse inspector" }).click();
  await expect(dock).toBeHidden();
  await expect(splitter).toBeHidden();
  await page.reload();
  await expect(dock).toBeHidden();
  await page.getByRole("button", { name: "Restore inspector" }).click();
  await page.getByLabel("Resume saved replay").selectOption("panel-session");
  await expect(dock).toBeVisible();
  await expect(page.locator(".cutoff-readout time")).toHaveAttribute("datetime", cutoffIso);

  await page.getByRole("button", { name: "Reset layout" }).click();
  await expect(splitter).toHaveAttribute("aria-valuenow", "320");
  await expect.poll(async () => {
    const c = (await core.boundingBox())!;
    const d = (await dock.boundingBox())!;
    return d.x > c.x;
  }).toBe(true);

  // Phone viewport (390x844): price scale, inspected candle, drawings, and cutoff stay intact
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(splitter).toBeHidden();
  const phoneChart = await page.getByRole("region", { name: "Primary chart" }).boundingBox();
  expect(phoneChart!.width).toBeGreaterThan(220);
  expect(phoneChart!.x + phoneChart!.width).toBeLessThanOrEqual(390);
  await expect(page.getByLabel("Select drawing").locator('option[value="00000000-0000-4000-8000-000000000123"]')).toHaveCount(1);

  await page.getByRole("button", { name: "Inspect evidence" }).click();
  await expect(page.locator(".cutoff-readout time")).toHaveAttribute("datetime", cutoffIso);
  await expect(page.locator(".archive-replay-readout time").first()).toHaveAttribute("datetime", inspectedIso);
});
