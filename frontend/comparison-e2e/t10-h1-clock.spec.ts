import {readFileSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {expect, test, type Browser, type Locator, type Page} from '@playwright/test';

type Prices = {open: number; high: number; low: number; close: number};
type Pin = {id: string; sha256: string};
type Child = Prices & {time: number; available_at: number; source_id: string; partitions: Pin[]};
type Hour = {
  status: 'COMPLETE' | 'UNRESOLVED'; reason: string | null; ohlc: Prices | null;
  available_at: number | null; missing_slots: number[]; children: Child[];
};
type Symbol = 'EURUSD' | 'GBPUSD';
type Scenario = 'delayed' | 'missing';
type Pane = 'primary' | '2';

const storage = process.env.TRADEFORGE_H1_CLOCK_STORAGE!;
const output = process.env.TRADEFORGE_H1_CLOCK_OUTPUT!;
const reference: {
  day: string; feed_id: string; start: number; short_end: number; long_end: number; delay: number;
  symbols: Record<Symbol, {pin: Pin; provider_csv_sha256: string; hours: Record<string, Hour>}>;
} = JSON.parse(readFileSync(join(storage, 'reference.json'), 'utf8'));
const fields = ['open', 'high', 'low', 'close'] as const;
const utc = (time: number) => new Date(time * 1000).toISOString().replace('T', ' ').replace('.000Z', ' UTC');
const replay = (page: Page) => page.getByLabel('Historical replay', {exact: true});
const evidence = (page: Page) => page.getByRole('region', {name: 'H1 replay evidence'});
const interval = (page: Page) => evidence(page).getByRole('article', {name: 'Selected H1 interval'});
const select = (scope: Locator | Page, name: string) => scope.getByRole('combobox', {name, exact: true});
const button = (scope: Locator | Page, name: string) => scope.getByRole('button', {name, exact: true});
const state = (scenario: Scenario) => join(storage, scenario + '-state.json');
const checkpoint = (scenario: Scenario) => join(storage, scenario + '-checkpoint.json');

function saveReceipt(name: string, receipt: unknown) {
  writeFileSync(join(output, name + '.json'), JSON.stringify(receipt, null, 2) + '\n');
}

async function verifyCutoff(page: Page, cutoff: number) {
  await expect(replay(page).locator('output')).toContainText(new Date(cutoff * 1000).toISOString());
  await expect(evidence(page)).toContainText('M5 replay cutoff ' + utc(cutoff));
}

async function openTerminal(page: Page) {
  await page.setViewportSize({width: 1536, height: 960});
  await page.goto('/?timezone=UTC');
  await button(page, 'Market Terminal').click();
}

async function createReplay(page: Page, end: string) {
  await openTerminal(page);
  await select(page, 'Market data source').selectOption('DUKASCOPY_SAVED');
  await page.getByLabel('From UTC', {exact: true}).fill(reference.day);
  await page.getByLabel('From time (UTC)', {exact: true}).fill('13:00');
  await page.getByLabel('Until UTC (exclusive)', {exact: true}).fill(reference.day);
  await page.getByLabel('Until time (UTC)', {exact: true}).fill(end);
  await button(page, 'Load saved range').click();
  await select(page, 'Chart layout').selectOption('2');
  const peer = page.getByLabel('Chart view 2', {exact: true});
  await select(peer, 'View 2 instrument').selectOption('GBPUSD');
  await select(peer, 'View 2 timeframe').selectOption('M5');
  const pending = page.waitForResponse(response => response.url().endsWith('/replay/sessions') &&
    response.request().method() === 'POST');
  await button(page, 'Bar replay').click();
  const response = await pending;
  expect(response.status(), await response.text()).toBe(201);
  const session = await response.json();
  expect(session.views['2'].symbol).toBe('GBPUSD');
  await button(replay(page), 'Last candle').click();
  return session.id as string;
}

function waitForHour(page: Page, pane: Pane, start: number, cutoff: number) {
  return page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname.endsWith('/higher-timeframe') && url.searchParams.get('view') === pane &&
      url.searchParams.get('hour_start') === String(start) && url.searchParams.get('cutoff') === String(cutoff);
  });
}

