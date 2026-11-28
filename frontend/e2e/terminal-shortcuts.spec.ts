import { expect, test } from "@playwright/test";

test("terminal keyboard shortcuts fit focused pane, bookmark inspected candle across restart, switch tools and timeframes safely", async ({ browser }) => {
  const start = 1700000400; // aligned to M5 (300) and H1 (3600)
  // Build 120 M5 bars of EURUSD (0..599 min) and 10 H1 bars of GBPUSD
  const eur = Array.from({ length: 120 }, (_, i) => {
    const t = start + i * 300;
    const base = 1.08 + i * 0.0001;
    return { time: t, open: base, high: base + 0.0005, low: base - 0.0004, close: base + 0.0002, available_at: t + 300 };
  });
  const gbpH1 = Array.from({ length: 10 }, (_, i) => {
    const t = start + i * 3600;
    const base = 1.26 + i * 0.001;
    return { time: t, open: base, high: base + 0.0012, low: base - 0.001, close: base + 0.0004, available_at: t + 3600 };
  });
  const cutoffs = eur.map(b => b.time + 300);
  const sessionId = "shortcut-bookmark-session";

  // Persist checkpoint on mocked server across browser context restarts
  let savedCheckpoint: { position: number; bookmarks: number[]; selected?: { view: string; time: number } } | null = null;
  let drawPostCount = 0;

  const installRoutes = async (page: import("@playwright/test").Page) => {
    await page.addInitScript(() => {
      const liveCharts = new Map<string, any>();
      window.workspaceProbe = {
        chart: (factory: Function) => (el: Element, opts: unknown) => {
          const chart = factory(el, opts);
          const origRemove = chart.remove.bind(chart);
          chart.remove = () => {
            for (const [k, v] of liveCharts) if (v === chart) liveCharts.delete(k);
            return origRemove();
          };
          queueMicrotask(() => {
            const pane = el.parentElement?.closest("[aria-label]")?.getAttribute("aria-label") || "chart";
            liveCharts.set(pane, chart);
          });
          return chart;
        },
        setLogicalRange: (ariaLabel: string, from: number, to: number) => {
          liveCharts.get(ariaLabel)?.timeScale().setVisibleLogicalRange({ from, to });
        },
        getLogicalRange: (ariaLabel: string) => {
          return liveCharts.get(ariaLabel)?.timeScale().getVisibleLogicalRange();
        },
      } as any;
    });

    const sessionPayload = () => ({
      id: sessionId,
      market: {
        source: "DUKASCOPY_SAVED",
        synthetic: false,
        price_basis: "BID",
        feed_id: "duka-k",
        candles: eur,
        replay_cutoffs: cutoffs,
        coverage: { start, end: start + 120 * 300, gaps: [], duplicate_bars: 0, truncated: false },
      },
      views: {
        "2": {
          symbol: "GBPUSD",
          timeframe: "H1",
          source: "DUKASCOPY_SAVED",
          synthetic: false,
          price_basis: "BID",
          feed_id: "duka-k",
          candles: gbpH1,
          coverage: { start, end: start + 120 * 300, gaps: [], duplicate_bars: 0, truncated: false },
        },
      },
      request: { start, end: start + 120 * 300, window_bars: 5000 },
      checkpoint: savedCheckpoint,
    });

    await page.route("**/research/api/**", async route => {
      const url = route.request().url();
      const method = route.request().method();
      if (url.includes("/market/candles")) {
        const reqSource = new URL(url).searchParams.get("source") ?? "DEMO";
        const reqFrame = new URL(url).searchParams.get("timeframe") ?? "M5";
        return route.fulfill({ json: { ...sessionPayload().market, source: reqSource, timeframe: reqFrame, candles: eur } });
      }
      if (url.endsWith("/indicators")) return route.fulfill({ json: [] });
      if (url.includes("/drawings")) {
        if (method === "POST") drawPostCount++;
        return route.fulfill({ json: { schema_version: 1, revision: 0, items: [] } });
      }
      if (url.includes(`/replay/sessions/${sessionId}/checkpoint`) && method === "POST") {
        savedCheckpoint = route.request().postDataJSON();
        return route.fulfill({ json: savedCheckpoint });
      }
      if (url.includes(`/replay/sessions/${sessionId}/`)) {
        return route.fulfill({ status: 404, json: { detail: "Skipped" } });
      }
      if (url.includes(`/replay/sessions/${sessionId}`)) {
        return route.fulfill({ json: sessionPayload() });
      }
      if (url.includes("/replay/sessions")) {
        return route.fulfill({
          json: savedCheckpoint
            ? [{ id: sessionId, position: savedCheckpoint.position, updated_at: "2026-11-28T12:00:00Z" }]
            : [],
        });
      }
      if (url.includes("/chart-snapshots") && method === "POST") {
        return route.fulfill({ status: 201, json: { id: "snap-1" } });
      }
      if (url.includes("/overview")) {
        return route.fulfill({
          json: { analytics: { trades: 0, active_plans: 0, closed: 0, win_rate: null, expectancy_r: null, net_pnl_usd: "0", profit_factor: null, note: "" }, mt5: { connected: false }, datasets: [], recent_trades: [], recent_reviews: [], journal_count: 0 },
        });
      }
      return route.fulfill({ json: [] });
    });
  };

  // Context 1: Multi-pane Demo / Archive shortcuts + E bookmark on earlier GBPUSD H1 candle while EURUSD is ahead
  const ctx1 = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page1 = await ctx1.newPage();
  await installRoutes(page1);
  await page1.addInitScript(() => {
    localStorage.setItem("tradeforge.workspace.page", "terminal");
    localStorage.setItem("tradeforge.workspace.source", "DEMO");
    localStorage.setItem("tradeforge.workspace.timeframe", "M5");
    localStorage.setItem("tradeforge.workspace.chart-views", "2");
    localStorage.setItem("tradeforge.workspace.chart-comparison", "none");
  });
  await page1.goto("/");
  await expect(page1.getByRole("region", { name: "Primary chart" }).locator("canvas").first()).toBeVisible();
  await expect(page1.getByRole("region", { name: "Chart view 2" }).locator("canvas").first()).toBeVisible();

  // Verify published shortcuts in UI
  await expect(page1.locator(".watch-shortcuts")).toContainText("Fit focused chart");
  await expect(page1.locator(".watch-shortcuts")).toContainText("Bookmark evidence candle");

  // Unfrozen timeframe key updates ONLY the focused pane; focus View 2 -> press '3' (M5)
  await page1.getByRole("region", { name: "Chart view 2" }).locator(".chart-input").click({ position: { x: 40, y: 40 } });
  await page1.keyboard.press("3");
  await expect(page1.getByLabel("View 2 timeframe")).toHaveValue("M5");
  // Primary remains M5, change primary back focus and press '4' (M15) -> View 2 stays M5
  await page1.getByRole("region", { name: "Primary chart" }).locator(".chart-input").click({ position: { x: 40, y: 40 } });
  await page1.keyboard.press("4");
  await expect(page1.locator(".primary-chart .pane-label")).toContainText("M15");
  await expect(page1.getByLabel("View 2 timeframe")).toHaveValue("M5");

  // Zoom View 2 to tight range, hover View 2 and press 'F' -> View 2 fits all bars, Primary keeps tight zoom
  await page1.evaluate(() => {
    (window as any).workspaceProbe.setLogicalRange("Primary chart", 80, 100);
    (window as any).workspaceProbe.setLogicalRange("Chart view 2", 80, 100);
  });
  await page1.getByRole("region", { name: "Chart view 2" }).locator(".chart-input").hover();
  await page1.keyboard.press("f");
  await expect.poll(async () => {
    const r2 = await page1.evaluate(() => (window as any).workspaceProbe.getLogicalRange("Chart view 2"));
    return r2 ? r2.to - r2.from : 0;
  }).toBeGreaterThan(40);
  const r1AfterFit2 = await page1.evaluate(() => (window as any).workspaceProbe.getLogicalRange("Primary chart"));
  expect(r1AfterFit2.to - r1AfterFit2.from).toBeLessThan(30);

  // Guard checks: editable field + modal dialog + held repeat key do NOT trigger shortcuts
  await page1.getByRole("button", { name: "Commands" }).click();
  await page1.keyboard.press("g");
  await expect(page1.getByText(/Fibonacci retracement · select/)).toHaveCount(0);
  await page1.getByRole("button", { name: "Close" }).click();

  await page1.evaluate(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "g", repeat: true, bubbles: true })));
  await expect(page1.getByText(/Fibonacci retracement · select/)).toHaveCount(0);

  // Common drawing tool shortcut 'g' arms Fibonacci retracement (one keypress -> one action)
  await page1.getByRole("region", { name: "Primary chart" }).locator(".chart-input").click({ position: { x: 40, y: 40 } });
  await page1.keyboard.press("g");
  await expect(page1.getByText(/Fibonacci retracement · select 100% swing origin/)).toBeVisible();
  await page1.keyboard.press("Escape");
  await expect(page1.getByText(/Fibonacci retracement · select/)).toHaveCount(0);
  expect(drawPostCount).toBe(0);

  // Switch to saved archive session via Saved Replay & test frozen layout + E bookmark on earlier GBPUSD candle
  await page1.evaluate(() => {
    localStorage.setItem("tradeforge.workspace.source", "DUKASCOPY_SAVED");
    localStorage.setItem("tradeforge.workspace.timeframe", "M5");
    localStorage.setItem("tradeforge.workspace.chart-views", "2");
  });
  await page1.reload();
  // Create/load session directly by triggering Load saved range or resume mock
  savedCheckpoint = { position: 95, bookmarks: [], selected: { view: "primary", time: eur[95].time } };
  await page1.reload();
  await page1.getByLabel("Resume saved replay").selectOption(sessionId);
  await expect(page1.getByLabel("Inspection pane")).toHaveValue("primary");

  // Select View 2 (GBPUSD H1), select an EARLIER GBPUSD candle (index 2 = 2 hours in), while EURUSD replay cutoff is at index 95 (nearly 8 hours ahead)
  await page1.getByLabel("Inspection pane").selectOption("2");
  const earlierGbpTime = gbpH1[2].time;
  const aheadCutoffIso = new Date(cutoffs[95] * 1000).toISOString();
  // Editable-field guard: focus is inside Inspect candle input; pressing 'g' or 'e' must NOT trigger chart shortcuts
  await page1.getByLabel("Inspect candle", { exact: true }).fill("2");
  await page1.keyboard.press("e");
  await expect(page1.getByText(/Bookmarked GBPUSD H1/)).toHaveCount(0);

  // Blur input and hover chart pane (clicking a bar selects that bar for inspection)
  await page1.getByLabel("Inspect candle", { exact: true }).blur();
  await page1.getByRole("region", { name: "Chart view 2" }).locator(".chart-input").hover();
  await expect(page1.locator(".archive-replay-readout time").first()).toHaveAttribute(
    "datetime",
    new Date(earlierGbpTime * 1000).toISOString(),
  );

  // Frozen-layout restriction: pressing timeframe key '1' does NOT alter replay timeframe
  await page1.keyboard.press("1");
  await expect(page1.locator(".primary-chart .pane-label")).toContainText("M5");

  // Press E to bookmark inspected GBPUSD candle + ahead EURUSD cutoff
  const cpWait = page1.waitForResponse(r => r.url().includes("/checkpoint") && r.request().method() === "POST");
  await page1.keyboard.press("e");
  await cpWait;
  await expect(page1.getByText(/Bookmarked GBPUSD H1/)).toBeVisible();
  await expect(page1.locator(".cutoff-readout time")).toHaveAttribute("datetime", aheadCutoffIso);
  await ctx1.close();

  // Context 2 (fresh restart, clean storage): reopen from persisted backend checkpoint
  const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  const page2 = await ctx2.newPage();
  await installRoutes(page2);
  await page2.addInitScript(() => {
    localStorage.setItem("tradeforge.workspace.page", "terminal");
    localStorage.setItem("tradeforge.workspace.source", "DUKASCOPY_SAVED");
    localStorage.setItem("tradeforge.workspace.timeframe", "M5");
  });
  await page2.goto("/");
  await page2.getByLabel("Resume saved replay").selectOption(sessionId);
  await expect(page2.getByLabel("Inspection pane")).toHaveValue("2");
  await expect(page2.locator(".cutoff-readout time")).toHaveAttribute("datetime", aheadCutoffIso);
  await expect(page2.locator(".archive-replay-readout time").first()).toHaveAttribute(
    "datetime",
    new Date(earlierGbpTime * 1000).toISOString(),
  );
  await ctx2.close();
});
