import {readFileSync, writeFileSync} from 'node:fs';
import {join, resolve} from 'node:path';
import {expect, test, type Locator, type Page} from '@playwright/test';
import type {ReplaySession} from '../src/types';

type Symbol = 'EURUSD' | 'GBPUSD';
type Prices = {open: number; high: number; low: number; close: number};
type Pin = {id: string; sha256: string};
type Hour = {
  start: number; end: number; available_at: number; ohlc: Prices;
  children: {open_time: number; available_at: number; partition: Pin; ohlc: Prices}[];
};
type Oracle = {date_utc: string; symbols: Record<Symbol, {
  market: {partitions: Pin[]; feed_id: string; price_basis: string};
  provider_csv_sha256: string; hours: Hour[];
}>};
const reference: Oracle = JSON.parse(readFileSync(resolve('../docs/implementation/evidence/T10-H1/provider-reference.json'), 'utf8'));
const storage = process.env.TRADEFORGE_PAIR_STORAGE!;
const output = process.env.TRADEFORGE_T10_CHART_OUTPUT!;
const reopen = process.env.TRADEFORGE_PAIR_REOPEN === '1';
const checkpointPath = join(storage, 't10-h1-checkpoint.json');
const statePath = join(storage, 't10-h1-browser-state.json');
const prices = ['open', 'high', 'low', 'close'] as const;
const instant = (time: string) => Date.parse(reference.date_utc + 'T' + time + ':00Z') / 1000;
const utc = (epoch: number) => new Date(epoch * 1000).toISOString().replace('T', ' ').replace('.000Z', ' UTC');
const select = (scope: Page | Locator, name: string) => scope.getByRole('combobox', {name, exact: true});
const button = (scope: Page | Locator, name: string) => scope.getByRole('button', {name, exact: true});
const replay = (page: Page) => page.getByLabel('Historical replay', {exact: true});
const evidence = (page: Page) => page.getByRole('region', {name: 'H1 replay evidence'});
const interval = (page: Page) => evidence(page).getByRole('article', {name: 'Selected H1 interval'});

if (reopen) test.use({storageState: statePath});

async function verifyCutoff(page: Page, time: string) {
  const expected = new Date(instant(time) * 1000).toISOString();
  await expect(replay(page).locator('output')).toContainText(expected);
  await expect(evidence(page)).toContainText('M5 replay cutoff ' + utc(instant(time)));
}

async function seek(page: Page, time: string) {
  await replay(page).getByLabel('Jump to UTC', {exact: true}).fill(reference.date_utc + 'T' + time);
  await button(replay(page), 'Seek').click();
  await verifyCutoff(page, time);
}

async function verifyHour(page: Page, symbol: Symbol, index: number, status: 'COMPLETE' | 'FORMING') {
  const {hours, market} = reference.symbols[symbol];
  const expected = hours[index];
  const candle = interval(page);
  await expect(candle).toHaveAttribute('data-start', String(expected.start));
  await expect(candle).toHaveAttribute('data-end', String(expected.end));
  await expect(candle).toHaveAttribute('data-status', status);
  await expect(candle).toContainText(symbol + ' H1 · ' + market.price_basis + ' · ' + market.feed_id);
  if (status === 'FORMING') {
    await expect(candle).toContainText('Still forming');
    await expect(candle.getByLabel('H1 OHLC')).toHaveCount(0);
    await expect(candle.locator('details')).toHaveCount(0);
    return null;
  }
  await expect(candle).toContainText('Modeled availability ' + utc(expected.available_at));
  const figures = candle.getByLabel('H1 OHLC').locator('dd');
  await expect(figures).toHaveText(prices.map(key => String(expected.ohlc[key])));
  const disclosure = candle.locator('details');
  await expect(disclosure.locator('summary')).toHaveText('12 source candles');
  if (await disclosure.getAttribute('open') === null) await disclosure.locator('summary').click();
  const pins = [...new Map(expected.children.map(({partition}) => [partition.id, partition])).values()];
  await expect(disclosure.locator('li')).toHaveText(pins.map(({id, sha256}) => 'Dataset ' + id + ' · SHA-256 ' + sha256));
  const rows = disclosure.getByRole('table', {name: 'M5 source candles'}).locator('tbody tr');
  const sources = expected.children.map(child => child.partition.id + ':' + child.open_time);
  await expect(rows).toHaveCount(12);
  for (const [i, child] of expected.children.entries()) {
    const row = rows.nth(i);
    await expect(row).toHaveAttribute('data-source-id', sources[i]);
    await expect(row.getByRole('cell')).toHaveText([
      utc(child.open_time), utc(child.available_at),
      ...prices.map(key => String(child.ohlc[key])), sources[i], child.partition.id.slice(0, 12),
    ]);
  }
  return {
    symbol, start: expected.start, end: expected.end, available_at: expected.available_at,
    ohlc: Object.fromEntries((await figures.allTextContents()).map((value, i) => [prices[i], Number(value)])),
    sources: await rows.evaluateAll(nodes => nodes.map(node => node.getAttribute('data-source-id'))),
    partitions: pins, provider_csv_sha256: reference.symbols[symbol].provider_csv_sha256,
  };
}

