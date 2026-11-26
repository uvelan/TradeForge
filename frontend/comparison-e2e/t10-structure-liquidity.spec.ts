import {readFileSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {expect, test, type Locator, type Page} from '@playwright/test';
import type {ReplaySession} from '../src/types';

type Symbol = 'EURUSD' | 'GBPUSD';
type View = 'primary' | '2';
type Pin = {id: string; sha256: string};
type Prices = Record<'open' | 'high' | 'low' | 'close', string | number>;
type Bar = Prices & {time: number; available_at: number};
type Expected = {
  type: string; side: string; anchor_time: number; observation_time: number;
  available_at: number; confirmation_time: number | null; level_price: string | number;
  direction: string | null; status: string; reference_opens: number[]; displacement?: string;
};
type Summary = Omit<Expected, 'reference_opens' | 'displacement'> & {
  id: string; rule_version: number; supporting_ids: string[];
  level_available_at?: number; break_price?: number; displacement?: number;
  liquidity_side?: string; lower?: number; upper?: number; state_before?: string; state_after?: string;
};
type Source = Bar & {
  source_id: string; partitions: Pin[]; availability_basis: string; roles: {role: string; event_id: string}[];
};
type Receipt = Summary & {evidence: Source[]};
type EventPage = {
  session_id: string; symbol: Symbol; view: View; timeframe: string; cutoff: number;
  feed_id: string; price_basis: string; events: Summary[]; total: number; previous_before: number | null; selected: Receipt | null;
};
type Oracle = {
  date_utc: string; range: [number, number];
  symbols: Record<Symbol, {csv_sha256: string; partition: Pin; bars: Bar[]; events: Expected[]}>;
};
type Timings = Record<string, number>;

const reference: Oracle = JSON.parse(readFileSync(process.env.TRADEFORGE_T10_STRUCTURE_REFERENCE!, 'utf8'));
const storage = process.env.TRADEFORGE_PAIR_STORAGE!;
const output = process.env.TRADEFORGE_T10_CHART_OUTPUT!;
const reopen = process.env.TRADEFORGE_PAIR_REOPEN === '1';
const statePath = join(storage, 't10-structure-browser-state.json');
const checkpointPath = join(storage, 't10-structure-checkpoint.json');
const [start, end] = reference.range;
const frame = 300;
const prices = ['open', 'high', 'low', 'close'] as const;
const words = (value: string) => value.replaceAll('_', ' ');
const iso = (time: number) => new Date(time * 1000).toISOString();
const utc = (time: number) => iso(time).replace('T', ' ').replace('.000Z', ' UTC');
const select = (scope: Page | Locator, name: string) => scope.getByRole('combobox', {name, exact: true});
const button = (scope: Page | Locator, name: string) => scope.getByRole('button', {name, exact: true});
const replay = (page: Page) => page.getByLabel('Historical replay', {exact: true});
const panel = (page: Page) => page.getByRole('region', {name: 'Replay structure and liquidity'});
const events = (page: Page) => select(panel(page), 'Replay event');
const selectedReceipt = (page: Page) => panel(page).getByRole('article', {name: 'Selected replay event'});
const pane = (page: Page) => select(panel(page), 'Event chart pane');

if (reopen) test.use({storageState: statePath});

function waitForEvents(page: Page, view: View, cutoff: number, eventId?: string) {
  return page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname.endsWith('/structure-liquidity') && url.searchParams.get('view') === view &&
      url.searchParams.get('cutoff') === String(cutoff) &&
      (eventId === undefined || url.searchParams.get('event_id') === eventId);
  }).then(async response => {
    expect(response.status(), await response.text()).toBe(200);
    return await response.json() as EventPage;
  });
}

async function seek(page: Page, cutoff: number) {
  await replay(page).getByLabel('Jump to UTC', {exact: true}).fill(iso(cutoff).slice(0, 16));
  await button(replay(page), 'Seek').click();
  await expect(replay(page).locator('output')).toContainText(iso(cutoff));
  await expect(panel(page)).toContainText('M5 replay cutoff ' + utc(cutoff));
}

async function openTerminal(page: Page) {
  await page.setViewportSize({width: 1536, height: 960});
  await page.goto('/?timezone=UTC');
  await button(page, 'Market Terminal').click();
}

async function setOppositePeer(page: Page, peer: Symbol) {
  const chart = page.getByLabel('Chart view 2', {exact: true});
  await select(chart, 'View 2 instrument').selectOption(peer);
  await select(chart, 'View 2 timeframe').selectOption('M5');
}

