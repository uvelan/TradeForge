import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  isValidIndicatorPeriod,
  loadIndicatorCatalog,
  periodBounds,
  resetIndicatorCatalogCache,
} from './indicatorCatalog';

describe('indicatorCatalog', () => {
  afterEach(() => {
    resetIndicatorCatalogCache();
  });

  it('derives period validation bounds from backend registry metadata', () => {
    const custom = { parameters: { period: { min: 5, max: 50 } } };
    expect(periodBounds(custom)).toEqual({ min: 5, max: 50 });
    expect(isValidIndicatorPeriod(4, custom)).toBe(false);
    expect(isValidIndicatorPeriod(5, custom)).toBe(true);
    expect(isValidIndicatorPeriod(50, custom)).toBe(true);
    expect(isValidIndicatorPeriod(51, custom)).toBe(false);
    expect(isValidIndicatorPeriod(null, custom)).toBe(true);
    expect(periodBounds(undefined)).toEqual({ min: 2, max: 200 });
  });

  it('shares a single cached backend catalog request across panes and resets on failure', async () => {
    const failing = vi.fn().mockRejectedValueOnce(new Error('offline'));
    await expect(loadIndicatorCatalog(failing)).rejects.toThrow('offline');

    const loader = vi.fn().mockResolvedValue([{ id: 'sma', label: 'Simple moving average' }]);
    const [first, second] = await Promise.all([
      loadIndicatorCatalog(loader),
      loadIndicatorCatalog(loader),
    ]);
    const third = await loadIndicatorCatalog(loader);
    expect(loader).toHaveBeenCalledTimes(1);
    expect(first).toEqual(second);
    expect(third).toEqual(first);
  });
});
