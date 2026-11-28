import { describe, expect, it } from 'vitest';
import {
  normalizeCandles,
  syncCandleMap,
  syncCandlestickSeries,
  type CandlestickSeriesTarget,
} from './candleSeriesSync';
import { visibleAt } from './ReplayControls';
import type { Candle } from './types';

function makeBar(time: number, closeOffset = 0): Candle {
  return {
    time,
    open: 1.08 + closeOffset,
    high: 1.081 + closeOffset,
    low: 1.079 + closeOffset,
    close: 1.0805 + closeOffset,
  };
}

class MemorySeries implements CandlestickSeriesTarget {
  bars: Array<{ time: number; open: number; high: number; low: number; close: number }> = [];
  calls: { setData: number; update: number; pop: number } = { setData: 0, update: 0, pop: 0 };

  setData(data: Array<{ time: unknown; open: number; high: number; low: number; close: number }>) {
    this.calls.setData++;
    this.bars = data.map(item => ({
      time: Number(item.time),
      open: item.open,
      high: item.high,
      low: item.low,
      close: item.close,
    }));
  }

  update(bar: { time: unknown; open: number; high: number; low: number; close: number }) {
    this.calls.update++;
    const next = {
      time: Number(bar.time),
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    };
    const last = this.bars.at(-1);
    if (last && last.time === next.time) {
      this.bars[this.bars.length - 1] = next;
    } else if (!last || next.time > last.time) {
      this.bars.push(next);
    } else {
      throw new Error(`Out-of-order incremental update at ${next.time} after ${last.time}`);
    }
  }

  pop(count: number) {
    this.calls.pop++;
    return this.bars.splice(Math.max(0, this.bars.length - count), count) as unknown as ReturnType<CandlestickSeriesTarget['pop']>;
  }
}

describe('candleSeriesSync', () => {
  it('syncs initial load, noop, append update, last-bar revision, rewind pop, and large seek identically to setData', () => {
    const series = new MemorySeries();
    const map = new Map<number, Candle>();
    const fullArchive = Array.from({ length: 200 }, (_, i) => makeBar(1700000000 + i * 300, i * 0.00001));

    // Initial load uses setData
    let prev: Candle[] = [];
    let next = fullArchive.slice(0, 100);
    let res = syncCandlestickSeries(series, prev, next);
    syncCandleMap(map, prev, res.candles, res.mode);
    expect(res.mode).toBe('setData');
    expect(series.calls).toEqual({ setData: 1, update: 0, pop: 0 });
    expect(series.bars).toEqual(next);

    // Unchanged slice (gap or H1 unchanged) skips writes
    prev = res.candles;
    next = fullArchive.slice(0, 100);
    res = syncCandlestickSeries(series, prev, next);
    syncCandleMap(map, prev, res.candles, res.mode);
    expect(res.mode).toBe('noop');
    expect(res.writes).toBe(0);
    expect(series.calls).toEqual({ setData: 1, update: 0, pop: 0 });

    // Single forward step uses update
    prev = res.candles;
    next = fullArchive.slice(0, 101);
    res = syncCandlestickSeries(series, prev, next);
    syncCandleMap(map, prev, res.candles, res.mode);
    expect(res.mode).toBe('update');
    expect(res.writes).toBe(1);
    expect(series.calls).toEqual({ setData: 1, update: 1, pop: 0 });
    expect(series.bars).toEqual(next);
    expect(map.size).toBe(101);

    // Same-bar revision on last candle uses update
    prev = res.candles;
    const revisedLast = { ...fullArchive[100], close: 1.09999 };
    next = [...fullArchive.slice(0, 100), revisedLast];
    res = syncCandlestickSeries(series, prev, next);
    syncCandleMap(map, prev, res.candles, res.mode);
    expect(res.mode).toBe('update');
    expect(series.bars.at(-1)?.close).toBe(1.09999);
    expect(map.get(revisedLast.time)?.close).toBe(1.09999);

    // Rewind 1 bar uses pop
    prev = res.candles;
    next = fullArchive.slice(0, 100);
    res = syncCandlestickSeries(series, prev, next);
    syncCandleMap(map, prev, res.candles, res.mode);
    expect(res.mode).toBe('pop');
    expect(series.calls.pop).toBe(1);
    expect(series.bars).toEqual(next);
    expect(map.size).toBe(100);

    // Large seek forward (>64 bars) falls back safely to setData
    prev = res.candles;
    next = fullArchive.slice(0, 195);
    res = syncCandlestickSeries(series, prev, next);
    syncCandleMap(map, prev, res.candles, res.mode);
    expect(res.mode).toBe('setData');
    expect(series.bars).toEqual(next);
    expect(map.size).toBe(195);

    // Rewind large (>64 bars) falls back safely to setData
    prev = res.candles;
    next = fullArchive.slice(0, 50);
    res = syncCandlestickSeries(series, prev, next);
    syncCandleMap(map, prev, res.candles, res.mode);
    expect(res.mode).toBe('setData');
    expect(series.bars).toEqual(next);
    expect(map.size).toBe(50);
  });

  it('caches immutable archive normalization without re-sorting on every call', () => {
    const raw = [makeBar(1700000600), makeBar(1700000300), makeBar(1700000300), makeBar(1700000900)];
    const first = normalizeCandles(raw);
    const second = normalizeCandles(raw);
    expect(first).toBe(second);
    expect(first.map(b => b.time)).toEqual([1700000300, 1700000600, 1700000900]);
  });

  it('handles monotonic binary-search and non-monotonic late-arrival candles in visibleAt', () => {
    const step = 300;
    const monotonic = [
      { ...makeBar(1000), available_at: 1300 },
      { ...makeBar(1300), available_at: 1600 },
      { ...makeBar(1600), available_at: 1900 },
    ];
    expect(visibleAt(monotonic, 1600, step).map(b => b.time)).toEqual([1000, 1300]);

    const lateArrival = [
      { ...makeBar(1000), available_at: 1300 },
      { ...makeBar(1300), available_at: 2500 },
      { ...makeBar(1600), available_at: 1900 },
    ];
    expect(visibleAt(lateArrival, 2000, step).map(b => b.time)).toEqual([1000, 1600]);
    expect(visibleAt(lateArrival, 2600, step).map(b => b.time)).toEqual([1000, 1300, 1600]);
  });
});