async function createSession(page: Page, primary: Symbol, peer: Symbol, timings: Timings) {
  const requests: string[] = [];
  const recordRequest = (request: {url(): string}) => {
    if (new URL(request.url()).pathname.endsWith('/structure-liquidity')) requests.push(request.url());
  };
  page.on('request', recordRequest);
  const created = page.waitForResponse(response => response.url().endsWith('/replay/sessions') &&
    response.request().method() === 'POST');
  await button(page, 'Bar replay').click();
  const response = await created;
  expect(response.status(), await response.text()).toBe(201);
  const session: ReplaySession = await response.json();
  expect(session.market.partitions).toEqual([reference.symbols[primary].partition]);
  expect(session.views['2'].symbol).toBe(peer);
  expect(session.views['2']).toMatchObject({partitions: [reference.symbols[peer].partition]});
  expect(session.market.candles.length).toBe((end - start) / frame);
  await expect(panel(page)).toHaveCount(0);
  expect(requests).toHaveLength(0);
  const opening = performance.now();
  await page.locator('summary').getByText('Replay structure and liquidity', {exact: true}).click();
  await expect(events(page)).toHaveValue('');
  timings[primary + ' open inspector and event list'] = performance.now() - opening;
  expect(requests).toHaveLength(1);
  page.off('request', recordRequest);
  return session;
}

function matches(event: Summary, expected: Expected) {
  return event.type === expected.type && event.side === expected.side &&
    event.anchor_time === expected.anchor_time && event.observation_time === expected.observation_time &&
    event.available_at === expected.available_at && Number(event.level_price) === Number(expected.level_price);
}

function verifySourceRows(receipt: Receipt, symbol: Symbol, expected: Expected) {
  const {bars, partition} = reference.symbols[symbol];
  const byTime = new Map(bars.map(bar => [bar.time, bar]));
  const sourceTimes = receipt.evidence.map(row => row.time);
  expect(new Set(sourceTimes).size).toBe(sourceTimes.length);
  expect(sourceTimes).toEqual(expect.arrayContaining(expected.reference_opens));
  const anchor = receipt.evidence.find(row => row.time === expected.anchor_time);
  const observation = receipt.evidence.find(row => row.time === expected.observation_time);
  expect(anchor?.roles).toEqual(expect.arrayContaining([expect.objectContaining({role: 'PIVOT_ANCHOR'})]));
  expect(observation?.roles).toContainEqual({role: 'EVENT_OBSERVATION', event_id: receipt.id});
  expect(receipt.evidence.some(row => row.roles.length === 0)).toBe(true);
  for (const row of receipt.evidence) {
    const bar = byTime.get(row.time);
    expect(bar).toBeDefined();
    expect(row.partitions).toEqual([partition]);
    expect(row.source_id).toBe(partition.id + ':' + row.time);
    expect(row.availability_basis).toBe('MODELED_NOMINAL_CLOSE');
    expect(row.available_at).toBe(bar!.available_at);
    expect(row.available_at).toBeLessThanOrEqual(expected.available_at);
    for (const price of prices) expect(row[price]).toBe(Number(bar![price]));
  }
}

async function verifyReceipt(page: Page, receipt: Receipt) {
  const article = selectedReceipt(page);
  await expect(article).toHaveAttribute('data-event-id', receipt.id);
  await expect(article).toHaveAttribute('data-available-at', String(receipt.available_at));
  const fields = Object.fromEntries([
    ['Event', words(receipt.type)], ['Side', words(receipt.side)], ['Direction', receipt.direction ?? '—'],
    ['Status', words(receipt.status)], ['Level', String(receipt.level_price)],
    ['Anchor', utc(receipt.anchor_time)], ['Observation', utc(receipt.observation_time)],
    ['Confirmation', receipt.confirmation_time === null ? 'Unconfirmed' : utc(receipt.confirmation_time)],
    ['First available', utc(receipt.available_at)], ['Rule version', String(receipt.rule_version)],
    ['Event ID', receipt.id],
    ...(receipt.level_available_at != null ? [['Level first available', utc(receipt.level_available_at)]] : []),
    ...(receipt.break_price != null ? [['Crossing price', String(receipt.break_price)]] : []),
    ...(receipt.displacement != null ? [['Directional body', String(receipt.displacement)], ['MSS threshold (price)', '0.001']] : []),
    ...(receipt.liquidity_side ? [['Liquidity side', receipt.liquidity_side]] : []),
    ...(receipt.lower != null && receipt.upper != null ? [['Pool range', receipt.lower + ' – ' + receipt.upper]] : []),
    ...(receipt.state_before && receipt.state_after ? [['State change', words(receipt.state_before) + ' → ' + words(receipt.state_after)]] : []),
    ...(receipt.supporting_ids.length ? [['Supporting events', receipt.supporting_ids.join(', ')]] : []),
  ]);
  for (const [field, value] of Object.entries(fields)) {
    await expect(article.getByText(field, {exact: true}).locator('..').locator('dd')).toHaveText(value);
  }
  const direct = receipt.evidence.filter(row => row.roles.length > 0);
  const context = receipt.evidence.filter(row => row.roles.length === 0);
  const disclosure = article.locator('details').filter({hasText: direct.length + ' direct source candles'});
  if (await disclosure.getAttribute('open') === null) await disclosure.locator('summary').click();
  const pins = [...new Map(receipt.evidence.flatMap(row => row.partitions).map(pin => [pin.id, pin])).values()];
  await expect(disclosure.locator('li')).toHaveText(pins.map(pin => 'Dataset ' + pin.id + ' · SHA-256 ' + pin.sha256));
  const directRows = await verifySourceTable(article, 'Direct event source candles', direct);
  const details = article.locator('details').filter({hasText: context.length + ' calculation context candles'});
  await details.locator('summary').click();
  const contextRows = await verifySourceTable(article, 'Calculation context candles', context);
  await details.locator('summary').click();
  return {fields, pins, directRows, contextRows};
}

