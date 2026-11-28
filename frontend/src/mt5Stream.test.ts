import { afterEach, describe, expect, it, vi } from 'vitest';
import { mergeMt5Candles, subscribeMt5Live } from './mt5Stream';
import type { CandleResponse } from './types';

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  url: string;
  readyState = FakeEventSource.CONNECTING;
  onerror: ((ev: Event) => void) | null = null;
  private listeners = new Map<string, Array<(ev: MessageEvent<string>) => void>>();
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: (ev: MessageEvent<string>) => void) {
    const list = this.listeners.get(type) ?? [];
    list.push(cb);
    this.listeners.set(type, list);
  }

  emit(type: string, payload: unknown) {
    if (this.closed) return;
    this.readyState = FakeEventSource.OPEN;
    const event = { data: JSON.stringify(payload) } as MessageEvent<string>;
    for (const cb of this.listeners.get(type) ?? []) cb(event);
  }

  triggerError() {
    this.onerror?.(new Event('error'));
  }

  close() {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }
}

const makeResp = (candles: CandleResponse['candles'], feed = 'mt5:demo'): CandleResponse => ({
  source: 'MT5',
  synthetic: false,
  price_basis: 'BID',
  feed_id: feed,
  candles,
  latest_unconfirmed: true,
});

describe('mt5Stream', () => {
  const originalEs = globalThis.EventSource;
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    vi.useRealTimers();
    FakeEventSource.instances = [];
    globalThis.EventSource = originalEs;
    globalThis.fetch = originalFetch;
  });

  it('merges same-bar revisions, new bars, feed switches, and ignores stale tails', () => {
    const base = makeResp([
      { time: 100, open: 1.0, high: 1.1, low: 0.9, close: 1.05 },
      { time: 400, open: 1.05, high: 1.06, low: 1.02, close: 1.04 },
    ]);

    const sameBar = mergeMt5Candles(base, makeResp([{ time: 400, open: 1.05, high: 1.09, low: 1.02, close: 1.08 }]), 200);
    expect(sameBar?.candles).toHaveLength(2);
    expect(sameBar?.candles[1].close).toBe(1.08);

    const nextBar = mergeMt5Candles(sameBar, makeResp([{ time: 700, open: 1.08, high: 1.12, low: 1.07, close: 1.11 }]), 200);
    expect(nextBar?.candles.map(b => b.time)).toEqual([100, 400, 700]);

    const stale = mergeMt5Candles(nextBar, makeResp([{ time: 400, open: 1, high: 1, low: 1, close: 1 }]), 200);
    expect(stale?.candles.at(-1)?.time).toBe(700);
  });

  it('streams SSE snapshots without polling, isolates post-close events, and switches cleanly to non-overlapping polling fallback', async () => {
    vi.useFakeTimers();
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => makeResp([{ time: 700, open: 1.1, high: 1.2, low: 1.0, close: 1.15 }]),
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const received: number[] = [];
    const quotes: Array<Record<string, number | null>> = [];
    const modes: string[] = [];

    const stop = subscribeMt5Live({
      symbol: 'EURUSD',
      timeframe: 'M5',
      quoteSymbols: ['EURUSD', 'GBPUSD'],
      onCandles: u => received.push(u.candles.at(-1)!.close),
      onQuotes: q => quotes.push(q),
      onTransportChange: m => modes.push(m),
      pollIntervalMs: 5000,
      quotePollIntervalMs: 10000,
      reconnectDelayMs: 15000,
    });

    expect(FakeEventSource.instances).toHaveLength(1);
    const es = FakeEventSource.instances[0];
    expect(modes).toEqual(['sse']);

    // While SSE is healthy, no polling requests happen
    await vi.advanceTimersByTimeAsync(12000);
    expect(fetchMock).not.toHaveBeenCalled();

    es.emit('snapshot', { candles: makeResp([{ time: 400, open: 1, high: 1.1, low: 0.9, close: 1.01 }]), quotes: { EURUSD: 1.01, GBPUSD: 1.25 } });
    es.emit('candles', { candles: makeResp([{ time: 400, open: 1, high: 1.1, low: 0.9, close: 1.02 }]) });
    es.emit('quote', { quotes: { EURUSD: 1.03, GBPUSD: 1.26 } });
    expect(received).toEqual([1.01, 1.02]);
    expect(quotes).toEqual([{ EURUSD: 1.01, GBPUSD: 1.25 }, { EURUSD: 1.03, GBPUSD: 1.26 }]);

    // Error shuts down EventSource before starting polling fallback (no overlap)
    es.triggerError();
    expect(es.closed).toBe(true);
    expect(modes.at(-1)).toBe('polling');

    // Quote poll ran once immediately on fallback; candle poll runs after 5000ms
    await vi.advanceTimersByTimeAsync(5000);
    expect(received).toEqual([1.01, 1.02, 1.15]);

    // Cleanup prevents late events from propagating
    stop();
    es.emit('candles', { candles: makeResp([{ time: 1000, open: 1, high: 1, low: 1, close: 9.99 }]) });
    await vi.advanceTimersByTimeAsync(10000);
    expect(received).toEqual([1.01, 1.02, 1.15]);
  });
});