async function verifyHour(page: Page, symbol: Symbol, start: number) {
  const oracle = reference.symbols[symbol];
  const expected = oracle.hours[start];
  const selected = interval(page);
  await expect(selected).toHaveAttribute('data-start', String(start));
  await expect(selected).toHaveAttribute('data-end', String(start + 3600));
  await expect(selected).toHaveAttribute('data-status', expected.status);
  await expect(selected).toContainText(symbol + ' H1 · BID · ' + reference.feed_id);
  const availability = expected.available_at == null ? null : 'Modeled availability ' + utc(expected.available_at);
  const missing = expected.missing_slots.map(utc);
  if (expected.status === 'UNRESOLVED') {
    const reason = 'Unresolved · ' + expected.reason!.toLowerCase().replaceAll('_', ' ');
    await expect(selected.getByRole('status')).toHaveText(reason);
    await expect(selected).toContainText('Missing M5 candles ' + missing.join(', '));
    await expect(selected.getByLabel('H1 OHLC')).toHaveCount(0);
    await expect(selected.getByText('Modeled availability', {exact: false})).toHaveCount(0);
    await expect(selected.locator('details')).toHaveCount(0);
    return {symbol, start, status: expected.status, reason: await selected.getByRole('status').innerText(),
      missing: await selected.getByText('Missing M5 candles').innerText(), availability, ohlc: null, children: []};
  }
  await expect(selected.getByRole('status')).toHaveText('Complete');
  const shownAvailability = selected.getByText(availability!, {exact: true});
  await expect(shownAvailability).toBeVisible();
  const values = selected.getByLabel('H1 OHLC').locator('dd');
  await expect(values).toHaveText(fields.map(field => String(expected.ohlc![field])));
  const disclosure = selected.locator('details');
  await expect(disclosure.locator('summary')).toHaveText('12 source candles');
  await disclosure.locator('summary').click();
  await expect(disclosure.locator('li')).toHaveText('Dataset ' + oracle.pin.id + ' · SHA-256 ' + oracle.pin.sha256);
  const rows = disclosure.getByRole('table', {name: 'M5 source candles'}).locator('tbody tr');
  await expect(rows).toHaveCount(12);
  for (const [index, child] of expected.children.entries()) {
    await expect(rows.nth(index)).toHaveAttribute('data-source-id', child.source_id);
    await expect(rows.nth(index).getByRole('cell')).toHaveText([
      utc(child.time), utc(child.available_at), ...fields.map(field => String(child[field])),
      child.source_id, oracle.pin.id.slice(0, 12),
    ]);
  }
  return {symbol, start, status: expected.status, availability: await shownAvailability.innerText(),
    ohlc: Object.fromEntries((await values.allTextContents()).map((value, i) => [fields[i], Number(value)])),
    dataset: await disclosure.locator('li').innerText(),
    children: await rows.evaluateAll(nodes => nodes.map(row => [...(row as HTMLTableRowElement).cells].map(cell => cell.textContent?.trim()))),
  };
}

async function savePosition(page: Page, scenario: Scenario, id: string) {
  const pending = page.waitForResponse(response => response.url().endsWith('/checkpoint') &&
    response.request().method() === 'POST');
  await button(replay(page), 'Save replay position').click();
  const response = await pending;
  expect(response.status(), await response.text()).toBe(200);
  await expect(replay(page).getByText('Replay position saved', {exact: true})).toBeVisible();
  await page.context().storageState({path: state(scenario)});
  const payload = await response.json();
  writeFileSync(checkpoint(scenario), JSON.stringify({id, payload}));
  return payload;
}

async function reopenReplay(browser: Browser, scenario: Scenario) {
  const context = await browser.newContext({storageState: state(scenario), serviceWorkers: 'block'});
  const page = await context.newPage();
  await openTerminal(page);
  const saved = JSON.parse(readFileSync(checkpoint(scenario), 'utf8'));
  return {context, page, saved};
}