async function verifySourceTable(article: Locator, name: string, expected: Source[]) {
  const rows = article.getByRole('table', {name}).locator('tbody tr');
  await expect(rows).toHaveCount(expected.length);
  const displayed = await rows.evaluateAll(elements => elements.map(element => {
    const cells = [...(element as HTMLTableRowElement).cells];
    const roles = [...(cells[8]?.children ?? [])].map(role => ({
      role: role.firstChild?.textContent?.trim(),
      event_id: role.querySelector('code')?.title,
    }));
    return {values: cells.slice(0, 8).map(cell => cell.innerText.trim()), roles};
  }));
  expect(displayed).toEqual(expected.map(row => ({
    values: [utc(row.time), utc(row.available_at), words(row.availability_basis),
      ...prices.map(price => String(row[price])), row.source_id],
    roles: row.roles.map(({role, event_id}) => ({role: words(role).toLowerCase(), event_id})),
  })));
  return displayed;
}

async function verifyUnavailable(page: Page, id: string) {
  await expect(events(page)).toHaveValue(id);
  await expect(events(page).locator('option:checked')).toHaveText('Selected event unavailable');
  await expect(panel(page).getByRole('status')).toHaveText('Selected event unavailable at this cutoff.');
  await expect(selectedReceipt(page)).toHaveCount(0);
}

async function chooseAtFirstAvailability(page: Page, view: View, symbol: Symbol, expected: Expected, timing: Timings) {
  const before = expected.available_at - frame;
  const previous = waitForEvents(page, view, before);
  await seek(page, before);
  expect((await previous).events.some(event => matches(event, expected))).toBe(false);
  const boundary = waitForEvents(page, view, expected.available_at);
  const began = performance.now();
  await button(replay(page), 'Next candle').click();
  const next = await boundary;
  const matched = next.events.filter(event => matches(event, expected));
  expect(matched).toHaveLength(1);
  const id = matched[0].id;
  await expect(events(page).locator('option[value="' + id + '"]')).toHaveCount(1);
  timing[symbol + ' ' + expected.type + ' step and event list'] = performance.now() - began;
  const exact = waitForEvents(page, view, expected.available_at, id);
  const picked = performance.now();
  await events(page).selectOption(id);
  const {selected} = await exact;
  expect(selected).toMatchObject({
    id, type: expected.type, side: expected.side, direction: expected.direction,
    status: expected.status, anchor_time: expected.anchor_time, observation_time: expected.observation_time,
    available_at: expected.available_at, confirmation_time: expected.confirmation_time,
    level_price: Number(expected.level_price),
  });
  await expect(selectedReceipt(page)).toHaveAttribute('data-event-id', id);
  timing[symbol + ' ' + expected.type + ' select and receipt'] = performance.now() - picked;
  verifySourceRows(selected!, symbol, expected);
  return {receipt: selected!, shown: await verifyReceipt(page, selected!)};
}

function first(symbol: Symbol, type: string) {
  const event = reference.symbols[symbol].events.find(candidate => candidate.type === type);
  if (!event) throw new Error(symbol + ' independent reference is missing ' + type);
  return event;
}

