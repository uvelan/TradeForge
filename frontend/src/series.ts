import type { UTCTimestamp } from "lightweight-charts";

export const timeframes = ["M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1"] as const;
export type Timeframe = typeof timeframes[number];
export type Candle = { time: number; available_at: number; open: number; high: number; low: number; close: number };
export type Dataset = {
  id: string; provider: string; feed: string; timeframe: Timeframe; price_scale: number;
  start_utc: number; end_utc: number; row_count: number; symbol: string; price_basis: string;
  validation_status: string;
};
export type Series = { dataset: Dataset; candles: Candle[] };

export function chartPoints(series: Series) {
  const scale = series.dataset.price_scale;
  return series.candles.map(({ time, open, high, low, close }) => ({
    time: time as UTCTimestamp, open: open / scale, high: high / scale, low: low / scale, close: close / scale,
  }));
}