test('initial: both panes retain their own availability at the same delayed cutoff', async ({page, browser}) => {
  const id = await createReplay(page, '14:00');
  const cutoff = reference.short_end + reference.delay;
  await verifyCutoff(page, cutoff);
  const primary = await verifyHour(page, 'EURUSD', reference.start);
  const pending = waitForHour(page, '2', reference.start, cutoff);
  await select(evidence(page), 'H1 chart pane').selectOption('2');
  const response = await pending;
  const body = await response.json();
  const saved = await savePosition(page, 'delayed', id);
  const receipt = {browser: browser.version(), id, cutoff, primary, saved, peer_http: response.status(), peer_api: body};
  saveReceipt('delayed-initial', receipt);
  expect(response.status(), await response.text()).toBe(200);
  const peer = await verifyHour(page, 'GBPUSD', reference.start);
  saveReceipt('delayed-initial', {...receipt, peer});
});

test('initial: a whole missing open hour remains selectable after later candles', async ({page, browser}) => {
  const id = await createReplay(page, '16:00');
  const cutoff = reference.long_end;
  await verifyCutoff(page, cutoff);
  await select(evidence(page), 'H1 chart pane').selectOption('2');
  const later = await verifyHour(page, 'GBPUSD', cutoff - 3600);
  const available = await select(evidence(page), 'H1 interval').locator('option').evaluateAll(
    nodes => nodes.map(node => (node as HTMLOptionElement).value));
  const saved = await savePosition(page, 'missing', id);
  const receipt = {browser: browser.version(), id, cutoff, later, saved, available};
  saveReceipt('missing-initial', receipt);
  expect(available).toContain(String(reference.short_end));
  await select(evidence(page), 'H1 interval').selectOption(String(reference.short_end));
  const missing = await verifyHour(page, 'GBPUSD', reference.short_end);
  saveReceipt('missing-initial', {...receipt, missing});
  await evidence(page).screenshot({path: join(output, 'missing-initial.png')});
});

test('restart: both panes remain inspectable at the delayed checkpoint', async ({browser}) => {
  const {context, page, saved} = await reopenReplay(browser, 'delayed');
  try {
    const cutoff = reference.short_end + reference.delay;
    const pending = waitForHour(page, '2', reference.start, cutoff);
    await select(page, 'Resume saved replay').selectOption(saved.id);
    await verifyCutoff(page, cutoff);
    await expect(select(evidence(page), 'H1 chart pane')).toHaveValue('2');
    const response = await pending;
    const receipt = {browser: browser.version(), ...saved, cutoff, peer_http: response.status(),
      peer_api: await response.json()};
    saveReceipt('delayed-restart', receipt);
    expect(response.status(), await response.text()).toBe(200);
    const peer = await verifyHour(page, 'GBPUSD', reference.start);
    await select(evidence(page), 'H1 chart pane').selectOption('primary');
    const primary = await verifyHour(page, 'EURUSD', reference.start);
    const initial = JSON.parse(readFileSync(join(output, 'delayed-initial.json'), 'utf8'));
    expect({primary, peer}).toEqual({primary: initial.primary, peer: initial.peer});
    saveReceipt('delayed-restart', {...receipt, primary, peer});
    await evidence(page).screenshot({path: join(output, 'delayed-restart.png')});
  } finally {
    await context.close();
  }
});

test('restart: the empty hour can still be reselected at the later checkpoint', async ({browser}) => {
  const {context, page, saved} = await reopenReplay(browser, 'missing');
  try {
    const cutoff = reference.long_end;
    await select(page, 'Resume saved replay').selectOption(saved.id);
    await verifyCutoff(page, cutoff);
    await expect(select(evidence(page), 'H1 chart pane')).toHaveValue('2');
    const available = await select(evidence(page), 'H1 interval').locator('option').evaluateAll(
      nodes => nodes.map(node => (node as HTMLOptionElement).value));
    const receipt = {browser: browser.version(), ...saved, cutoff, available};
    saveReceipt('missing-restart', receipt);
    expect(available).toContain(String(reference.short_end));
    await select(evidence(page), 'H1 interval').selectOption(String(reference.short_end));
    const missing = await verifyHour(page, 'GBPUSD', reference.short_end);
    const initial = JSON.parse(readFileSync(join(output, 'missing-initial.json'), 'utf8'));
    expect(missing).toEqual(initial.missing);
    saveReceipt('missing-restart', {...receipt, missing});
    await evidence(page).screenshot({path: join(output, 'missing-restart.png')});
  } finally {
    await context.close();
  }
});