test('initial: sees first availability, retains scoped selections and ignores a late genuine event receipt', async ({page, context, browser}) => {
  test.skip(reopen);
  test.setTimeout(150_000);
  const timings: Timings = {};
  await openTerminal(page);
  await select(page, 'Market data source').selectOption('DUKASCOPY_SAVED');
  await page.getByLabel('From UTC', {exact: true}).fill(reference.date_utc);
  await page.getByLabel('From time (UTC)', {exact: true}).fill(iso(start).slice(11, 16));
  await page.getByLabel('Until UTC (exclusive)', {exact: true}).fill(reference.date_utc);
  await page.getByLabel('Until time (UTC)', {exact: true}).fill(iso(end).slice(11, 16));
  await button(page, 'Load saved range').click();
  await select(page, 'Chart layout').selectOption('2');
  await setOppositePeer(page, 'GBPUSD');
  const session = await createSession(page, 'EURUSD', 'GBPUSD', timings);

  const bos = await chooseAtFirstAvailability(page, 'primary', 'EURUSD', first('EURUSD', 'BOS'), timings);
  const choch = await chooseAtFirstAvailability(page, 'primary', 'EURUSD', first('EURUSD', 'CHOCH'), timings);
  const eql = await chooseAtFirstAvailability(page, 'primary', 'EURUSD', first('EURUSD', 'EQL'), timings);
  await pane(page).selectOption('2');
  await expect(events(page)).toHaveValue('');
  const swept = await chooseAtFirstAvailability(page, '2', 'GBPUSD', first('GBPUSD', 'SWEPT'), timings);
  const mss = await chooseAtFirstAvailability(page, '2', 'GBPUSD', first('GBPUSD', 'MSS'), timings);
  expect(mss.receipt.supporting_ids.length).toBeGreaterThan(0);
  expect(mss.receipt.displacement).toBe(Number(first('GBPUSD', 'MSS').displacement));

  const id = mss.receipt.id;
  const available = mss.receipt.available_at;
  const intercepted = Promise.withResolvers<Receipt | null>();
  const release = Promise.withResolvers<void>();
  const delivered = Promise.withResolvers<void>();
  const pattern = '**/structure-liquidity?*';
  await page.route(pattern, async route => {
    const params = new URL(route.request().url()).searchParams;
    if (params.get('view') !== '2' || params.get('event_id') !== id ||
      params.get('cutoff') !== String(available + frame)) return route.continue();
    const genuine = await route.fetch();
    intercepted.resolve((await genuine.json() as EventPage).selected);
    await release.promise;
    try {
      await route.fulfill({response: genuine});
    } finally {
      delivered.resolve();
    }
  });
  const late = waitForEvents(page, '2', available + frame, id);
  const staleStart = performance.now();
  try {
    await seek(page, available + frame);
    const held = await intercepted.promise;
    expect(held).toEqual(mss.receipt);
    const rewound = waitForEvents(page, '2', available - frame, id);
    await seek(page, available - frame);
    expect((await rewound).selected).toBeNull();
    await verifyUnavailable(page, id);
    const earlierPrimary = waitForEvents(page, 'primary', available - frame, eql.receipt.id);
    await pane(page).selectOption('primary');
    expect((await earlierPrimary).selected).toEqual(eql.receipt);
    release.resolve();
    await delivered.promise;
    expect((await late).selected).toEqual(mss.receipt);
    await expect(selectedReceipt(page)).toHaveAttribute('data-event-id', eql.receipt.id);
    await pane(page).selectOption('2');
    await verifyUnavailable(page, id);
    timings['GBPUSD MSS delayed upstream response after rewind and pane change'] = performance.now() - staleStart;
  } finally {
    release.resolve();
    await page.unroute(pattern);
  }

  const final = waitForEvents(page, '2', end, id);
  await button(replay(page), 'Last candle').click();
  const finalPage = await final;
  expect(finalPage.selected).toEqual(mss.receipt);
  expect(await verifyReceipt(page, mss.receipt)).toEqual(mss.shown);
  const primary = waitForEvents(page, 'primary', end, eql.receipt.id);
  await pane(page).selectOption('primary');
  const primaryPage = await primary;
  expect(primaryPage.selected).toEqual(eql.receipt);
  expect(primaryPage.events.some(event => event.id === eql.receipt.id)).toBe(false);
  expect(await verifyReceipt(page, eql.receipt)).toEqual(eql.shown);
  const older = waitForEvents(page, 'primary', end, eql.receipt.id);
  await button(panel(page), 'Older replay events').click();
  expect((await older).selected).toEqual(eql.receipt);
  await expect(selectedReceipt(page)).toHaveAttribute('data-event-id', eql.receipt.id);
  await pane(page).selectOption('2');
  await expect(selectedReceipt(page)).toHaveAttribute('data-event-id', id);
  await button(replay(page), 'Save replay position').click();
  await expect(replay(page).getByText('Replay position saved', {exact: true})).toBeVisible();
  await context.storageState({path: statePath});

  await button(replay(page), 'Exit replay').click();
  await page.locator('.market-watchlist').getByRole('button', {name: /^GBPUSD/}).click();
  await setOppositePeer(page, 'EURUSD');
  const swapped = await createSession(page, 'GBPUSD', 'EURUSD', timings);
  expect(swapped.id).not.toBe(session.id);
  await expect(pane(page)).toHaveValue('primary');
  await expect(events(page)).toHaveValue('');
  await pane(page).selectOption('2');
  await expect(panel(page)).toContainText('EURUSD M5');
  await expect(events(page)).toHaveValue('');
  await expect(selectedReceipt(page)).toHaveCount(0);

  const checkpoint = {
    session_id: session.id, swapped_session_id: swapped.id, cutoff: end, timings_ms: timings,
    chromium: browser.version(), observed: {bos, choch, eql, swept, mss},
    held_response_selected_id: id, rewind_missing_at: available - frame,
    sources: Object.fromEntries(Object.entries(reference.symbols).map(([symbol, lane]) =>
      [symbol, {csv_sha256: lane.csv_sha256, partition: lane.partition}])),
  };
  writeFileSync(checkpointPath, JSON.stringify(checkpoint));
  writeFileSync(join(output, 'structure-liquidity-initial.json'), JSON.stringify(checkpoint, null, 2) + '\n');
});

