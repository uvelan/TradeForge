import {expect, test} from '@playwright/test';
import type {CandleResponse, ReplaySession} from '../src/types';

const at = (utc: string) => Date.parse(utc + ':00Z') / 1000;
const hour = 3600;
const begin = at('2026-01-05T10:00');
const finalHour = at('2026-02-10T12:00');
const peerHour = finalHour - hour;
const closedHour = at('2026-01-10T14:00');
const missingHour = at('2026-01-12T14:00');
const prices = {open: 1.12, high: 1.13, low: 1.11, close: 1.125};

function archived(start: number, end: number, times: number[], truncated = false): CandleResponse {
  return {
    source: 'DUKASCOPY_SAVED', feed_id: 'fixture', price_basis: 'BID', synthetic: false,
    coverage: {start, end, gaps: [], duplicate_bars: 0, truncated},
    candles: times.map(time => ({time, ...prices})),
  };
}

test('navigates sparse H1 capture dates and clears out-of-range pane choices on rewind', async ({page}) => {
  const primaryTimes = [begin, finalHour].flatMap(start => Array.from({length: 12}, (_, i) => start + i * 300));
  const primary = archived(begin, finalHour + hour, primaryTimes);
  const peer = archived(begin, finalHour, [peerHour + 2100, peerHour + 2400], true);
  const session: ReplaySession = {
    id: 'sparse-h1', market: primary, views: {'2': {...peer, symbol: 'GBPUSD', timeframe: 'M5'}},
    checkpoint: {position: primary.candles.length - 1, bookmarks: []},
  };
  await page.route('**/research/api/market/candles?*', route => route.fulfill({
    json: new URL(route.request().url()).searchParams.get('symbol') === 'GBPUSD' ? peer : primary,
  }));
  await page.route('**/research/api/replay/sessions', route => route.fulfill({status: 201, json: session}));
  const pendingPeer = Promise.withResolvers<void>();
  await page.route('**/higher-timeframe?*', async route => {
    const query = new URL(route.request().url()).searchParams;
    const start = Number(query.get('hour_start'));
    const cutoff = Number(query.get('cutoff'));
    const isPeer = query.get('view') === '2';
    if (isPeer) await pendingPeer.promise;
    const end = start + hour;
    const complete = !isPeer && start === finalHour && cutoff >= end;
    const status = start === closedHour ? 'CLOSED' : complete ? 'COMPLETE' : cutoff < end ? 'FORMING' : 'UNRESOLVED';
    await route.fulfill({json: {
      symbol: isPeer ? 'GBPUSD' : 'EURUSD', feed_id: 'fixture', price_basis: 'BID', start, end, cutoff,
      available_at: complete ? end : null, status, reason: status === 'UNRESOLVED' ? 'MISSING_CANDLES' : null,
      ohlc: complete ? prices : null, children: [], missing_slots: [],
    }});
  });

  await page.goto('/?timezone=UTC');
  await page.getByRole('button', {name: 'Market Terminal', exact: true}).click();
  await page.getByRole('combobox', {name: 'Market data source', exact: true}).selectOption('DUKASCOPY_SAVED');
  await page.getByRole('combobox', {name: 'Chart layout', exact: true}).selectOption('2');
  await page.getByRole('combobox', {name: 'View 2 instrument', exact: true}).selectOption('GBPUSD');
  await page.getByRole('combobox', {name: 'View 2 timeframe', exact: true}).selectOption('M5');
  await page.getByRole('button', {name: 'Bar replay', exact: true}).click();

  const evidence = page.getByRole('region', {name: 'H1 replay evidence'});
  const pane = evidence.getByRole('combobox', {name: 'H1 chart pane'});
  const date = evidence.getByLabel('H1 UTC date', {exact: true});
  const choice = evidence.getByRole('combobox', {name: 'H1 interval'});
  const receipt = evidence.getByRole('article', {name: 'Selected H1 interval'});
  const ohlc = receipt.getByLabel('H1 OHLC');
  await expect(receipt).toHaveAttribute('data-status', 'COMPLETE');
  await expect(ohlc).toBeVisible();
  await expect(date).toHaveValue('2026-02-10');
  await expect(date).toHaveAttribute('min', '2026-01-05');
  await expect(date).toHaveAttribute('max', '2026-02-10');
  await expect(choice.getByRole('option')).toHaveCount(14);

  await date.fill('2026-01-10');
  await expect(choice.getByRole('option')).toHaveCount(25);
  await choice.selectOption(String(closedHour));
  await expect(receipt).toHaveAttribute('data-status', 'CLOSED');
  await expect(receipt).toContainText('Market closed');
  await expect(ohlc).toHaveCount(0);
  await date.fill('2026-01-12');
  await choice.selectOption(String(missingHour));
  await expect(receipt).toHaveAttribute('data-start', String(missingHour));
  await expect(receipt).toHaveAttribute('data-status', 'UNRESOLVED');
  await expect(ohlc).toHaveCount(0);

  await choice.selectOption('');
  await expect(ohlc).toBeVisible();
  try {
    await pane.selectOption('2');
    await expect(evidence.getByRole('status')).toHaveText('Loading H1 evidence…');
    await expect(ohlc).toHaveCount(0);
    await expect(date).toHaveCount(0);
    await expect(choice.getByRole('option')).toHaveCount(2);
    await expect(choice.getByRole('option').last()).toHaveAttribute('value', String(peerHour));
  } finally {
    pendingPeer.resolve();
  }
  await expect(receipt).toContainText('GBPUSD H1');
  await expect(receipt).toHaveAttribute('data-status', 'UNRESOLVED');
  await pane.selectOption('primary');
  await choice.selectOption(String(finalHour));
  await expect(ohlc).toBeVisible();

  const replay = page.getByLabel('Historical replay', {exact: true});
  await replay.getByRole('button', {name: 'Restart', exact: true}).click();
  await expect(choice).toHaveValue('');
  await expect(choice.getByRole('option')).toHaveCount(2);
  await expect(choice.getByRole('option').last()).toHaveAttribute('value', String(begin));
  await expect(receipt).toHaveAttribute('data-status', 'FORMING');
  await expect(ohlc).toHaveCount(0);

  await replay.getByRole('button', {name: 'Last candle', exact: true}).click();
  await expect(choice).toHaveValue('');
  await expect(receipt).toHaveAttribute('data-start', String(finalHour));
  await expect(ohlc).toBeVisible();

  await replay.getByRole('button', {name: 'Restart', exact: true}).click();
  await pane.selectOption('2');
  await expect(evidence).toContainText('No captured H1 interval at this cutoff for GBPUSD.');
  await expect(choice).toHaveCount(0);
});
