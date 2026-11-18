import {expect, test} from '@playwright/test';

test('strict SMT ignores stale pair responses and rewinds a frozen pair', async ({page}, info) => {
  const start = Date.parse('2026-10-26T00:00:00Z') / 1000;
  const candles = Array.from({length: 12}, (_, index) => ({
    time: start + index * 300, open: 10, close: 10, low: 9, high: 11,
  }));
  const coverage = {start, end: start + 3600, gaps: [], truncated: false};
  const market = (symbol: string) => ({
    symbol, source: 'DUKASCOPY_SAVED', timeframe: 'M5', synthetic: false,
    price_basis: 'BID', feed_id: 'test-' + symbol,
    partitions: [{id: 'partition-' + symbol, sha256: 'a'.repeat(64)}], candles, coverage,
  });
  await page.route('**/research/api/market/candles?*', route => {
    const symbol = new URL(route.request().url()).searchParams.get('symbol') ?? 'EURUSD';
    return route.fulfill({json: market(symbol)});
  });
  await page.route('**/research/api/indicators/smt', route => route.fulfill({json: {events: [], alerts: []}}));
  await page.route('**/research/api/replay/sessions', route => route.fulfill({status: 201, json: {
    id: 'strict-smt-fixture', market: market('EURUSD'), checkpoint: {position: 8, bookmarks: []},
    views: {comparison: market('USDJPY')},
  }}));
  let release = () => {};
  const oldPair = new Promise<void>(resolve => {release = resolve;});
  await page.route('**/research/api/indicators/smt/strict', async route => {
    const {primary, comparison, cutoff} = route.request().postDataJSON();
    if (comparison.symbol === 'GBPUSD') await oldPair;
    const confirmed = cutoff >= start + 3000;
    const uncertain = cutoff >= start + 3300;
    await route.fulfill({json: {
      status: comparison.symbol === 'GBPUSD' ? 'STALE_PAIR_RESPONSE' : 'EVALUATED',
      sources: {primary: {...primary, authority: 'CHECKSUM_VERIFIED_ARCHIVE'},
                comparison: {...comparison, authority: 'CHECKSUM_VERIFIED_ARCHIVE'}},
      current: [{id: 'stable-event', direction: 'bearish',
                 state: uncertain ? 'UNCERTAIN' : confirmed ? 'SMT_CONFIRMED' : 'SMT_CANDIDATE',
                 current_eligible: confirmed && !uncertain,
                 reason: uncertain ? 'MISSING_LAGGARD_OBSERVATION' : null}], events: [],
    }}).catch(() => {});
  });

  await page.setViewportSize({width: 1536, height: 900});
  await page.goto('/');
  await page.getByRole('button', {name: 'Market Terminal', exact: true}).click();
  await page.getByRole('combobox', {name: 'Market data source'}).selectOption('DUKASCOPY_SAVED');
  await page.getByRole('button', {name:'M5', exact:true}).click();
  const compare = page.getByRole('combobox', {name: 'SMT comparison'});
  await compare.selectOption('GBPUSD');
  await page.getByText('SMT monitor', {exact: true}).click();
  const mode = page.getByLabel('SMT evidence mode');
  await expect(mode).toHaveValue('legacy');
  await mode.selectOption('strict');
  const monitor = page.getByRole('region', {name: 'Strict causal SMT'});
  const first = page.waitForRequest(req => req.url().endsWith('/smt/strict'));
  await monitor.getByLabel('Shared SMT market hours').selectOption('continuous');
  expect((await first).postDataJSON().comparison.symbol).toBe('GBPUSD');
  await compare.selectOption('USDJPY');
  await expect(monitor).toContainText('comparison · USDJPY');
  release();
  await expect(monitor).not.toContainText('STALE_PAIR_RESPONSE');
  await page.getByRole('button', {name: 'Bar replay', exact: true}).click();
  const replay = page.getByLabel('Historical replay');
  await expect(monitor).toContainText('SMT_CANDIDATE');
  const next = page.waitForRequest(req => req.url().endsWith('/smt/strict')
    && req.postDataJSON().cutoff === start + 3000);
  await replay.getByRole('button', {name: 'Next candle'}).click();
  expect((await next).postDataJSON().comparison.partitions[0].id).toBe('partition-USDJPY');
  await expect(monitor).toContainText('SMT_CONFIRMED');
  await page.screenshot({path: info.outputPath('strict-smt-confirmed-replay.png')});
  await replay.getByRole('button', {name: 'Next candle'}).click();
  await expect(monitor).toContainText('UNCERTAIN · MISSING_LAGGARD_OBSERVATION');
  await page.screenshot({path: info.outputPath('strict-smt-uncertain-replay.png')});
  await replay.getByRole('button', {name: 'Previous candle'}).click();
  await expect(monitor).toContainText('SMT_CONFIRMED');
  await replay.getByRole('button', {name: 'Previous candle'}).click();
  await expect(monitor).toContainText('SMT_CANDIDATE');
  await expect(monitor).not.toContainText('SMT_CONFIRMED');

  await page.getByText('Indicator library', {exact: true}).click();
  for (const [query, id] of [
    ['fair value gap', 'fair_value_gap'], ['order block', 'order_blocks'], ['candle range', 'candle_range_theory'],
  ]) {
    const evaluated = page.waitForResponse(response => response.ok() && response.url().endsWith('/indicators/evaluate')
      && response.request().postDataJSON().indicators.some((item: {id: string}) => item.id === id));
    await page.getByLabel('Search indicators').fill(query);
    await page.getByLabel('Add indicator').selectOption(id);
    await page.getByRole('button', {name: 'Enable indicator', exact: true}).click();
    await evaluated;
    await expect(monitor).toContainText('SMT_CANDIDATE');
  }
});
