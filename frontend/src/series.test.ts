import { expect, test } from "vitest";
import { chartPoints } from "./series";

test("converts integer prices without changing UTC candle identity", () => {
  const points = chartPoints({
    dataset: {
      id: "sample", provider: "simulator", feed: "v1", timeframe: "M15", symbol: "EURUSD",
      price_basis: "synthetic_bid", price_scale: 100000, start_utc: 900, end_utc: 1800, row_count: 1,
      validation_status: "VERIFIED",
    },
    candles: [{ time: 900, available_at: 1800, open: 108000, high: 109000, low: 107000, close: 108500 }],
  });
  expect(points).toEqual([{ time: 900, open: 1.08, high: 1.09, low: 1.07, close: 1.085 }]);
});
