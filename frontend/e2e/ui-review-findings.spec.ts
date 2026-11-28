import { expect, test } from "@playwright/test";

test("Forex coverage verifies unequal per-label totals and all five hourly status badge tones", async ({ page }) => {
  await page.route("**/research/api/forex/coverage?*", async route => {
    await route.fulfill({
      json: {
        start: 1794528000,
        end: 1794614400,
        provider: "DUKASCOPY",
        max_gap_ms: 15000,
        hours: [
          {
            start: 1794528000,
            end: 1794531600,
            status: "Healthy",
            expected: 12,
            eur: { feed: "dukascopy-web-m1-v1", pins: [], count: 5, missing: [], error: null },
            gbp: { feed: "dukascopy-web-m1-v1", pins: [], count: 5, missing: [], error: null },
            paired: 5,
            paired_ranges: [],
            chart_selections: [],
            pair_error: null,
            quotes: { count: 40, pins: [], gaps: [], error: null, registered: true },
          },
          {
            start: 1794531600,
            end: 1794535200,
            status: "Conflicting evidence",
            expected: 12,
            eur: { feed: "dukascopy-web-m1-v1", pins: [], count: 4, missing: [], error: "Duplicate partition checksum" },
            gbp: { feed: "dukascopy-web-m1-v1", pins: [], count: 7, missing: [], error: null },
            paired: 0,
            paired_ranges: [],
            chart_selections: [],
            pair_error: null,
            quotes: { count: 0, pins: [], gaps: [], error: null, registered: false },
          },
          {
            start: 1794535200,
            end: 1794538800,
            status: "Scheduled closure",
            expected: 0,
            eur: { feed: null, pins: [], count: 0, missing: [], error: null },
            gbp: { feed: null, pins: [], count: 0, missing: [], error: null },
            paired: 0,
            paired_ranges: [],
            chart_selections: [],
            pair_error: null,
            quotes: { count: 0, pins: [], gaps: [], error: null, registered: false },
          },
          {
            start: 1794538800,
            end: 1794542400,
            status: "Partial",
            expected: 12,
            eur: { feed: "dukascopy-web-m1-v1", pins: [], count: 2, missing: [], error: null },
            gbp: { feed: "dukascopy-web-m1-v1", pins: [], count: 10, missing: [], error: null },
            paired: 2,
            paired_ranges: [],
            chart_selections: [],
            pair_error: null,
            quotes: { count: 4, pins: [], gaps: [], error: null, registered: true },
          },
          {
            start: 1794542400,
            end: 1794546000,
            status: "Missing open time",
            expected: 12,
            eur: { feed: null, pins: [], count: 0, missing: [], error: null },
            gbp: { feed: null, pins: [], count: 0, missing: [], error: null },
            paired: 0,
            paired_ranges: [],
            chart_selections: [],
            pair_error: null,
            quotes: { count: 0, pins: [], gaps: [], error: null, registered: false },
          },
        ],
      },
    });
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Data Library", exact: true }).click();
  const panel = page.getByRole("region", { name: "Forex history", exact: true });
  await panel.getByLabel("Start date (UTC)", { exact: true }).fill("2026-11-13");
  await panel.getByLabel("Exclusive end date (UTC)", { exact: true }).fill("2026-11-14");
  await panel.getByRole("button", { name: "Inspect coverage" }).click();

  // Unequal totals: 11 EURUSD candles, 22 GBPUSD candles, 7 Paired UTC opens, 44 EURUSD quotes
  for (const [label, expected] of [
    ["EURUSD candles", 11],
    ["GBPUSD candles", 22],
    ["Paired UTC opens", 7],
    ["EURUSD quotes", 44],
  ] as const) {
    const card = panel.locator(".evidence-grid > div", { has: page.locator("dt", { hasText: label }) });
    await expect(card).toHaveCount(1);
    await expect(card.locator("dt")).toHaveText(label);
    await expect(card.locator("dd")).toHaveText(String(expected));
  }

  const rows = panel.locator("table tbody tr");
  await expect(rows).toHaveCount(5);
  for (const [rowIndex, label, toneClass] of [
    [0, "Healthy", "badge-green"],
    [1, "Conflicting evidence", "badge-red"],
    [2, "Scheduled closure", "badge-neutral"],
    [3, "Partial", "badge-amber"],
    [4, "Missing open time", "badge-amber"],
  ] as const) {
    const badge = rows.nth(rowIndex).locator(".badge");
    await expect(badge).toHaveText(label);
    await expect(badge).toBeVisible();
    await expect(badge).toHaveClass(new RegExp(`\\b${toneClass}\\b`));
  }
});

test("Decision review exposes one keyboard-reachable queue link on mobile and desktop", async ({ browser }) => {
  const reviewHandler = async (page: import("@playwright/test").Page) => {
    await page.route("**/research/api/learning/reviews/**", async route => {
      await route.fulfill({
        json: {
          phase: "ASSESSING",
          prompt_hash: "hash-1",
          prompt: {
            plan: { symbol: "EURUSD", direction: "BUY" },
            cutoff: null,
            lanes: [],
            plan_version_id: "pv-1",
            signal_reference: null,
          },
        },
      });
    });
    await page.route("**/research/api/learning/reviews", async route => {
      await route.fulfill({
        json: [{ trade: "TF-REV-1", version: 1, phase: "ASSESSING", symbol: "EURUSD" }],
      });
    });
  };

  // Phone viewport (390px): direct individual review URL -> tab to "Decision reviews" -> Enter opens queue
  const mobileCtx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const mobilePage = await mobileCtx.newPage();
  await reviewHandler(mobilePage);
  await mobilePage.goto("/?practice&trade=TF-REV-1&v=1");
  await expect(mobilePage.getByRole("heading", { name: "Review TF-REV-1 · v1" })).toBeVisible();

  const queueLinks = mobilePage.getByRole("link", { name: "Decision reviews", exact: true });
  await expect(queueLinks).toHaveCount(1);
  await expect(queueLinks.first()).toBeVisible();

  const overflow = await mobilePage.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);

  let reachedQueue = false;
  for (let i = 0; i < 6; i++) {
    await mobilePage.keyboard.press("Tab");
    reachedQueue = await mobilePage.evaluate(() => document.activeElement?.textContent?.trim() === "Decision reviews");
    if (reachedQueue) break;
  }
  expect(reachedQueue).toBe(true);
  await Promise.all([
    mobilePage.waitForURL(url => url.search === "?practice"),
    mobilePage.keyboard.press("Enter"),
  ]);
  await expect(mobilePage.getByRole("heading", { name: "Decision reviews", exact: true })).toBeVisible();
  await expect(mobilePage.getByRole("link", { name: /TF-REV-1/ })).toBeVisible();
  await mobileCtx.close();

  // Desktop viewport (1440px): breadcrumb visible with one focus stop for "Decision reviews"
  const desktopCtx = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const desktopPage = await desktopCtx.newPage();
  await reviewHandler(desktopPage);
  await desktopPage.goto("/?practice&trade=TF-REV-1&v=1");
  const breadcrumb = desktopPage.getByRole("navigation", { name: "Decision review breadcrumb" });
  await expect(breadcrumb).toBeVisible();
  await expect(breadcrumb.getByRole("link", { name: "Decision reviews", exact: true })).toHaveCount(1);
  await expect(desktopPage.getByRole("link", { name: "Decision reviews", exact: true })).toHaveCount(1);
  await desktopCtx.close();
});

test("Catalog returns to workspace preserving Data Library preference and clean-profile Overview", async ({ browser }) => {
  // Journey 1: From Data Library -> catalog -> Return to workspace -> Data Library reopens
  const ctx1 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page1 = await ctx1.newPage();
  await page1.goto("/");
  await page1.getByRole("button", { name: "Data Library", exact: true }).click();
  await expect(page1.getByRole("heading", { name: "Market data library" })).toBeVisible();
  await page1.getByRole("link", { name: "Open fixed-point catalog" }).click();
  await expect(page1.getByRole("button", { name: "Generate EURUSD simulation" })).toBeVisible();
  await expect(page1.getByText("Return to Terminal")).toHaveCount(0);
  const returnLink1 = page1.getByRole("link", { name: "Return to workspace", exact: true });
  await expect(returnLink1).toBeVisible();
  await returnLink1.click();
  await expect(page1.getByRole("heading", { name: "Market data library" })).toBeVisible();
  await ctx1.close();

  // Journey 2: Clean profile -> direct catalog -> Return to workspace -> Overview opens
  const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page2 = await ctx2.newPage();
  await page2.goto("/catalog.html");
  await expect(page2.getByRole("button", { name: "Generate EURUSD simulation" })).toBeVisible();
  await expect(page2.getByText("Return to Terminal")).toHaveCount(0);
  await page2.getByRole("link", { name: "Return to workspace", exact: true }).click();
  await expect(page2.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await ctx2.close();
});
