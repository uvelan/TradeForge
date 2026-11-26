import {readFileSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {chromium, expect, test, type Locator, type Page, type Response} from '@playwright/test';
import type {ReplayCrtPage, ReplayCrtReceipt, ReplayCrtRevision} from '../src/replayCrtTypes';
import type {ReplaySourceBar} from '../src/ReplayEvidenceSources';
import type {ReplaySession} from '../src/types';

type Symbol = 'EURUSD' | 'GBPUSD';
type View = 'primary' | '2';
type Pin = ReplaySourceBar['partitions'][number];
type Row = Omit<ReplaySourceBar, 'source_id' | 'partitions' | 'availability_basis'>;
type Moment = Pick<ReplayCrtRevision, 'state' | 'reason' | 'observation_time' | 'confirmation_time' | 'available_at'>;
type Lane = {
  bars: Row[]; partitions: Pin[]; origins: Record<string, Pin>; basis?: ReplaySourceBar['availability_basis'];
  reference_start: number; reference_end: number; checkpoints: Moment[];
  previous?: number; successor?: number; missing_open_intervals?: {start: number; end: number}[];
};
type Manifest = {start: number; end: number; symbols: Record<Symbol, Lane>};
type Reading = {page: ReplayCrtPage; bytes: number};
type Sample = {ms: number; page_nodes: number; scope_nodes: number; source_rows: number};
type Saved = {session: string; cutoff: number; exactCutoff: number; receipts: Record<Symbol, ReplayCrtReceipt>};

const manifest: Manifest = JSON.parse(readFileSync(process.env.TRADEFORGE_T10_CRT_REFERENCE!, 'utf8'));
const mode = process.env.TRADEFORGE_T10_CRT_CASE!;
for (const lane of Object.values(manifest.symbols)) {
  for (const bar of lane.bars) {
    for (const key of ['open', 'high', 'low', 'close'] as const) bar[key] = Number(bar[key]);
  }
}
const output = process.env.TRADEFORGE_T10_CHART_OUTPUT!;
const profile = process.env.TRADEFORGE_T10_CRT_PROFILE!;
const title = 'Replay Candle Range Theory';
const frame = 300;
const iso = (stamp: number) => new Date(stamp * 1000).toISOString();
const utc = (stamp: number) => iso(stamp).replace('T', ' ').replace('.000Z', ' UTC');
const words = (s: string) => s.replaceAll('_', ' ');
const section = (page: Page) => page.getByRole('region', {name: title, exact: true});
const inspector = (page: Page) => page.locator('details.replay-crt-evidence');
const pane = (page: Page) => combo(section(page), 'CRT chart pane');
const ranges = (page: Page) => combo(section(page), 'CRT H1 reference');
const selected = (page: Page) => section(page).getByRole('article', {name: 'Selected CRT range'});
const replay = (page: Page) => page.getByLabel('Historical replay', {exact: true});
const legacy = (page: Page) => page.getByRole('region', {name: 'CRT evidence', exact: true});
const legacyDetail = (page: Page) => legacy(page).locator('dl[aria-label="Selected CRT event"]');
const combo = (scope: Page | Locator, name: string) => scope.getByRole('combobox', {name, exact: true});
const button = (scope: Page | Locator, name: string) => scope.getByRole('button', {name, exact: true});
const field = (scope: Locator, label: string) => scope.getByText(label, {exact: true}).locator('..').locator('dd');
const file = (name: string) => join(output, name + '.json');
const load = <T,>(name: string): T => JSON.parse(readFileSync(file(name), 'utf8'));
function record(name: string, value: object) {
  const revision = spawnSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'});
  writeFileSync(file(name), JSON.stringify({git_revision: revision.stdout?.trim(), worker_pid: process.pid,
    case: mode, ...value}, null, 2) + '\n');
}

const isCrt = (url: URL) => url.pathname.endsWith('/crt');
const expectedRow = (lane: Lane, time: number) => lane.bars.find(row => row.time === time)!;
const cutoffOf = (symbol: Symbol) => manifest.symbols[symbol].bars.at(-1)!.available_at;
const finalArrival = Math.max(...Object.values(manifest.symbols).map(lane => lane.checkpoints.at(-1)!.available_at));
const benchmarkCutoff = manifest.symbols.EURUSD.bars.find(row => row.available_at > finalArrival)!.available_at;

async function payload<T>(response: Response, status = 200) {
  expect(response.status(), await response.text()).toBe(status);
  return await response.json() as T;
}

function getCrt(page: Page, cutoff: number, view: View, id?: string): Promise<Reading> {
  return page.waitForResponse(response => {
    const url = new URL(response.url());
    return isCrt(url) && url.searchParams.get('cutoff') === String(cutoff) && url.searchParams.get('view') === view &&
      url.searchParams.get('range_id') === (id ?? null);
  }).then(async response => ({page: await payload<ReplayCrtPage>(response), bytes: (await response.body()).length}));
}

async function profileRun(baseURL: string | undefined, work: (page: Page) => Promise<void>, legacyProfile = false) {
  const context = await chromium.launchPersistentContext(profile + (legacyProfile ? '-legacy' : ''), {
    baseURL, serviceWorkers: 'block', viewport: {width: 1536, height: 960},
  });
  try {
    await work(context.pages()[0] ?? await context.newPage());
  } finally {
    await context.close();
  }
}

async function openTerminal(page: Page) {
  await page.goto('/?timezone=UTC');
  await button(page, 'Market Terminal').click();
}

async function instrument(page: Page, primary: Symbol, peer: Symbol) {
  await page.locator('.market-watchlist button').filter({hasText: primary}).click();
  const chart = page.getByLabel('Chart view 2', {exact: true});
  await combo(chart, 'View 2 instrument').selectOption(peer);
  await combo(chart, 'View 2 timeframe').selectOption('M5');
}

async function configure(page: Page) {
  await openTerminal(page);
  await combo(page, 'Market data source').selectOption('DUKASCOPY_SAVED');
  await page.getByLabel('From UTC', {exact: true}).fill(iso(manifest.start).slice(0, 10));
  await page.getByLabel('From time (UTC)', {exact: true}).fill(iso(manifest.start).slice(11, 16));
  await page.getByLabel('Until UTC (exclusive)', {exact: true}).fill(iso(manifest.end).slice(0, 10));
  await page.getByLabel('Until time (UTC)', {exact: true}).fill(iso(manifest.end).slice(11, 16));
  await button(page, 'Load saved range').click();
  await combo(page, 'Chart layout').selectOption('2');
  await instrument(page, 'EURUSD', 'GBPUSD');
}

async function startSession(page: Page, primary: Symbol, peer: Symbol) {
  const created = page.waitForResponse(response => response.url().endsWith('/replay/sessions') &&
    response.request().method() === 'POST');
  await button(page, 'Bar replay').click();
  const result = await payload<ReplaySession>(await created, 201);
  for (const [market, symbol] of [[result.market, primary], [result.views['2'], peer]] as const) {
    if (!market || 'error' in market) throw new Error('Frozen replay pane unavailable for ' + symbol);
    expect(market.partitions).toEqual(manifest.symbols[symbol].partitions);
    expect(market.candles.map(bar => [bar.time, bar.available_at])).toEqual(
      manifest.symbols[symbol].bars.map(bar => [bar.time, bar.available_at]));
  }
  return result.id;
}

async function moveChart(page: Page, time: number) {
  const [date, minute] = [iso(time).slice(0, 10), iso(time).slice(11, 16)];
  await replay(page).getByLabel('Jump to UTC', {exact: true}).fill(date + 'T' + minute);
  await button(replay(page), 'Seek').click();
  if (time % 60 !== 0) await button(replay(page), 'Next candle').click();
  await expect(replay(page).locator('output')).toContainText(iso(time));
}

async function savePosition(page: Page) {
  await button(replay(page), 'Save replay position').click();
  await expect(replay(page).getByText('Replay position saved', {exact: true})).toBeVisible();
}

async function probe(page: Page, time: number) {
  await section(page).getByLabel('CRT as of (UTC)', {exact: true}).fill(iso(time).slice(0, 19));
  await button(section(page), 'Inspect CRT cutoff').click();
  await expect(section(page).locator('time[datetime="' + iso(time) + '"]')).toHaveText(utc(time));
}

async function ready(page: Page, id: string, moment?: Moment) {
  await expect(selected(page)).toHaveAttribute('data-range-id', id);
  if (moment) {
    await expect(selected(page)).toHaveAttribute('data-available-at', String(moment.available_at));
    await expect(field(selected(page), 'State')).toHaveText(words(moment.state));
    await expect(field(selected(page), 'Reason')).toHaveText(words(moment.reason));
  }
}

async function sample(started: number, scope: Locator): Promise<Sample> {
  const ms = Math.round((performance.now() - started) * 10) / 10;
  const counts = await scope.evaluate(el => ({page_nodes: document.querySelectorAll('*').length,
    scope_nodes: el.querySelectorAll('*').length, source_rows: el.querySelectorAll('tbody tr').length}));
  return {ms, ...counts};
}

async function verifyRange(page: Page, symbol: Symbol, reading: Reading, cutoff: number, id: string, expected: Moment[]) {
  expect(reading.page).toMatchObject({cutoff, symbol, source: 'DUKASCOPY_SAVED', price_basis: 'BID',
    authority: 'RESEARCH_ONLY', timeframe: 'M5'});
  const range = reading.page.selected;
  if (!expected.length) {
    expect(range).toBeNull();
    await expect(selected(page)).toHaveCount(0);
    await expect(section(page).getByText('Selected CRT range unavailable at this cutoff.', {exact: true})).toBeVisible();
    return null;
  }
  expect(range).not.toBeNull();
  const lane = manifest.symbols[symbol];
  const children = lane.bars.filter(row => row.time >= lane.reference_start && row.time < lane.reference_end);
  const highs = children.map(row => row.high), lows = children.map(row => row.low);
  const high = Math.max(...highs), low = Math.min(...lows), midpoint = Number(((high + low) / 2).toPrecision(15));
  const ohlc = [children[0].open, high, low, children[11].close];
  expect(range).toMatchObject({range_id: id, reference_start: lane.reference_start, reference_end: lane.reference_end,
    reference_available_at: expected[0].available_at, reference_ohlc: ohlc, high, low, midpoint,
    ...expected.at(-1)});
  expect(range!.revisions).toHaveLength(expected.length);
  // Use the oracle's five timing/state fields for each event, independent of producer IDs.
  const moment = ({state, reason, observation_time, confirmation_time, available_at}: Moment) =>
    ({state, reason, observation_time, confirmation_time, available_at});
  expect(range!.revisions.map(moment)).toEqual(expected.map(moment));
  expect(range!.id).toBe(range!.revisions.at(-1)!.id);
  expect(range!.reference_children).toHaveLength(12);
  expect(range!.reference_children.map(row => row.time)).toEqual(children.map(row => row.time));
  for (const row of [...range!.reference_children, ...range!.evidence]) {
    const pin = lane.origins[row.time];
    expect(row).toMatchObject({...expectedRow(lane, row.time), source_id: pin.id + ':' + row.time,
      partitions: [pin], availability_basis: lane.basis ?? 'ARCHIVE_SUPPLIED'});
    expect(row.available_at).toBeLessThanOrEqual(cutoff);
  }
  const sweep = expected.find(m => m.reason === 'SAME_CANDLE_RECLAIM');
  if (sweep) {
    const direction = sweep.state.startsWith('HIGH') ? 'HIGH' : 'LOW';
    const row = expectedRow(lane, sweep.observation_time);
    const boundary = direction === 'HIGH' ? high : low, extreme = direction === 'HIGH' ? row.high : row.low;
    expect(range).toMatchObject({sweep_side: direction, swept_boundary: boundary, sweep_extreme: extreme,
      reclaim_close: row.close, sweep_id: expect.stringMatching(/^[a-f0-9]{64}$/), reclaim_id: expect.stringMatching(/^[a-f0-9]{64}$/)});
  }
  await ready(page, id, expected.at(-1));
  await expect(selected(page)).toHaveAttribute('data-revision-id', range!.id);
  await expect(field(selected(page), 'Reference OHLC')).toHaveText(ohlc.join(' / '));
  await expect(field(selected(page), 'Reference low / midpoint / high')).toHaveText([low, midpoint, high].join(' / '));
  await expect(field(selected(page), 'Reference first available')).toHaveText(utc(expected[0].available_at));
  await expect(field(selected(page), 'Revision observed')).toHaveText(utc(range!.observation_time));
  await expect(field(selected(page), 'Revision confirmed')).toHaveText(utc(range!.confirmation_time));
  await expect(field(selected(page), 'Revision available')).toHaveText(utc(range!.available_at));
  await expect(field(selected(page), 'Reference ID')).toHaveText(range!.reference_id);
  const gap = selected(page).getByRole('region', {name: 'CRT missing open candles evidence'});
  if (range!.reason === 'MISSING_OPEN_CANDLES') {
    expect(range!.continuity).toMatchObject({previous_observed_open: lane.previous,
      next_observed_open: lane.successor, missing_open_intervals: lane.missing_open_intervals});
    await expect(gap).toHaveAttribute('data-revision-id', range!.id);
    await expect(field(gap, 'Missing candle opens [start, end) · UTC').locator('div')).toHaveText(
      lane.missing_open_intervals!.map(({start, end}) => '[' + utc(start) + ', ' + utc(end) + ')'));
    for (const [time, role, label] of [
      [lane.previous!, 'GAP_PREVIOUS_OBSERVED', 'Previous observed candle open'],
      [lane.successor!, 'GAP_NEXT_OBSERVED', 'Next observed candle open'],
    ] as const) {
      const witness = range!.evidence.find(row => row.time === time)!;
      expect(witness.roles).toContainEqual({role, event_id: range!.id});
      await expect(field(gap, label)).toContainText(witness.source_id);
      await expect(field(gap, label)).toContainText(lane.origins[time].sha256);
    }
  } else {
    await expect(gap).toHaveCount(0);
  }
  return range!;
}

async function expandSources(page: Page, range: ReplayCrtReceipt, repetitions = 1) {
  const receipt = selected(page);
  const disclosure = receipt.getByText('12 H1 source candles', {exact: true});
  const rows = receipt.locator('table[aria-label="CRT H1 source candles"] tbody tr');
  const samples: number[] = [];
  let measured: Sample | undefined;
  for (let index = 0; index < repetitions; index++) {
    await expect(rows).toHaveCount(0);
    const started = performance.now();
    await disclosure.click();
    await expect(rows).toHaveCount(12);
    measured = await sample(started, inspector(page));
    samples.push(measured.ms);
    if (index + 1 < repetitions) await disclosure.click();
  }
  const ordered = [...samples].sort((a, b) => a - b);
  const median = (ordered[Math.floor((ordered.length - 1) / 2)] + ordered[Math.floor(ordered.length / 2)]) / 2;
  const cells = await rows.evaluateAll(entries => entries.map(row => [...row.children].map(cell => cell.textContent)));
  expect(cells).toEqual(range.reference_children.map(row => [utc(row.time), utc(row.available_at),
    words(row.availability_basis), String(row.open), String(row.high), String(row.low), String(row.close), row.source_id]));
  for (const pin of new Map(range.reference_children.flatMap(row => row.partitions).map(pin => [pin.id, pin])).values()) {
    await expect(receipt.getByText(pin.sha256, {exact: true}).first()).toBeVisible();
  }
  await receipt.getByText('CRT lifecycle source candles', {exact: true}).click();
  const linked = receipt.getByRole('table', {name: 'CRT lifecycle source candles'});
  await expect(linked.locator('tbody tr')).toHaveCount(range.evidence.length);
  for (const rev of range.revisions.slice(1)) {
    const witness = linked.getByRole('row').filter({hasText: utc(rev.observation_time)});
    await expect(witness.locator('code[title="' + rev.id + '"]')).toHaveCount(
      range.evidence.find(row => row.time === rev.observation_time)!.roles.filter(role => role.event_id === rev.id).length);
  }
  await receipt.getByText(range.revisions.length + ' CRT lifecycle revisions', {exact: true}).click();
  const revisionList = receipt.getByRole('list', {name: 'CRT lifecycle revisions'});
  await expect(revisionList.locator('li')).toHaveCount(range.revisions.length);
  for (const rev of range.revisions) await expect(revisionList.getByText(rev.id, {exact: true})).toBeVisible();
  const {page_nodes, scope_nodes, source_rows} = measured!;
  return {n: samples.length, median_ms: median, p95_ms: ordered[Math.ceil(ordered.length * 0.95) - 1],
    samples_ms: samples, page_nodes, scope_nodes, source_rows, collapsed_source_rows: 0};
}

async function selectLatestRange(page: Page, cutoff: number, symbol: Symbol, view: View) {
  const lane = manifest.symbols[symbol];
  const option = ranges(page).locator('option').filter({hasText: utc(lane.reference_start) + ' · H1 · '});
  await expect(option).toHaveCount(1);
  const id = (await option.getAttribute('value'))!;
  const response = getCrt(page, cutoff, view, id);
  const started = performance.now();
  await ranges(page).selectOption(id);
  const received = await response;
  await ready(page, id);
  const measured = await sample(started, inspector(page));
  return {received, id, measured};
}

async function checkBoundaries(page: Page, symbol: Symbol, view: View, id: string) {
  const history = manifest.symbols[symbol].checkpoints;
  const byTime = [...new Set(history.map(moment => moment.available_at))];
  const samples: Record<string, {bytes: number; revision_id: string | null; state: string | null}> = {};
  for (const boundary of byTime) {
    for (const cutoff of [boundary - 1, boundary]) {
      const response = getCrt(page, cutoff, view, id);
      await probe(page, cutoff);
      const result = await response;
      const matched = await verifyRange(page, symbol, result, cutoff, id, history.filter(m => m.available_at <= cutoff));
      samples[iso(cutoff)] = {bytes: result.bytes, revision_id: matched?.id ?? null, state: matched?.state ?? null};
    }
  }
  return samples;
}

async function stalePane(page: Page, cutoff: number, primaryId: string, peerId: string) {
  const staleCutoff = manifest.symbols.EURUSD.checkpoints[0].available_at - 1;
  const predicate = (url: URL) => isCrt(url) && url.searchParams.get('view') === 'primary' &&
    url.searchParams.get('cutoff') === String(staleCutoff);
  let fetched!: () => void, release!: () => void;
  const requested = new Promise<void>(resolve => {fetched = resolve;});
  const delayed = new Promise<void>(resolve => {release = resolve;});
  await page.route(predicate, async route => {
    const original = await route.fetch();
    fetched();
    await delayed;
    await route.fulfill({response: original});
  });
  try {
    await probe(page, staleCutoff);
    await requested;
    await pane(page).selectOption('2');
    const current = getCrt(page, cutoff, '2', peerId);
    await button(section(page), 'Use replay cutoff').click();
    await current;
    await ready(page, peerId);
    const stale = getCrt(page, staleCutoff, 'primary', primaryId);
    release();
    expect((await stale).page.selected).toBeNull();
    await expect(pane(page)).toHaveValue('2');
    await ready(page, peerId);
  } finally {
    release();
    await page.unroute(predicate);
  }
}

test('@phase-initial@end CRT preserves cutoff, witnesses and swapped pane identity', async ({baseURL}) => {
  test.setTimeout(180_000);
  await profileRun(baseURL, async page => {
    const urls: string[] = [];
    page.on('request', req => {if (isCrt(new URL(req.url()))) urls.push(req.url());});
    await configure(page);
    const started = performance.now();
    const session = await startSession(page, 'EURUSD', 'GBPUSD');
    const cutoff = benchmarkCutoff;
    await moveChart(page, cutoff);
    await expect(inspector(page)).toHaveJSProperty('open', false);
    const collapsed = await sample(started, inspector(page));
    expect(urls).toHaveLength(0);
    expect(collapsed.scope_nodes).toBe(1);
    const listing = getCrt(page, cutoff, 'primary');
    const startList = performance.now();
    await inspector(page).locator('summary').click();
    const list = await listing;
    await expect(ranges(page)).toBeVisible();
    const listed = await sample(startList, inspector(page));
    const primary = await selectLatestRange(page, cutoff, 'EURUSD', 'primary');
    const primaryValue = await verifyRange(page, 'EURUSD', primary.received, cutoff, primary.id,
      manifest.symbols.EURUSD.checkpoints) as ReplayCrtReceipt;
    const beforeExpansion = urls.length;
    const sources = await expandSources(page, primaryValue, 20);
    expect(urls).toHaveLength(beforeExpansion);
    await page.screenshot({path: join(output, 'crt-primary-sources.png'), fullPage: true});
    const primaryBoundaries = await checkBoundaries(page, 'EURUSD', 'primary', primary.id);
    const rewindTime = manifest.symbols.EURUSD.checkpoints.at(-1)!.available_at;
    // Put the chart itself on a causal arrival and test normal slider rewind independently of the inspector clock.
    const exact = getCrt(page, rewindTime, 'primary', primary.id);
    await moveChart(page, rewindTime);
    expect((await exact).page.selected).toEqual(primaryValue);
    const previous = manifest.symbols.EURUSD.bars.filter(row => row.available_at < rewindTime).at(-1)!.available_at;
    const rewound = getCrt(page, previous, 'primary', primary.id);
    const startRewind = performance.now();
    await button(replay(page), 'Previous candle').click();
    const prior = await rewound;
    await ready(page, primary.id, manifest.symbols.EURUSD.checkpoints.at(-2));
    const rewind = await sample(startRewind, inspector(page));
    await verifyRange(page, 'EURUSD', prior, previous, primary.id,
      manifest.symbols.EURUSD.checkpoints.filter(m => m.available_at <= previous));
    const advanced = getCrt(page, rewindTime, 'primary', primary.id);
    await button(replay(page), 'Next candle').click();
    expect((await advanced).page.selected).toEqual(primaryValue);
    await moveChart(page, cutoff);
    const peerList = getCrt(page, cutoff, '2');
    await pane(page).selectOption('2');
    await peerList;
    const peer = await selectLatestRange(page, cutoff, 'GBPUSD', '2');
    const peerValue = await verifyRange(page, 'GBPUSD', peer.received, cutoff, peer.id,
      manifest.symbols.GBPUSD.checkpoints) as ReplayCrtReceipt;
    const peerBoundaries = await checkBoundaries(page, 'GBPUSD', '2', peer.id);
    await pane(page).selectOption('primary');
    await stalePane(page, cutoff, primary.id, peer.id);
    const exactCutoff = cutoff - 1;
    const exactProbe = getCrt(page, exactCutoff, '2', peer.id);
    await probe(page, exactCutoff);
    expect((await exactProbe).page.selected).toEqual(peerValue);
    await savePosition(page);
    record('inspector-initial', {browser: await page.evaluate(() => navigator.userAgent), session, cutoff, exactCutoff,
      receipts: {EURUSD: primaryValue, GBPUSD: peerValue}, exact_cutoffs: {EURUSD: primaryBoundaries, GBPUSD: peerBoundaries},
      measurements: {collapsed, list: {...listed, bytes: list.bytes}, select: {...primary.measured, bytes: primary.received.bytes},
        sources, rewind: {...rewind, bytes: prior.bytes}}});
    await button(replay(page), 'Exit replay').click();
    await instrument(page, 'GBPUSD', 'EURUSD');
    const swapped = await startSession(page, 'GBPUSD', 'EURUSD');
    expect(swapped).not.toBe(session);
    await button(replay(page), 'Last candle').click();
    const swappedCutoff = cutoffOf('GBPUSD');
    const swappedList = getCrt(page, swappedCutoff, 'primary');
    await inspector(page).locator('summary').click();
    await swappedList;
    const gbp = await selectLatestRange(page, swappedCutoff, 'GBPUSD', 'primary');
    expect(gbp.received.page.selected).toEqual(peerValue);
    const eurList = getCrt(page, swappedCutoff, '2');
    await pane(page).selectOption('2');
    await eurList;
    const eur = await selectLatestRange(page, swappedCutoff, 'EURUSD', '2');
    expect(eur.received.page.selected).toEqual(primaryValue);
    record('inspector-swapped', {session: swapped, EURUSD: eur.id, GBPUSD: gbp.id});
    await button(replay(page), 'Exit replay').click();
    await instrument(page, 'EURUSD', 'GBPUSD');
  });
});

test('@phase-restart@end CRT retains both selected ranges in a fresh Chromium process', async ({baseURL}) => {
  test.setTimeout(120_000);
  await profileRun(baseURL, async page => {
    const saved = load<Saved>('inspector-initial');
    await openTerminal(page);
    await expect(combo(page, 'Resume saved replay')).toBeVisible();
    const lane = getCrt(page, saved.exactCutoff, '2', saved.receipts.GBPUSD.range_id);
    const started = performance.now();
    await combo(page, 'Resume saved replay').selectOption(saved.session);
    const received = await lane;
    expect(received.page.selected).toEqual(saved.receipts.GBPUSD);
    await expect(pane(page)).toHaveValue('2');
    await expect(replay(page).locator('output')).toContainText(iso(saved.cutoff));
    await ready(page, saved.receipts.GBPUSD.range_id);
    await expect(section(page).locator('time[datetime="' + iso(saved.exactCutoff) + '"]')).toBeVisible();
    await expect(section(page).locator('time[datetime="' + iso(saved.cutoff) + '"]')).toBeVisible();
    const cold = await sample(started, inspector(page));
    await expandSources(page, saved.receipts.GBPUSD);
    const primary = getCrt(page, saved.exactCutoff, 'primary', saved.receipts.EURUSD.range_id);
    await pane(page).selectOption('primary');
    expect((await primary).page.selected).toEqual(saved.receipts.EURUSD);
    await ready(page, saved.receipts.EURUSD.range_id);
    await expandSources(page, saved.receipts.EURUSD);
    record('inspector-restart', {browser: await page.evaluate(() => navigator.userAgent), saved,
      measurements: {cold_resume: {...cold, bytes: received.bytes}}, persisted_profile: true});
  });
});

function legacyResult(page: Page, cutoff: number) {
  return page.waitForResponse(response => {
    const req = response.request();
    if (!req.url().endsWith('/indicators/evaluate') || req.method() !== 'POST') return false;
    const body = req.postDataJSON();
    return body.frozen && body.cutoff === cutoff &&
      body.indicators.some((spec: {id: string; crt?: {market_schedule?: {kind: string}}}) =>
        spec.id === 'candle_range_theory' && spec.crt?.market_schedule?.kind === 'forex_weekly');
  });
}

const legacyEvent = (page: Page, moment: Moment) => button(legacy(page), words(moment.state) + ' · ' + iso(moment.available_at));

test('@phase-legacy@end CRT measures list, selection and rewind in the existing indicator library', async ({baseURL}) => {
  test.setTimeout(120_000);
  await profileRun(baseURL, async page => {
    await configure(page);
    const started = performance.now();
    const session = await startSession(page, 'EURUSD', 'GBPUSD');
    const cutoff = benchmarkCutoff;
    await moveChart(page, cutoff);
    const library = page.locator('details.indicator-library');
    const collapsed = await sample(started, library);
    await library.locator('summary').click();
    await combo(library, 'Evidence timing policy').selectOption('STRICT_CAUSAL');
    await combo(library, 'Add indicator').selectOption('candle_range_theory');
    await button(library, 'Enable indicator').click();
    await combo(library, 'CRT reference timeframe').selectOption('H1');
    const match = legacyResult(page, cutoff);
    const startList = performance.now();
    await combo(library, 'CRT market schedule').selectOption('forex_weekly');
    const listed = await match;
    expect((await payload<{results: unknown[]}>(listed)).results).toHaveLength(1);
    await expect(legacy(page)).toBeVisible();
    const list = await sample(startList, library);
    const history = manifest.symbols.EURUSD.checkpoints;
    const final = history.at(-1)!;
    const startedSelect = performance.now();
    await legacyEvent(page, final).first().click();
    await expect(legacyDetail(page)).toContainText(words(final.reason));
    await expect(legacyDetail(page)).toContainText(iso(manifest.symbols.EURUSD.reference_start));
    const selection = await sample(startedSelect, library);
    const at = legacyResult(page, final.available_at);
    await moveChart(page, final.available_at);
    await at;
    const previous = manifest.symbols.EURUSD.bars.filter(row => row.available_at < final.available_at).at(-1)!.available_at;
    const startRewind = performance.now();
    const earlier = legacyResult(page, previous);
    await button(replay(page), 'Previous candle').click();
    const prior = await earlier;
    expect((await payload<{results: unknown[]}>(prior)).results).toHaveLength(1);
    await expect(legacy(page)).toBeVisible();
    const rewind = await sample(startRewind, library);
    await moveChart(page, cutoff);
    await savePosition(page);
    record('legacy-initial', {session, cutoff, reference: final,
      measurements: {collapsed, list: {...list, bytes: (await listed.body()).length},
        select: {...selection, bytes: 0}, rewind: {...rewind, bytes: (await prior.body()).length}, source_expansion: null}});
  }, true);
});

test('@phase-legacy-restart@end CRT measures cold indicator recomputation and manual selection', async ({baseURL}) => {
  test.setTimeout(120_000);
  await profileRun(baseURL, async page => {
    const saved = load<{session: string; cutoff: number; reference: Moment}>('legacy-initial');
    await openTerminal(page);
    const response = legacyResult(page, saved.cutoff);
    const library = page.locator('details.indicator-library');
    const started = performance.now();
    await combo(page, 'Resume saved replay').selectOption(saved.session);
    const resumed = await response;
    await library.locator('summary').click();
    await expect(legacy(page)).toBeVisible();
    const autoRestored = (await legacyDetail(page).count()) > 0;
    await legacyEvent(page, saved.reference).first().click();
    await expect(legacyDetail(page)).toContainText(words(saved.reference.reason));
    const cold = await sample(started, library);
    record('legacy-restart', {auto_restored_selection: autoRestored,
      measurements: {cold_resume: {...cold, bytes: (await resumed.body()).length}}});
  }, true);
});
