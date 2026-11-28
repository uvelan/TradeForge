import { expect, test } from "@playwright/test";

test("MT5 live SSE pushes same-bar revisions, new bars, and quotes, falls back without overlap, and isolates late events on replay or source switch", async ({ page }) => {
  const start = 1700000400;
  const baseCandles = Array.from({ length: 40 }, (_, i) => {
    const t = start + i * 300;
    const price = 1.08 + i * 0.0001;
    return { time: t, open: price, high: price + 0.0005, low: price - 0.0004, close: price + 0.0002, volume: 20 };
  });

  let candlePollRequests = 0;
  let quotePollRequests = 0;

  // Controlled EventSource mock in browser so we can drive snapshot, same-bar revision, new bar, error, and post-close late events deterministically.
  await page.addInitScript(() => {
    localStorage.setItem("tradeforge.workspace.page", "terminal");
    localStorage.setItem("tradeforge.workspace.source", "MT5");
    localStorage.setItem("tradeforge.workspace.timeframe", "M5");
    localStorage.setItem("tradeforge.workspace.chart-comparison", "none");

    class MockMt5EventSource {
      static instances: MockMt5EventSource[] = [];
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static readonly CLOSED = 2;

      url: string;
      readyState = MockMt5EventSource.CONNECTING;
      onerror: ((ev: Event) => void) | null = null;
      private listeners = new Map<string, Array<(ev: MessageEvent<string>) => void>>();
      closed = false;

      constructor(url: string) {
        this.url = url;
        MockMt5EventSource.instances.push(this);
      }

      addEventListener(type: string, cb: (ev: MessageEvent<string>) => void) {
        const list = this.listeners.get(type) ?? [];
        list.push(cb);
        this.listeners.set(type, list);
      }

      emit(type: string, data: unknown) {
        this.readyState = MockMt5EventSource.OPEN;
        const ev = { data: JSON.stringify(data) } as MessageEvent<string>;
        for (const cb of this.listeners.get(type) ?? []) cb(ev);
      }

      forceDispatchBypassingGuard(type: string, data: unknown) {
        const ev = { data: JSON.stringify(data) } as MessageEvent<string>;
        for (const cb of this.listeners.get(type) ?? []) cb(ev);
      }

      fail() {
        this.onerror?.(new Event("error"));
      }

      close() {
        this.closed = true;
        this.readyState = MockMt5EventSource.CLOSED;
      }
    }

    (window as any).__mockMt5Sse = MockMt5EventSource;
    (window as any).EventSource = MockMt5EventSource;
  });

  await page.route("**/research/api/**", async route => {
    const url = route.request().url();
    const method = route.request().method();
    if (url.includes("/market/mt5/symbols")) {
      return route.fulfill({ json: [{ name: "EURUSD", description: "Euro vs US Dollar", digits: 5 }] });
    }
    if (url.includes("/market/candles")) {
      candlePollRequests++;
      return route.fulfill({
        json: {
          source: "MT5",
          synthetic: false,
          price_basis: "BID",
          feed_id: "mt5:ctrl",
          candles: baseCandles,
          latest_unconfirmed: true,
        },
      });
    }
    if (url.includes("/market/quote")) {
      quotePollRequests++;
      const sym = new URL(url).searchParams.get("symbol") ?? "EURUSD";
      const bid = sym === "GBPUSD" ? 1.27111 : sym === "USDJPY" ? 151.111 : sym === "XAUUSD" ? 2650.11 : 1.0841;
      return route.fulfill({ json: { source: "MT5", synthetic: false, symbol: sym, bid, ask: bid + 0.0001, stale: false } });
    }
    if (url.includes("/replay/sessions") && method === "POST") {
      return route.fulfill({
        status: 201,
        json: {
          id: "mt5-replay-session",
          market: {
            source: "MT5",
            synthetic: false,
            price_basis: "BID",
            feed_id: "mt5:ctrl",
            candles: baseCandles,
          },
          views: {},
        },
      });
    }
    if (url.includes("/replay/sessions")) return route.fulfill({ json: [] });
    if (url.includes("/drawings")) return route.fulfill({ json: { schema_version: 1, revision: 0, items: [] } });
    if (url.includes("/overview")) {
      return route.fulfill({
        json: { analytics: { trades: 0, active_plans: 0, closed: 0, win_rate: null, expectancy_r: null, net_pnl_usd: "0", profit_factor: null, note: "" }, mt5: { connected: true }, datasets: [], recent_trades: [], recent_reviews: [], journal_count: 0 },
      });
    }
    return route.fulfill({ json: [] });
  });

  await page.goto("/");
  await expect(page.locator(".market-watchlist")).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as any).__mockMt5Sse.instances.length)).toBeGreaterThanOrEqual(1);
  const candleRequestsAtConnect = candlePollRequests;
  const quotesBeforePush = quotePollRequests;

  // Initial SSE snapshot, same-bar revision, and new bar update live price and watchlist without HTTP polling
  await page.evaluate(bars => {
    const es = (window as any).__mockMt5Sse.instances.at(-1);
    const last = bars.at(-1);
    es.emit("snapshot", {
      candles: { source: "MT5", synthetic: false, price_basis: "BID", feed_id: "mt5:ctrl", candles: [last], latest_unconfirmed: true },
      quotes: { EURUSD: 1.09123, GBPUSD: 1.27456, USDJPY: 151.234, XAUUSD: 2666.5 },
    });
  }, baseCandles);

  await expect(page.locator(".market-watchlist")).toContainText("1.09123");
  await expect(page.locator(".terminal-identity")).toContainText("live stream");

  // Same-bar revision on bar 39
  await page.evaluate(last => {
    const es = (window as any).__mockMt5Sse.instances.at(-1);
    es.emit("candles", {
      candles: {
        source: "MT5",
        synthetic: false,
        price_basis: "BID",
        feed_id: "mt5:ctrl",
        candles: [{ ...last, high: last.high + 0.002, close: 1.09333 }],
        latest_unconfirmed: true,
      },
    });
  }, baseCandles.at(-1)!);
  await expect(page.locator(".terminal-head-price")).toContainText("1.09333");

  // New bar appended (t + 300)
  await page.evaluate(last => {
    const es = (window as any).__mockMt5Sse.instances.at(-1);
    const next = { time: last.time + 300, open: 1.09333, high: 1.095, low: 1.093, close: 1.09444, volume: 12 };
    es.emit("candles", {
      candles: { source: "MT5", synthetic: false, price_basis: "BID", feed_id: "mt5:ctrl", candles: [next], latest_unconfirmed: true },
    });
    es.emit("quote", { quotes: { EURUSD: 1.09444, GBPUSD: 1.275, USDJPY: 151.3, XAUUSD: 2667.0 } });
  }, baseCandles.at(-1)!);
  await expect(page.locator(".terminal-head-price")).toContainText("1.09444");
  await expect(page.locator(".market-watchlist")).toContainText("1.09444");
  expect(candlePollRequests).toBe(candleRequestsAtConnect);
  expect(quotePollRequests).toBe(quotesBeforePush);

  // Enter frozen replay -> EventSource closes and late SSE frames are isolated
  const frozenBeforeReplay = await page.locator(".terminal-head-price").innerText();
  await page.getByRole("button", { name: "Bar replay" }).click();
  await expect(page.getByRole("region", { name: "Historical replay" })).toBeVisible();
  const isClosedDuringReplay = await page.evaluate(() => (window as any).__mockMt5Sse.instances.every((i: any) => i.closed));
  expect(isClosedDuringReplay).toBe(true);

  await page.evaluate(last => {
    const es = (window as any).__mockMt5Sse.instances.at(-1);
    es.forceDispatchBypassingGuard("candles", {
      candles: {
        source: "MT5",
        synthetic: false,
        price_basis: "BID",
        feed_id: "mt5:ctrl",
        candles: [{ time: last.time + 600, open: 9, high: 9.5, low: 8.5, close: 9.12345 }],
        latest_unconfirmed: true,
      },
    });
  }, baseCandles.at(-1)!);
  await expect(page.locator(".terminal-head-price")).not.toContainText("9.12345");

  // Exit replay -> fresh EventSource opens, then simulate stream failure -> falls back cleanly to polling
  await page.getByRole("button", { name: "Exit replay" }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__mockMt5Sse.instances.filter((i: any) => !i.closed).length)).toBe(1);
  expect(frozenBeforeReplay).toBeTruthy();

  await page.evaluate(() => (window as any).__mockMt5Sse.instances.at(-1).fail());
  await expect(page.locator(".terminal-identity")).toContainText("polling fallback");
  await expect.poll(() => quotePollRequests).toBeGreaterThan(quotesBeforePush);

  // Switch source to DEMO -> EventSource stays closed
  await page.getByLabel("Market data source").selectOption("DEMO");
  const allClosedOnDemo = await page.evaluate(() => (window as any).__mockMt5Sse.instances.every((i: any) => i.closed));
  expect(allClosedOnDemo).toBe(true);
});
