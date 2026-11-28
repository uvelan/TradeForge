import { api } from './api';
import type { CandleResponse, Timeframe } from './types';

export type Mt5TransportMode = 'idle' | 'sse' | 'polling';

export interface Mt5LiveOptions {
  symbol: string;
  timeframe: Timeframe;
  limit?: number;
  includeLatest?: boolean;
  quoteSymbols?: readonly string[];
  onCandles: (update: CandleResponse) => void;
  onQuotes?: (quotes: Record<string, number | null>) => void;
  onError?: (message: string) => void;
  onTransportChange?: (mode: Mt5TransportMode) => void;
  pollIntervalMs?: number;
  quotePollIntervalMs?: number;
  reconnectDelayMs?: number;
}

export function mergeMt5Candles(
  current: CandleResponse | null | undefined,
  update: CandleResponse,
  maxBars: number,
): CandleResponse | null | undefined {
  if (!update.candles.length) return current;
  if (!current) return { ...update, candles: update.candles.slice(-maxBars) };
  if (current.feed_id !== update.feed_id || current.price_basis !== update.price_basis) {
    return { ...update, candles: update.candles.slice(-maxBars) };
  }
  const first = update.candles[0].time;
  const lastCurrent = current.candles.at(-1)?.time ?? 0;
  const lastUpdate = update.candles.at(-1)!.time;
  if (lastUpdate < lastCurrent) return current;
  const candles = [...current.candles.filter(bar => bar.time < first), ...update.candles].slice(-maxBars);
  return { ...update, candles };
}

export function mt5StreamUrl(options: {
  symbol: string;
  timeframe: Timeframe;
  limit: number;
  includeLatest: boolean;
  quoteSymbols: readonly string[];
}): string {
  const params = new URLSearchParams({
    symbol: options.symbol,
    timeframe: options.timeframe,
    limit: String(options.limit),
    include_latest: String(options.includeLatest),
  });
  if (options.quoteSymbols.length > 0) {
    params.set('quote_symbols', options.quoteSymbols.join(','));
  }
  return '/research/api/market/mt5/stream?' + params.toString();
}

export function subscribeMt5Live(options: Mt5LiveOptions): () => void {
  const limit = options.limit ?? 10;
  const includeLatest = options.includeLatest ?? true;
  const quoteSymbols = options.quoteSymbols ?? [];
  const pollIntervalMs = options.pollIntervalMs ?? 5000;
  const quotePollIntervalMs = options.quotePollIntervalMs ?? 10000;
  const reconnectDelayMs = options.reconnectDelayMs ?? 15000;

  let closed = false;
  let source: EventSource | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let quoteTimer: ReturnType<typeof setInterval> | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let pollInFlight = false;
  let quoteInFlight = false;
  let transport: Mt5TransportMode = 'idle';

  function setTransport(next: Mt5TransportMode) {
    if (transport === next) return;
    transport = next;
    options.onTransportChange?.(next);
  }

  function stopPolling() {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    if (quoteTimer !== null) {
      clearInterval(quoteTimer);
      quoteTimer = null;
    }
  }

  function stopSse() {
    if (source) {
      source.close();
      source = null;
    }
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  }

  async function pollCandlesOnce() {
    if (closed || transport !== 'polling' || pollInFlight || (typeof document !== 'undefined' && document.hidden)) return;
    pollInFlight = true;
    try {
      const update = await api.candles(options.symbol, options.timeframe, 'MT5', limit, includeLatest);
      if (!closed && transport === 'polling') {
        options.onCandles(update);
      }
    } catch {
      if (!closed && transport === 'polling') {
        options.onError?.('MT5 update unavailable · showing last received bars');
      }
    } finally {
      pollInFlight = false;
    }
  }

  async function pollQuotesOnce() {
    if (closed || transport !== 'polling' || !quoteSymbols.length || quoteInFlight) return;
    quoteInFlight = true;
    try {
      const entries = await Promise.all(
        quoteSymbols.map(async symbol => {
          try {
            const quote = await api.quote(symbol as never, 'MT5');
            return [symbol, quote.bid] as const;
          } catch {
            return [symbol, null] as const;
          }
        }),
      );
      if (!closed && transport === 'polling') {
        options.onQuotes?.(Object.fromEntries(entries));
      }
    } finally {
      quoteInFlight = false;
    }
  }

  function startPollingFallback() {
    if (closed) return;
    // Enforce single active transport: SSE must be closed before polling starts.
    stopSse();
    setTransport('polling');
    pollTimer = setInterval(() => {
      void pollCandlesOnce();
    }, pollIntervalMs);
    if (quoteSymbols.length > 0 && options.onQuotes) {
      void pollQuotesOnce();
      quoteTimer = setInterval(() => {
        void pollQuotesOnce();
      }, quotePollIntervalMs);
    }
    if (typeof EventSource !== 'undefined' && reconnectDelayMs > 0) {
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (!closed) startSse();
      }, reconnectDelayMs);
    }
  }

  function parsePayload(raw: string): { candles?: CandleResponse; quotes?: Record<string, number | null>; error?: string } | null {
    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  function startSse() {
    if (closed) return;
    if (typeof EventSource === 'undefined') {
      startPollingFallback();
      return;
    }
    // Never overlap polling with an active/connecting EventSource.
    stopPolling();
    stopSse();
    setTransport('sse');
    const es = new EventSource(mt5StreamUrl({
      symbol: options.symbol,
      timeframe: options.timeframe,
      limit,
      includeLatest,
      quoteSymbols,
    }));
    source = es;

    const handleFrame = (event: MessageEvent<string>) => {
      if (closed || source !== es) return;
      const data = parsePayload(event.data);
      if (!data) return;
      if (data.candles) options.onCandles(data.candles);
      if (data.quotes && options.onQuotes) options.onQuotes(data.quotes);
    };

    es.addEventListener('snapshot', handleFrame as EventListener);
    es.addEventListener('candles', handleFrame as EventListener);
    es.addEventListener('quote', handleFrame as EventListener);
    es.addEventListener('mt5_error', ((event: MessageEvent<string>) => {
      if (closed || source !== es) return;
      const data = parsePayload(event.data);
      if (data?.error) options.onError?.(data.error);
    }) as EventListener);

    es.onerror = () => {
      if (closed || source !== es) return;
      startPollingFallback();
    };
  }

  startSse();

  return () => {
    closed = true;
    setTransport('idle');
    stopPolling();
    stopSse();
  };
}