test('inspects H1 values, child pins and stale responses from the ordinary M5 replay', async ({page, context}) => {
  test.skip(reopen);
  test.setTimeout(90000);
  await page.setViewportSize({width: 1536, height: 960});
  await page.goto('/?timezone=UTC');
  await button(page, 'Market Terminal').click();
  await select(page, 'Market data source').selectOption('DUKASCOPY_SAVED');
  await page.getByLabel('From UTC', {exact: true}).fill(reference.date_utc);
  await page.getByLabel('From time (UTC)', {exact: true}).fill('13:00');
  await page.getByLabel('Until UTC (exclusive)', {exact: true}).fill(reference.date_utc);
  await page.getByLabel('Until time (UTC)', {exact: true}).fill('15:00');
  await button(page, 'Load saved range').click();
  await select(page, 'Chart layout').selectOption('2');
  const peer = page.getByLabel('Chart view 2', {exact: true});
  await select(peer, 'View 2 instrument').selectOption('GBPUSD');
  await select(peer, 'View 2 timeframe').selectOption('M5');
  const created = page.waitForResponse(response => response.url().endsWith('/replay/sessions') &&
    response.request().method() === 'POST');
  await button(page, 'Bar replay').click();
  const sessionResponse = await created;
  expect(sessionResponse.status(), await sessionResponse.text()).toBe(201);
  const session: ReplaySession = await sessionResponse.json();
  expect(session.views['2'].symbol).toBe('GBPUSD');

  await seek(page, '13:55');
  await verifyHour(page, 'EURUSD', 0, 'FORMING');
  await button(replay(page), 'Next candle').click();
  await verifyCutoff(page, '14:00');
  const first = await verifyHour(page, 'EURUSD', 0, 'COMPLETE');
  await select(evidence(page), 'H1 chart pane').selectOption('2');
  const peerFirst = await verifyHour(page, 'GBPUSD', 0, 'COMPLETE');
  await select(evidence(page), 'H1 chart pane').selectOption('primary');
  await seek(page, '14:50');
  expect(await verifyHour(page, 'EURUSD', 0, 'COMPLETE')).toEqual(first);
  await select(evidence(page), 'H1 interval').selectOption(String(instant('14:00')));
  await verifyHour(page, 'EURUSD', 1, 'FORMING');

  const held = Promise.withResolvers<{status: string; hour: number}>();
  const release = Promise.withResolvers<void>();
  const delivered = Promise.withResolvers<void>();
  const pattern = '**/higher-timeframe?*';
  await page.route(pattern, async route => {
    const params = new URL(route.request().url()).searchParams;
    if (params.get('cutoff') !== String(instant('14:55')) || params.get('view') !== 'primary') {
      return route.continue();
    }
    const response = await route.fetch();
    const body = await response.json();
    held.resolve({status: body.status, hour: body.start});
    await release.promise;
    try {
      await route.fulfill({response});
    } finally {
      delivered.resolve();
    }
  });
  try {
    await button(replay(page), 'Next candle').click();
    await verifyCutoff(page, '14:55');
    const priorResponse = await held.promise;
    expect(priorResponse).toEqual({status: 'FORMING', hour: instant('14:00')});
    await button(replay(page), 'Next candle').click();
    await verifyCutoff(page, '15:00');
    const second = await verifyHour(page, 'EURUSD', 1, 'COMPLETE');
    release.resolve();
    await delivered.promise;
    expect(await verifyHour(page, 'EURUSD', 1, 'COMPLETE')).toEqual(second);
    await page.unroute(pattern);

    await select(evidence(page), 'H1 chart pane').selectOption('2');
    const peerSecond = await verifyHour(page, 'GBPUSD', 1, 'COMPLETE');
    await seek(page, '14:00');
    expect(await verifyHour(page, 'GBPUSD', 0, 'COMPLETE')).toEqual(peerFirst);
    await seek(page, '15:00');
    expect(await verifyHour(page, 'GBPUSD', 1, 'COMPLETE')).toEqual(peerSecond);
    await button(replay(page), 'Save replay position').click();
    await expect(replay(page).getByText('Replay position saved', {exact: true})).toBeVisible();
    writeFileSync(checkpointPath, JSON.stringify({id: session.id, first, peerFirst, second, peerSecond, priorResponse}));
    await context.storageState({path: statePath});
  } finally {
    release.resolve();
  }
});

test('reopens the H1 hour and the same twelve children after restarting', async ({page, browser}) => {
  test.skip(!reopen);
  const receipt = JSON.parse(readFileSync(checkpointPath, 'utf8'));
  await page.setViewportSize({width: 1536, height: 960});
  await page.goto('/?timezone=UTC');
  await button(page, 'Market Terminal').click();
  await select(page, 'Resume saved replay').selectOption(receipt.id);
  await verifyCutoff(page, '15:00');
  await expect(select(evidence(page), 'H1 chart pane')).toHaveValue('2');
  expect(await verifyHour(page, 'GBPUSD', 1, 'COMPLETE')).toEqual(receipt.peerSecond);
  await seek(page, '14:00');
  expect(await verifyHour(page, 'GBPUSD', 0, 'COMPLETE')).toEqual(receipt.peerFirst);
  await seek(page, '15:00');
  const reopened = await verifyHour(page, 'GBPUSD', 1, 'COMPLETE');
  expect(reopened).toEqual(receipt.peerSecond);
  await evidence(page).screenshot({path: join(output, 'reopened-h1-evidence.png')});
  writeFileSync(join(output, 'h1-chart-journey.json'), JSON.stringify({
    status: 'PASS', chromium: browser.version(), day: reference.date_utc,
    replay: receipt.id, observed: [receipt.first, receipt.second, receipt.peerFirst, receipt.peerSecond],
    held_response: receipt.priorResponse, held_response_replaced_current_hour: false, reopened,
  }, null, 2) + '\n');
});