test('restart: restores the saved cutoff and two pane selections in fresh services', async ({page, browser}) => {
  test.skip(!reopen);
  test.setTimeout(100_000);
  const checkpoint: {
    session_id: string; cutoff: number; observed: Record<'eql' | 'mss', {receipt: Receipt; shown: unknown}>;
  } = JSON.parse(readFileSync(checkpointPath, 'utf8'));
  const {eql, mss} = checkpoint.observed;
  const timings: Timings = {};
  await openTerminal(page);
  const peer = waitForEvents(page, '2', checkpoint.cutoff, mss.receipt.id);
  const began = performance.now();
  await select(page, 'Resume saved replay').selectOption(checkpoint.session_id);
  expect((await peer).selected).toEqual(mss.receipt);
  await expect(replay(page).locator('output')).toContainText(iso(checkpoint.cutoff));
  await expect(pane(page)).toHaveValue('2');
  await expect(events(page)).toHaveValue(mss.receipt.id);
  await expect(selectedReceipt(page)).toHaveAttribute('data-event-id', mss.receipt.id);
  timings['resume and selected GBPUSD receipt'] = performance.now() - began;
  const rhs = await verifyReceipt(page, mss.receipt);
  expect(rhs).toEqual(mss.shown);
  await page.setViewportSize({width: 1536, height: 1500});
  await page.locator('.replay-structure-evidence').screenshot({path: join(output, 'structure-liquidity-reopened.png')});
  await page.setViewportSize({width: 1536, height: 960});

  const primary = waitForEvents(page, 'primary', checkpoint.cutoff, eql.receipt.id);
  const switching = performance.now();
  await pane(page).selectOption('primary');
  expect((await primary).selected).toEqual(eql.receipt);
  await expect(selectedReceipt(page)).toHaveAttribute('data-event-id', eql.receipt.id);
  timings['swap to and restore EURUSD receipt'] = performance.now() - switching;
  const lhs = await verifyReceipt(page, eql.receipt);
  expect(lhs).toEqual(eql.shown);

  const before = eql.receipt.available_at - frame;
  const missing = waitForEvents(page, 'primary', before, eql.receipt.id);
  await seek(page, before);
  expect((await missing).selected).toBeNull();
  await verifyUnavailable(page, eql.receipt.id);
  const recovery = waitForEvents(page, 'primary', eql.receipt.available_at, eql.receipt.id);
  await button(replay(page), 'Next candle').click();
  expect((await recovery).selected).toEqual(eql.receipt);
  expect(await verifyReceipt(page, eql.receipt)).toEqual(lhs);
  writeFileSync(join(output, 'structure-liquidity-restart.json'), JSON.stringify({
    session_id: checkpoint.session_id, cutoff: checkpoint.cutoff, chromium: browser.version(),
    restored: {primary: lhs, peer: rhs}, timings_ms: timings, rewind_missing_at: before,
  }, null, 2) + '\n');
});
