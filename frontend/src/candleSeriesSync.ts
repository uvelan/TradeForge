import { useRef } from 'react';
import type { CandlestickData, UTCTimestamp } from 'lightweight-charts';
import type { Candle } from './types';

export function useStableCandles(candles: Candle[]): Candle[];
export function useStableCandles(candles: Candle[] | undefined): Candle[] | undefined;
export function useStableCandles(candles: Candle[] | undefined): Candle[] | undefined {
  const ref = useRef<Candle[] | undefined>(candles);
  if (candles === undefined) {
    ref.current = undefined;
  } else if (!ref.current || !sameCandleSequence(ref.current, candles)) {
    ref.current = candles;
  }
  return ref.current;
}

export type SyncMode = 'noop' | 'update' | 'pop' | 'setData';

export interface SeriesSyncResult {
  mode: SyncMode;
  writes: number;
  candles: Candle[];
}

export type CandlestickSeriesTarget = {
  setData(data: CandlestickData<UTCTimestamp>[]): void;
  update(bar: CandlestickData<UTCTimestamp>, historicalUpdate?: boolean): void;
  pop(count: number): unknown;
};

const normalizedCache = new WeakMap<readonly Candle[], Candle[]>();
const alreadyNormalized = new WeakSet<readonly Candle[]>();

export function markNormalizedCandles(candles: readonly Candle[]): Candle[] {
  alreadyNormalized.add(candles);
  return candles as Candle[];
}

/** Normalize an array to strictly ascending, deduplicated-by-time candles, caching immutable inputs. */
export function normalizeCandles(candles: readonly Candle[]): Candle[] {
  if (alreadyNormalized.has(candles)) return candles as Candle[];
  const cached = normalizedCache.get(candles);
  if (cached) return cached;

  let strictlyAscending = true;
  for (let i = 1; i < candles.length; i++) {
    if (candles[i].time <= candles[i - 1].time) {
      strictlyAscending = false;
      break;
    }
  }

  let result: Candle[];
  if (strictlyAscending) {
    result = candles as Candle[];
  } else {
    const sorted = [...candles].sort((a, b) => a.time - b.time);
    result = [];
    for (let i = 0; i < sorted.length; i++) {
      if (i === 0 || sorted[i - 1].time !== sorted[i].time) {
        result.push(sorted[i]);
      }
    }
  }

  normalizedCache.set(candles, result);
  alreadyNormalized.add(result);
  return result;
}

export function barsEqual(a: Candle, b: Candle): boolean {
  if (a === b) return true;
  return (
    a.time === b.time &&
    a.open === b.open &&
    a.high === b.high &&
    a.low === b.low &&
    a.close === b.close
  );
}

export function sameCandleSequence(a: readonly Candle[], b: readonly Candle[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (!barsEqual(a[i], b[i])) return false;
  }
  return true;
}

function toOhlc(bar: Candle): CandlestickData<UTCTimestamp> {
  return {
    time: bar.time as UTCTimestamp,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
  };
}

const MAX_INCREMENTAL_DELTA = 64;
const MAX_SUFFIX_REWRITE = 16;

/**
 * Synchronize a lightweight-charts candlestick series incrementally against the previous committed bars.
 * Guarantees the resulting bars match `setData(next)` identically across noop, update, pop, and replacement.
 */
export function syncCandlestickSeries(
  series: CandlestickSeriesTarget,
  prev: readonly Candle[],
  rawNext: readonly Candle[],
  options: { forceReplace?: boolean } = {},
): SeriesSyncResult {
  const next = normalizeCandles(rawNext);

  if (options.forceReplace || prev.length === 0 || next.length === 0) {
    if (!options.forceReplace && prev.length === 0 && next.length === 0) {
      return { mode: 'noop', writes: 0, candles: next };
    }
    series.setData(next.map(toOhlc));
    return { mode: 'setData', writes: 1, candles: next };
  }

  if (prev === next) {
    return { mode: 'noop', writes: 0, candles: next };
  }

  let prefix = 0;
  const minLen = Math.min(prev.length, next.length);
  while (prefix < minLen && barsEqual(prev[prefix], next[prefix])) {
    prefix++;
  }

  if (prefix === prev.length && prefix === next.length) {
    return { mode: 'noop', writes: 0, candles: next };
  }

  // Pure tail append (or last bar modified + optional append)
  const removedTail = prev.length - prefix;
  const addedTail = next.length - prefix;

  if (removedTail === 0 && addedTail <= MAX_INCREMENTAL_DELTA) {
    for (let i = prefix; i < next.length; i++) {
      series.update(toOhlc(next[i]));
    }
    return { mode: 'update', writes: addedTail, candles: next };
  }

  if (removedTail === 1 && addedTail >= 1 && addedTail <= MAX_INCREMENTAL_DELTA && next[prefix].time === prev[prefix].time) {
    for (let i = prefix; i < next.length; i++) {
      series.update(toOhlc(next[i]));
    }
    return { mode: 'update', writes: addedTail, candles: next };
  }

  // Pure suffix removal (rewind within threshold)
  if (addedTail === 0 && removedTail > 0 && removedTail <= MAX_INCREMENTAL_DELTA && next.length > 0) {
    series.pop(removedTail);
    return { mode: 'pop', writes: 1, candles: next };
  }

  // Small suffix rewrite (e.g., late bar or modified recent bars near tail): pop divergent suffix then replay updates
  if (removedTail <= MAX_SUFFIX_REWRITE && addedTail <= MAX_INCREMENTAL_DELTA && prefix > 0) {
    series.pop(removedTail);
    let writes = 1;
    for (let i = prefix; i < next.length; i++) {
      series.update(toOhlc(next[i]));
      writes++;
    }
    return { mode: 'pop', writes, candles: next };
  }

  series.setData(next.map(toOhlc));
  return { mode: 'setData', writes: 1, candles: next };
}

/** Update a lookup map in-place based on the sync result to avoid O(N) Map rebuilds on small steps. */
export function syncCandleMap(
  map: Map<number, Candle>,
  prev: readonly Candle[],
  next: readonly Candle[],
  mode: SyncMode,
): void {
  if (mode === 'noop') return;
  if (mode === 'setData') {
    map.clear();
    for (const bar of next) map.set(bar.time, bar);
    return;
  }
  let prefix = 0;
  const minLen = Math.min(prev.length, next.length);
  while (prefix < minLen && barsEqual(prev[prefix], next[prefix])) {
    prefix++;
  }
  for (let i = prefix; i < prev.length; i++) {
    map.delete(prev[i].time);
  }
  for (let i = prefix; i < next.length; i++) {
    map.set(next[i].time, next[i]);
  }
}
