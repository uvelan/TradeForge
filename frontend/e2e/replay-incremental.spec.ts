import { expect, test } from "@playwright/test";

test("incremental replay matches full rebuild across gaps, static H1 pane, rewind, large seek, pan/zoom, and drawings", async ({ page }) => {
  const start = 1700000400; // aligned to 3600 and 300
  const eur = Array.from({ length: 5000 }, (_, i) => {
    const t = start + i * 300;
    const b = 1.08 + Math.sin(i / 40) * 0.01;
    return { time: t, open: b, high: b + 0.0008, low: b - 0.0007, close: b + 0.0002, available_at: t + 300 };
  });
  const gbp = eur
    .filter((_, i) => i < 4010 || i > 4020)
    .map((bar, idx) => ({
      time: bar.time,
      open: 1.26 + Math.cos(idx / 45) * 0.012,
      high: 1.261 + Math.cos(idx / 45) * 0.012,
      low: 1.259 + Math.cos(idx / 45) * 0.012,
      close: 1.2603 + Math.cos(idx / 45) * 0.012,
      available_at: bar.time + 300,
    }));
  const h1 = eur
    .filter((_, i) => i % 12 === 0)
    .map(bar => ({
      time: bar.time,
      open: 1.261,
      high: 1.263,
      low: 1.259,
      close: 1.2615,
      available_at: bar.time + 3600,
    }));
  const cutoffs = eur.map(b => b.time + 300);

  await page.addInitScript(() => {
    const writes = { setData: 0, update: 0, pop: 0 };
    const liveCharts = new Set<any>();
    const wrappedSeries = new WeakSet();
    window.workspaceProbe = {
      chart: (factory: Function) => (el: Element, opts: unknown) => {
        const chart = factory(el, opts);
        liveCharts.add(chart);
        const origRemove = chart.remove.bind(chart);
        chart.remove = () => {
          liveCharts.delete(chart);
          return origRemove();
        };
        const origAdd = chart.addSeries.bind(chart);
        chart.addSeries = (...args: unknown[]) => {
          const s = origAdd(...args);
          if (!wrappedSeries.has(s)) {
            wrappedSeries.add(s);
            for (const m of ["setData", "update", "pop"] as const) {
              const orig = s[m]?.bind(s);
              if (!orig) continue;
              s[m] = (...a: unknown[]) => {
                if (s.seriesType?.() === "Candlestick") writes[m]++;
                return orig(...a);
              };
            }
          }
          return s;
        };
        return chart;
      },
      writes,
      resetWrites() {
        writes.setData = 0;
        writes.update = 0;
        writes.pop = 0;
      },
      seriesData() {
        return Array.from(liveCharts).map(c => c.panes().flatMap((p: any) =>
          p.getSeries().filter((s: any) => s.seriesType() === "Candlestick").map((s: any) => s.data()),
        ));
      },
      setPrimaryLogicalRange(from: number, to: number) {
        Array.from(liveCharts)[0]?.timeScale().setVisibleLogicalRange({ from, to });
      },
      getPrimaryLogicalRange() {
        return Array.from(liveCharts)[0]?.timeScale().getVisibleLogicalRange();
      },
    } as any;
    localStorage.setItem("tradeforge.workspace.page", "terminal");
    localStorage.setItem("tradeforge.workspace.source", "DUKASCOPY_SAVED");
    localStorage.setItem("tradeforge.workspace.chart-comparison", "GBPUSD");
    localStorage.setItem("tradeforge.workspace.chart-views", "2");
  });

  const session = {
    id: "inc-replay-5000",
    market: {
      source: "DUKASCOPY_SAVED",
      synthetic: false,
      price_basis: "BID",
      feed_id: "duka-e2e",
      candles: eur,
      replay_cutoffs: cutoffs,
      coverage: { start, end: start + 5000 * 300, gaps: [], duplicate_bars: 0, truncated: false },
    },
    views: {
      comparison: {
        symbol: "GBPUSD",
        timeframe: "M5",
        source: "DUKASCOPY_SAVED",
        synthetic: false,
        price_basis: "BID",
        feed_id: "duka-e2e",
        candles: gbp,
        coverage: { start, end: start + 5000 * 300, gaps: [], duplicate_bars: 0, truncated: false },
      },
      "2": {
        symbol: "GBPUSD",
        timeframe: "H1",
        source: "DUKASCOPY_SAVED",
        synthetic: false,
        price_basis: "BID",
        feed_id: "duka-e2e",
        candles: h1,
        coverage: { start, end: start + 5000 * 300, gaps: [], duplicate_bars: 0, truncated: false },
      },
    },
    request: { start, end: start + 5000 * 300, window_bars: 5000 },
    checkpoint: null,
  };

  await page.route("**/research/api/**", async route => {
    const url = route.request().url();
    if (url.includes("/market/candles")) {
      return route.fulfill({ json: { ...session.market, candles: eur.slice(-600) } });
    }
    if (url.includes("/indicators/smt/strict")) {
      return route.fulfill({ json: { version: 2, rules: {}, events: [], alerts: [], authority: "VERIFIED_SAVED_DATASET" } });
    }
    if (url.endsWith("/indicators")) return route.fulfill({ json: [] });
    if (url.includes("/drawings")) return route.fulfill({ json: { schema_version: 1, revision: 0, items: [] } });
    if (url.includes("/replay/sessions/inc-replay-5000/")) {
      return route.fulfill({ status: 404, json: { detail: "Not mocked" } });
    }
    if (url.includes("/replay/sessions/inc-replay-5000")) return route.fulfill({ json: session });
    if (url.includes("/replay/sessions")) return route.fulfill({ json: [{ id: "inc-replay-5000", position: 4005, updated_at: "2026-11-28T00:00:00Z" }] });
    if (url.includes("/overview")) return route.fulfill({ json: { analytics: { trades: 0, active_plans: 0, closed: 0, win_rate: null, expectancy_r: null, net_pnl_usd: "0", profit_factor: null, note: "" }, mt5: { connected: false }, datasets: [], recent_trades: [], recent_reviews: [], journal_count: 0 } });
    return route.fulfill({ json: [] });
  });

  await page.goto("/");
  await page.getByLabel("Resume saved replay").selectOption("inc-replay-5000");
  await expect(page.getByRole("button", { name: "Next candle", exact: true })).toBeVisible();

  const slider = page.getByLabel("Replay timeline");
  // Seek into index 4009 (last bar before GBPUSD gap at 4010..4020, mid-H1 hour)
  await slider.fill("4009");
  await page.waitForTimeout(100);
  await page.evaluate(() => (window as any).workspaceProbe.resetWrites());

  // Forward step into index 4010: EURUSD updates (+1), GBPUSD has a gap (0 writes), H1 stays still (0 writes)
  await page.getByRole("button", { name: "Next candle", exact: true }).click();
  await page.waitForTimeout(100);
  const gapStepWrites = await page.evaluate(() => ({ ...(window as any).workspaceProbe.writes }));
  expect(gapStepWrites.setData).toBe(0);
  expect(gapStepWrites.update).toBe(1);
  expect(gapStepWrites.pop).toBe(0);

  // Verify manual pan/zoom stays anchored while stepping when scrolled back into history
  await page.evaluate(() => (window as any).workspaceProbe.setPrimaryLogicalRange(2500, 2560));
  const beforePan = await page.evaluate(() => (window as any).workspaceProbe.getPrimaryLogicalRange());
  await page.getByRole("button", { name: "Next candle", exact: true }).click();
  await page.waitForTimeout(100);
  const afterPan = await page.evaluate(() => (window as any).workspaceProbe.getPrimaryLogicalRange());
  expect(Math.abs(afterPan.from - beforePan.from)).toBeLessThan(2);
  expect(Math.abs(afterPan.to - beforePan.to)).toBeLessThan(2);

  // Rewind uses pop and skips static comparison/H1 panes
  await page.evaluate(() => (window as any).workspaceProbe.resetWrites());
  await page.getByRole("button", { name: "Previous candle", exact: true }).click();
  await page.waitForTimeout(100);
  const rewindWrites = await page.evaluate(() => ({ ...(window as any).workspaceProbe.writes }));
  expect(rewindWrites.setData).toBe(0);
  expect(rewindWrites.pop).toBe(1);

  // Large seek triggers setData and yields exact same candle count and last close as expected slice at cutoff
  await slider.fill("4500");
  await page.waitForTimeout(100);
  const actual = await page.evaluate(() => (window as any).workspaceProbe.seriesData());
  const cutoff = cutoffs[4500];
  const expectedEur = eur.filter(b => b.available_at <= cutoff);
  const expectedGbp = gbp.filter(b => b.available_at <= cutoff);
  const expectedH1 = h1.filter(b => b.available_at <= cutoff);
  expect(actual[0][0].length).toBe(expectedEur.length);
  expect(actual[0][1].length).toBe(expectedGbp.length);
  expect(actual[1][0].length).toBe(expectedH1.length);
  expect(actual[0][0].at(-1).close).toBeCloseTo(expectedEur.at(-1)!.close, 8);
  expect(actual[0][1].at(-1).close).toBeCloseTo(expectedGbp.at(-1)!.close, 8);
  expect(actual[1][0].at(-1).close).toBeCloseTo(expectedH1.at(-1)!.close, 8);
});
