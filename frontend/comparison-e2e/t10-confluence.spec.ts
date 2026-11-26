import {readFileSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {chromium, expect, test, type Page, type Locator, type Response} from '@playwright/test';
import {confluenceProfile, type ConfluenceCandle, type ConfluenceDirection, type ConfluenceReceipt,
  type ConfluenceStatus, type ConfluenceReceiptPage, type ConfluenceCoverageRecovery} from '../src/replayConfluenceTypes';
import type {ReplaySession} from '../src/types';

type Symbol = 'EURUSD' | 'GBPUSD';
type Bar = Pick<ConfluenceCandle, 'time' | 'available_at' | 'open' | 'high' | 'low' | 'close'>;
type Lane = {bars: Bar[]; partitions: ConfluenceCandle['partitions']; origins: Record<string, ConfluenceCandle['partitions'][number]>};
type Checkpoint = {cutoff: number; verdict: ConfluenceStatus; crt_state: string | null; smt_state: string | null;
  direction: ConfluenceDirection};
type Manifest = {start: number; end: number; symbols: Record<Symbol, Lane>; checkpoints: Checkpoint[]};
type Reading = {receipt: ConfluenceReceipt; bytes: number};
type Sample = {ms: number; page_nodes: number; inspector_nodes: number; source_rows: number; heap_bytes: number | null; bytes: number};
type Processes = {api: number | null; frontend: number | null; browser: number | null; playwright_worker: number};
type Saved = {session: string; chartCutoff: number; receipt: ConfluenceReceipt; processes: Processes};

const manifest: Manifest = JSON.parse(readFileSync(process.env.TRADEFORGE_T10_CONFLUENCE_REFERENCE!, 'utf8'));
const output = process.env.TRADEFORGE_T10_CONFLUENCE_OUTPUT!;
const profile = process.env.TRADEFORGE_T10_CONFLUENCE_PROFILE!;
const mode = process.env.TRADEFORGE_T10_CONFLUENCE_CASE!;
const repetitions = Number(process.env.TRADEFORGE_T10_CONFLUENCE_SAMPLES);
const title = 'Replay CRT and SMT confluence';
const frame = 300;
const iso = (time: number) => new Date(time * 1000).toISOString();
const utc = (time: number) => iso(time).replace('T', ' ').replace('.000Z', ' UTC');
const words = (value: string | null) => value ? value.replaceAll('_', ' ') : 'Unavailable';
const combo = (scope: Page | Locator, name: string) => scope.getByRole('combobox', {name, exact: true});
const button = (scope: Page | Locator, name: string) => scope.getByRole('button', {name, exact: true});
const replay = (page: Page) => page.getByLabel('Historical replay', {exact: true});
const panel = (page: Page) => page.locator('details.replay-confluence-evidence');
const scope = (page: Page) => page.getByRole('region', {name: title, exact: true});
const receiptSelect = (page: Page) => combo(scope(page), 'Confluence receipt');
const selected = (page: Page) => scope(page).getByRole('article', {name: 'Selected confluence receipt'});
const component = (page: Page, name: 'CRT' | 'SMT') => selected(page).getByRole('region', {name: name + ' component'});
const field = (scope: Locator, label: string) => scope.locator('dt').filter({hasText: new RegExp('^' + label + '$')}).locator('..').locator('dd');
const isConfluence = (url: URL) => url.pathname.endsWith('/confluence');
const isSavedList = (url: URL) => url.pathname.endsWith('/confluence/receipts');
const receiptOption = (page: Page, id: string) => receiptSelect(page).locator('option[value="' + id + '"]');
const browserPath = (session: string) => '/research/api/replay/sessions/' + session + '/confluence';
const exactQuery = ({cutoff, profile, receipt_id}: ConfluenceReceipt) =>
  new URLSearchParams({cutoff: String(cutoff), profile, receipt_id});
const chosen = manifest.checkpoints.find(item => item.verdict === 'ALIGNED')!;
const newer = manifest.checkpoints.find(item => item.cutoff > chosen.cutoff && item.verdict !== 'ALIGNED')!;
const finalChartCutoff = Math.max(...manifest.symbols.EURUSD.bars.map(bar => bar.available_at));
const chartCutoffs = manifest.symbols.EURUSD.bars.map(bar => bar.available_at);
const beforeChartCutoff = chartCutoffs.filter(time => time < chosen.cutoff).at(-1)!;
const afterChartCutoff = chartCutoffs.find(time => time >= chosen.cutoff)!;
const rewindBoundary = manifest.checkpoints.find(item => item.cutoff === beforeChartCutoff)!;

function record(name: string, value: object) {
  const revision = spawnSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'}).stdout.trim();
  writeFileSync(join(output, name + '.json'), JSON.stringify({git_revision: revision, case: mode, ...value}, null, 2) + '\n');
}

async function payload<T>(response: Response, status = 200) {
  expect(response.status(), await response.text()).toBe(status);
  return await response.json() as T;
}

function responseAt(page: Page, cutoff: number, id?: string, method = 'GET'): Promise<Reading> {
  return page.waitForResponse(response => {
    const url = new URL(response.url());
    return isConfluence(url) && url.searchParams.get('cutoff') === String(cutoff) &&
      url.searchParams.get('profile') === confluenceProfile && url.searchParams.get('receipt_id') === (id ?? null) &&
      response.request().method() === method;
  }, {timeout: 20_000}).then(async response => ({receipt: await payload<ConfluenceReceipt>(response), bytes: (await response.body()).length}));
}

async function profileRun(baseURL: string | undefined, work: (page: Page, processes: Processes) => Promise<void>) {
  const context = await chromium.launchPersistentContext(profile, {
    baseURL, serviceWorkers: 'block', viewport: {width: 1536, height: 960},
    args: ['--enable-precise-memory-info'],
  });
  try {
    const netstat = process.platform === 'win32' ?
      spawnSync('netstat', ['-ano', '-p', 'tcp'], {encoding: 'utf8'}).stdout ?? '' : '';
    const listener = (port: string | undefined) => {
      const line = netstat.split(/\r?\n/).find(line => line.trim().split(/\s+/)[1] === '127.0.0.1:' + port &&
        line.includes('LISTENING'));
      return line ? Number(line.trim().split(/\s+/).at(-1)) : null;
    };
    const cdp = await context.browser()?.newBrowserCDPSession();
    const processInfo = await cdp?.send('SystemInfo.getProcessInfo');
    await cdp?.detach();
    await work(context.pages()[0] ?? await context.newPage(), {
      api: listener(process.env.TRADEFORGE_E2E_API_PORT), frontend: listener(process.env.TRADEFORGE_E2E_UI_PORT),
      browser: processInfo?.processInfo.find(item => item.type === 'browser')?.id ?? null, playwright_worker: process.pid,
    });
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
  const session = await payload<ReplaySession>(await created, 201);
  for (const [market, symbol] of [[session.market, primary], [session.views['2'], peer]] as const) {
    if (!market || 'error' in market) throw new Error('Frozen confluence pane unavailable for ' + symbol);
    expect(market.partitions).toEqual(manifest.symbols[symbol].partitions);
    expect(market.candles.map(bar => [bar.time, bar.available_at])).toEqual(
      manifest.symbols[symbol].bars.map(bar => [bar.time, bar.available_at]));
  }
  return session.id;
}

async function moveChart(page: Page, cutoff: number) {
  await replay(page).getByLabel('Jump to UTC', {exact: true}).fill(iso(cutoff).slice(0, 16));
  await button(replay(page), 'Seek').click();
  if (cutoff % 60 !== 0) await button(replay(page), 'Next candle').click();
  await expect(replay(page).locator('output')).toContainText(iso(cutoff));
}

async function probe(page: Page, cutoff: number) {
  await scope(page).getByLabel('Confluence as of (UTC)', {exact: true}).fill(iso(cutoff).slice(0, 19));
  await button(scope(page), 'Inspect confluence cutoff').click();
}

async function ready(page: Page, receipt: ConfluenceReceipt) {
  await expect(selected(page)).toHaveAttribute('data-receipt-id', receipt.receipt_id);
  await expect(selected(page)).toHaveAttribute('data-cutoff', String(receipt.cutoff));
}

async function verifyReceipt(page: Page, receipt: ConfluenceReceipt, expected?: Checkpoint) {
  await ready(page, receipt);
  expect(receipt).toMatchObject({profile: confluenceProfile, rule_version: 2, authority: 'RESEARCH_ONLY'});
  expect(receipt.receipt_id).toMatch(/^[a-f0-9]{64}$/);
  if (expected) {
    expect(receipt).toMatchObject({cutoff: expected.cutoff, status: expected.verdict, direction: expected.direction});
    if (expected.crt_state) expect(receipt.components.crt.state).toBe(expected.crt_state);
    if (expected.smt_state) expect(receipt.components.smt.state).toBe(expected.smt_state);
  }
  await expect(selected(page)).toHaveAttribute('data-rule-version', String(receipt.rule_version));
  await expect(field(selected(page), 'Verdict')).toHaveText(words(receipt.status));
  await expect(field(selected(page).locator('dl[aria-label="Confluence verdict"]'), 'Reason')).toHaveText(words(receipt.reason));
  await expect(field(selected(page).locator('dl[aria-label="Confluence verdict"]'), 'Direction')).toHaveText(words(receipt.direction));
  await expect(field(selected(page), 'First available')).toHaveText(
    receipt.first_available_at == null ? 'Unavailable' : utc(receipt.first_available_at));
  for (const [name, value] of [['CRT', receipt.components.crt], ['SMT', receipt.components.smt]] as const) {
    const part = component(page, name);
    await expect(field(part, 'Condition')).toHaveText(words(value.condition));
    await expect(field(part, 'State')).toHaveText(words(value.state));
    await expect(field(part, 'Reason')).toHaveText(words(value.reason));
    await expect(field(part, 'Family ID')).toHaveText(value.family_id ?? 'Unavailable');
    await expect(field(part, 'Revision ID')).toHaveText(value.revision_id ?? 'Unavailable');
    await expect(field(part, 'Revision first available')).toHaveText(value.available_at == null ? 'Unavailable' : utc(value.available_at));
    if (value.coverage_reason) await expect(field(part, 'Coverage')).toContainText(words(value.coverage_reason));
    for (const witness of value.evidence) {
      const lane = manifest.symbols[witness.symbol];
      const candle = lane.bars.find(bar => bar.time === witness.time)!;
      expect(witness).toMatchObject({time: candle.time, available_at: candle.available_at, open: Number(candle.open),
        high: Number(candle.high), low: Number(candle.low), close: Number(candle.close), source: 'DUKASCOPY_SAVED',
        timeframe: 'M5', price_basis: 'BID'});
      expect(witness.available_at).toBeLessThanOrEqual(receipt.cutoff);
      const origin = lane.origins?.[witness.time];
      if (origin) expect(witness.partitions).toContainEqual(origin);
    }
  }
}

async function measure(started: number, page: Page, bytes = 0): Promise<Sample> {
  const ms = Math.round((performance.now() - started) * 10) / 10;
  return {...await panel(page).evaluate(el => {
    const memory = (performance as Performance & {memory?: {usedJSHeapSize: number}}).memory;
    return {page_nodes: document.querySelectorAll('*').length, inspector_nodes: el.querySelectorAll('*').length,
      source_rows: el.querySelectorAll('tbody tr').length, heap_bytes: memory?.usedJSHeapSize ?? null};
  }), ms, bytes};
}

function summary(samples: Sample[]) {
  const ordered = samples.map(sample => sample.ms).sort((a, b) => a - b);
  return {n: samples.length,
    median_ms: (ordered[Math.floor((ordered.length - 1) / 2)] + ordered[Math.floor(ordered.length / 2)]) / 2,
    p95_ms: ordered[Math.ceil(ordered.length * 0.95) - 1], samples};
}

async function checkProof(page: Page, name: 'CRT' | 'SMT', witnesses: ConfluenceCandle[], repeat = 1, keepOpen = false) {
  const part = component(page, name);
  const disclosure = part.getByText(name + ' source candles (' + witnesses.length + ')', {exact: true});
  const ids = part.getByRole('list', {name: name + ' source candle IDs'});
  const candle = witnesses.find(witness => witness.symbol === (name === 'CRT' ? 'EURUSD' : 'GBPUSD'))!;
  expect(candle).toBeTruthy();
  const exact = part.getByRole('region', {name: name + ' exact source candle'});
  const samples: Sample[] = [];
  for (let index = 0; index < repeat; index++) {
    await expect(ids).toHaveCount(0);
    await expect(exact).toHaveCount(0);
    const started = performance.now();
    await disclosure.click();
    await button(ids, candle.source_id).click();
    await expect(exact).toHaveAttribute('data-source-id', candle.source_id);
    samples.push(await measure(started, page));
    if (index + 1 < repeat) await disclosure.click();
  }
  expect(await ids.getByRole('button').allTextContents()).toEqual(witnesses.map(row => row.source_id));
  await expect(button(ids, candle.source_id)).toHaveAttribute('aria-pressed', 'true');
  await expect(exact).toContainText(candle.symbol + ' M5 · BID · ' + candle.feed_id);
  const row = exact.getByRole('table').locator('tbody tr');
  const cells = await row.getByRole('cell').allTextContents();
  expect(cells.slice(0, 8)).toEqual([utc(candle.time), utc(candle.available_at), words(candle.availability_basis),
    String(candle.open), String(candle.high), String(candle.low), String(candle.close), candle.source_id]);
  for (const pin of candle.partitions) {
    await expect(exact.getByText(pin.id, {exact: true})).toBeVisible();
    await expect(exact.getByText(pin.sha256, {exact: true})).toBeVisible();
  }
  for (const role of candle.roles) await expect(row.locator('code[title="' + role.event_id + '"]').first()).toBeVisible();
  if (!keepOpen) await disclosure.click();
  return summary(samples);
}

async function measureListAndSelect(page: Page, requests: string[], chartCutoff: number) {
  const list: Sample[] = [];
  const select: Sample[] = [];
  const toggle = panel(page).locator('summary').first();
  for (let index = 0; index < repetitions; index++) {
    const request = responseAt(page, chartCutoff);
    const listedAt = performance.now();
    await toggle.click();
    const {receipt, bytes} = await request;
    await expect(receiptSelect(page).locator('option[value="current"]')).toContainText(words(receipt.status));
    list.push(await measure(listedAt, page, bytes));
    expect(list.at(-1)!.source_rows).toBe(0);
    const count = requests.length;
    expect(requests.at(-1)).toContain('cutoff=' + chartCutoff);
    const selectedAt = performance.now();
    await receiptSelect(page).selectOption('current');
    await ready(page, receipt);
    select.push(await measure(selectedAt, page));
    expect(select.at(-1)!.source_rows).toBe(0);
    expect(requests).toHaveLength(count);
    if (index + 1 < repetitions) {
      await receiptSelect(page).selectOption('');
      await toggle.click();
      await expect(panel(page)).toHaveJSProperty('open', false);
      await expect(panel(page).locator('section')).toHaveCount(0);
    }
  }
  return {list: summary(list), select: summary(select)};
}

async function staleResponse(page: Page) {
  const heldCutoff = chosen.cutoff - 1;
  const target = (url: URL) => isConfluence(url) && url.searchParams.get('cutoff') === String(heldCutoff);
  let markFetched!: () => void;
  let release!: () => void;
  const fetched = new Promise<void>(resolve => {markFetched = resolve;});
  const held = new Promise<void>(resolve => {release = resolve;});
  await page.route(target, async route => {
    const response = await route.fetch();
    markFetched();
    await held;
    await route.fulfill({response});
  });
  try {
    const stale = responseAt(page, heldCutoff);
    await probe(page, heldCutoff);
    await fetched;
    const actual = responseAt(page, chosen.cutoff);
    await probe(page, chosen.cutoff);
    const {receipt} = await actual;
    await ready(page, receipt);
    release();
    expect((await stale).receipt.status).not.toBe('ALIGNED');
    await expect(selected(page)).toHaveAttribute('data-receipt-id', receipt.receipt_id);
  } finally {
    release();
    await page.unroute(target);
  }
}

test('@phase-initial@end freezes conjunction, witnesses, saved receipt and swapped panes', async ({baseURL}) => {
  test.setTimeout(240_000);
  await profileRun(baseURL, async (page, processes) => {
    const requests: string[] = [];
    const savedListRequests: string[] = [];
    page.on('request', request => {
      const url = new URL(request.url());
      if (isConfluence(url)) requests.push(request.url());
      if (isSavedList(url)) savedListRequests.push(request.url());
    });
    await configure(page);
    const started = performance.now();
    const session = await startSession(page, 'EURUSD', 'GBPUSD');
    await moveChart(page, finalChartCutoff);
    await expect(panel(page)).toHaveJSProperty('open', false);
    const hidden = await measure(started, page);
    expect(hidden.inspector_nodes).toBe(1);
    expect(hidden.source_rows).toBe(0);
    expect(requests).toHaveLength(0);
    expect(savedListRequests).toHaveLength(0);

    const timings = await measureListAndSelect(page, requests, finalChartCutoff);
    const delayed = Object.entries(manifest.symbols).flatMap(([symbol, lane]) => lane.bars
      .filter(bar => bar.available_at > bar.time + frame)
      .map(bar => ({symbol, open: bar.time, nominalClose: bar.time + frame, arrival: bar.available_at})));
    if (mode === 'controlled') expect(delayed.some(item => item.symbol === 'GBPUSD')).toBe(true);
    const deferredBoundaries = new Set(delayed.flatMap(item => [item.nominalClose, item.arrival]));
    const checkpoints = manifest.checkpoints.filter((current, index) => {
      const previous = manifest.checkpoints[index - 1];
      return previous && current.cutoff >= manifest.start + frame && current.cutoff <= finalChartCutoff &&
        (deferredBoundaries.has(current.cutoff) || [current.verdict, current.crt_state, current.smt_state].join() !==
        [previous.verdict, previous.crt_state, previous.smt_state].join());
    });
    const snapshots: ConfluenceReceipt[] = [];
    for (const moment of checkpoints) {
      const reading = responseAt(page, moment.cutoff);
      await probe(page, moment.cutoff);
      const {receipt} = await reading;
      await verifyReceipt(page, receipt, moment);
      for (const bar of delayed.filter(item => item.nominalClose === moment.cutoff)) {
        const missing = {symbol: bar.symbol, reason: 'MISSING_OPEN_CANDLES',
          missing_intervals: [{start: bar.open, end: bar.nominalClose}]};
        expect(receipt.components.smt.coverage_issues).toContainEqual(missing);
        expect(receipt.components.smt.evidence.some(row => row.symbol === bar.symbol && row.time === bar.open)).toBe(false);
        const coverage = field(component(page, 'SMT'), 'Coverage');
        await expect(coverage).toContainText(bar.symbol);
        await expect(coverage).toContainText('[' + utc(bar.open) + ', ' + utc(bar.nominalClose) + ')');
      }
      for (const bar of delayed.filter(item => item.arrival === moment.cutoff)) {
        expect(receipt.components.smt.coverage_issues?.some(issue => issue.symbol === bar.symbol &&
          issue.missing_intervals.some(interval => interval.start <= bar.open && bar.open < interval.end))).toBe(false);
      }
      snapshots.push(receipt);
    }
    await staleResponse(page);
    const atNewer = responseAt(page, newer.cutoff);
    await probe(page, newer.cutoff);
    await ready(page, (await atNewer).receipt);
    const atAligned = responseAt(page, chosen.cutoff);
    await probe(page, chosen.cutoff);
    const {receipt: aligned} = await atAligned;
    await verifyReceipt(page, aligned, chosen);
    expect(aligned.first_available_at).toBe(chosen.cutoff);
    expect(aligned.components.crt.condition).toBe('SATISFIED');
    expect(aligned.components.smt.condition).toBe('SATISFIED');
    expect(aligned.components.smt.evidence.map(candle => candle.symbol)).toContain('GBPUSD');
    const beforeDrill = requests.length;
    const drillCrt = await checkProof(page, 'CRT', aligned.components.crt.evidence, repetitions);
    const drillSmt = await checkProof(page, 'SMT', aligned.components.smt.evidence, repetitions);
    expect(requests).toHaveLength(beforeDrill);

    const exactPath = '/research/api/replay/sessions/' + session + '/confluence?' + new URLSearchParams({
      cutoff: String(aligned.cutoff), profile: aligned.profile, receipt_id: aligned.receipt_id,
    });
    expect((await page.request.get(exactPath)).ok()).toBe(false);
    const pin = responseAt(page, chosen.cutoff, aligned.receipt_id, 'POST');
    await button(selected(page), 'Save exact receipt').click();
    expect((await pin).receipt).toEqual(aligned);
    await expect(scope(page).getByText('Exact receipt saved.', {exact: true})).toBeVisible();

    // The same exact receipt remains visible when the chart settles on the inspected cutoff.
    const followChart = afterChartCutoff === chosen.cutoff ? undefined : responseAt(page, afterChartCutoff);
    await moveChart(page, afterChartCutoff);
    await ready(page, followChart ? (await followChart).receipt : aligned);
    const rewind: Sample[] = [];
    for (let index = 0; index < repetitions; index++) {
      const rewound = responseAt(page, beforeChartCutoff);
      const rewindAt = performance.now();
      await button(replay(page), 'Previous candle').click();
      const {receipt, bytes} = await rewound;
      await ready(page, receipt);
      rewind.push(await measure(rewindAt, page, bytes));
      expect(receipt.status).toBe(rewindBoundary.verdict);
      await expect(receiptSelect(page).locator('option[value="' + aligned.receipt_id + '"]')).toHaveCount(0);
      const advanced = responseAt(page, afterChartCutoff);
      await button(replay(page), 'Next candle').click();
      await ready(page, (await advanced).receipt);
    }

    const latest = responseAt(page, finalChartCutoff);
    await moveChart(page, finalChartCutoff);
    const latestReceipt = (await latest).receipt;
    await ready(page, latestReceipt);
    expect(latestReceipt.receipt_id).not.toBe(aligned.receipt_id);
    expect(latestReceipt.cutoff).toBeGreaterThan(aligned.cutoff);
    const reopen: Sample[] = [];
    for (let index = 0; index < repetitions; index++) {
      const exact = responseAt(page, chosen.cutoff, aligned.receipt_id);
      const reopenAt = performance.now();
      await receiptSelect(page).selectOption(aligned.receipt_id);
      const {receipt, bytes} = await exact;
      await ready(page, receipt);
      reopen.push(await measure(reopenAt, page, bytes));
      expect(receipt).toEqual(aligned);
      if (index + 1 < repetitions) {
        await receiptSelect(page).selectOption('current');
        await ready(page, latestReceipt);
      }
    }
    const beforePin = responseAt(page, beforeChartCutoff);
    await moveChart(page, beforeChartCutoff);
    await beforePin;
    await expect(selected(page)).toHaveCount(0);
    await expect(scope(page).getByText('The saved receipt is unavailable at this chart cutoff.', {exact: true})).toBeVisible();
    const arrived = responseAt(page, aligned.cutoff, aligned.receipt_id);
    await moveChart(page, finalChartCutoff);
    expect((await arrived).receipt).toEqual(aligned);
    await ready(page, aligned);
    await button(replay(page), 'Save replay position').click();
    await expect(replay(page).getByText('Replay position saved', {exact: true})).toBeVisible();
    const saved = {session, chartCutoff: finalChartCutoff, receipt: aligned, processes};
    record('initial', {...saved, browser: await page.evaluate(() => navigator.userAgent), snapshots,
      measurements: {hidden, ...timings, rewind: summary(rewind), reopen: summary(reopen), drill_crt: drillCrt, drill_smt: drillSmt}});

    await button(replay(page), 'Exit replay').click();
    await instrument(page, 'GBPUSD', 'EURUSD');
    const swapped = await startSession(page, 'GBPUSD', 'EURUSD');
    expect(swapped).not.toBe(session);
    const swappedCutoff = Math.max(...manifest.symbols.GBPUSD.bars.map(bar => bar.available_at));
    await moveChart(page, swappedCutoff);
    const swappedList = responseAt(page, swappedCutoff);
    await panel(page).locator('summary').first().click();
    await swappedList;
    const swappedAligned = responseAt(page, chosen.cutoff);
    await probe(page, chosen.cutoff);
    const actual = (await swappedAligned).receipt;
    await verifyReceipt(page, actual, chosen);
    expect(actual.components).toEqual(aligned.components);
    expect(actual.first_available_at).toBe(aligned.first_available_at);
    record('swapped', {session: swapped, prior_session: session, receipt: actual});
    await button(replay(page), 'Exit replay').click();
    await instrument(page, 'EURUSD', 'GBPUSD');
  });
});

test('@phase-restart@end reopens the exact older receipt after API and browser restart', async ({baseURL}) => {
  test.setTimeout(120_000);
  const previous = JSON.parse(readFileSync(join(output, 'initial.json'), 'utf8')) as Saved;
  await profileRun(baseURL, async (page, processes) => {
    for (const kind of ['api', 'frontend', 'browser', 'playwright_worker'] as const) {
      if (processes[kind] && previous.processes[kind]) expect(processes[kind]).not.toBe(previous.processes[kind]);
    }
    await openTerminal(page);
    const persisted = responseAt(page, previous.receipt.cutoff, previous.receipt.receipt_id);
    const started = performance.now();
    await combo(page, 'Resume saved replay').selectOption(previous.session);
    const {receipt, bytes} = await persisted;
    await ready(page, receipt);
    const cold = await measure(started, page, bytes);
    expect(receipt).toEqual(previous.receipt);
    await expect(receiptSelect(page)).toHaveValue(receipt.receipt_id);
    await expect(replay(page).locator('output')).toContainText(iso(previous.chartCutoff));
    await verifyReceipt(page, receipt, chosen);
    await checkProof(page, 'CRT', receipt.components.crt.evidence, 1, true);
    await checkProof(page, 'SMT', receipt.components.smt.evidence, 1, true);
    await selected(page).locator('dl[aria-label="Confluence verdict"]').screenshot({path: join(output, 'reopened-verdict.png')});
    await component(page, 'CRT').getByRole('region', {name: 'CRT exact source candle'})
      .screenshot({path: join(output, 'reopened-crt-candle.png')});
    await component(page, 'SMT').getByRole('region', {name: 'SMT exact source candle'})
      .screenshot({path: join(output, 'reopened-smt-candle.png')});
    record('restart', {processes, receipt, measurement: summary([cold])});
  });
});

test('@phase-discovery@end finds server-saved receipts in a fresh browser profile', async ({baseURL, browser}) => {
  test.setTimeout(120_000);
  await profileRun(baseURL, async page => {
    await configure(page);
    const session = await startSession(page, 'EURUSD', 'GBPUSD');
    await moveChart(page, finalChartCutoff);
    if (!(await panel(page).evaluate(details => (details as HTMLDetailsElement).open))) {
      await panel(page).locator('summary').first().click();
    }
    const inspected = responseAt(page, chosen.cutoff);
    await probe(page, chosen.cutoff);
    const {receipt} = await inspected;
    const saved = responseAt(page, receipt.cutoff, receipt.receipt_id, 'POST');
    await button(selected(page), 'Save exact receipt').click();
    expect((await saved).receipt).toEqual(receipt);
    await button(replay(page), 'Save replay position').click();
    await expect(replay(page).getByText('Replay position saved', {exact: true})).toBeVisible();

    const fresh = await browser.newContext({baseURL, serviceWorkers: 'block'});
    try {
      const reopened = await fresh.newPage();
      await configure(reopened);
      await combo(reopened, 'Resume saved replay').selectOption(session);
      await expect(replay(reopened).locator('output')).toContainText(iso(finalChartCutoff));
      expect(await reopened.evaluate(() => Object.keys(localStorage).filter(key => key.includes('confluence-receipts'))))
        .toEqual([]);
      await panel(reopened).locator('summary').first().click();
      await expect(receiptOption(reopened, receipt.receipt_id)).toHaveCount(1);
      const exact = responseAt(reopened, receipt.cutoff, receipt.receipt_id);
      await receiptSelect(reopened).selectOption(receipt.receipt_id);
      expect((await exact).receipt).toEqual(receipt);
      await ready(reopened, receipt);
      record('discovery-fresh-browser', {session, receipt_id: receipt.receipt_id, cutoff: receipt.cutoff,
        browser: await reopened.evaluate(() => navigator.userAgent), immutable_rows: persistedRows(session)});
    } finally {
      await fresh.close();
    }
  });
});

async function inspectAt(page: Page, cutoff: number) {
  const inspected = responseAt(page, cutoff);
  await probe(page, cutoff);
  const {receipt} = await inspected;
  await ready(page, receipt);
  return receipt;
}

async function resumeInTab(page: Page, session: string, cutoff: number) {
  await openTerminal(page);
  await combo(page, 'Market data source').selectOption('DUKASCOPY_SAVED');
  await combo(page, 'Resume saved replay').selectOption(session);
  await expect(replay(page).locator('output')).toContainText(iso(cutoff));
}

function gate() {
  let open!: () => void;
  const wait = new Promise<void>(resolve => {open = resolve;});
  return {open, wait};
}

async function holdPost(page: Page, receipt: ConfluenceReceipt) {
  const predicate = (url: URL) => isConfluence(url) && url.searchParams.get('receipt_id') === receipt.receipt_id;
  const held = gate();
  const arrived = gate();
  await page.route(predicate, async route => {
    if (route.request().method() === 'POST') {
      arrived.open();
      await held.wait;
    }
    await route.continue();
  });
  return {release: held.open, arrived: arrived.wait, dispose: async () => {
    held.open();
    await page.unroute(predicate);
  }};
}

async function list(page: Page, session: string, cutoff: number) {
  const result = await page.request.get(browserPath(session) + '/receipts', {params: {cutoff}});
  expect(result.status(), await result.text()).toBe(200);
  return await result.json() as ConfluenceReceiptPage;
}

function persistedRows(session: string): {id: string; payload_sha256: string}[] {
  const program = [
    'import hashlib, json, sqlite3, sys',
    'from research_backend.app.config import DATABASE',
    'db = sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True)',
    'rows = db.execute("SELECT id,CAST(payload AS BLOB) FROM replay_confluence_receipts WHERE session_id=? ORDER BY id", (sys.argv[1],))',
    'print(json.dumps([dict(id=key,payload_sha256=hashlib.sha256(data).hexdigest()) for key,data in rows]))',
  ].join('\n');
  const result = spawnSync(process.env.TRADEFORGE_E2E_PYTHON!, ['-c', program, session], {encoding: 'utf8', cwd: '..'});
  expect(result.status, result.stderr).toBe(0);
  return JSON.parse(result.stdout);
}

test('@phase-discovery@end preserves both tabs’ saves in either completion order and isolates replay sessions', async ({baseURL}) => {
  test.setTimeout(120_000);
  await profileRun(baseURL, async page => {
    await configure(page);
    const session = await startSession(page, 'EURUSD', 'GBPUSD');
    await moveChart(page, finalChartCutoff);
    await button(replay(page), 'Save replay position').click();
    await expect(replay(page).getByText('Replay position saved', {exact: true})).toBeVisible();
    const peer = await page.context().newPage();
    await resumeInTab(peer, session, finalChartCutoff);
    for (const tab of [page, peer]) {
      if (!(await panel(tab).evaluate(details => (details as HTMLDetailsElement).open))) {
        await panel(tab).locator('summary').first().click();
      }
    }
    const proof: {order: string[]; ids: string[]}[] = [];
    for (const [index, order] of [[0, [0, 1]], [1, [1, 0]]] as const) {
      const tabs = [page, peer];
      const receipts = await Promise.all(tabs.map((tab, offset) => inspectAt(tab, chosen.cutoff + 1 + index * 2 + offset)));
      const holds = await Promise.all(tabs.map((tab, offset) => holdPost(tab, receipts[offset])));
      try {
        const responses = tabs.map((tab, offset) =>
          responseAt(tab, receipts[offset].cutoff, receipts[offset].receipt_id, 'POST'));
        await Promise.all(tabs.map(tab => button(selected(tab), 'Save exact receipt').click()));
        await Promise.all(holds.map(hold => hold.arrived));
        for (const offset of order) {
          holds[offset].release();
          expect((await responses[offset]).receipt).toEqual(receipts[offset]);
          await expect(scope(tabs[offset]).getByText('Exact receipt saved.', {exact: true})).toBeVisible();
        }
        for (const tab of tabs) {
          for (const receipt of receipts) await expect(receiptOption(tab, receipt.receipt_id)).toHaveCount(1);
        }
        proof.push({order: order.map(offset => receipts[offset].receipt_id), ids: receipts.map(item => item.receipt_id)});
      } finally {
        await Promise.all(holds.map(hold => hold.dispose()));
      }
    }
    const beforeRetry = persistedRows(session);
    const saved = await list(page, session, finalChartCutoff);
    for (const summary of saved.receipts) {
      const query = new URLSearchParams({cutoff: String(summary.cutoff), profile: summary.profile, receipt_id: summary.receipt_id});
      const get = await page.request.get(browserPath(session) + '?' + query);
      const retry = await page.request.post(browserPath(session) + '?' + query, {data: {}});
      expect(retry.status()).toBe(200);
      expect(await retry.json()).toEqual(await get.json());
    }
    expect(persistedRows(session)).toEqual(beforeRetry);
    for (const row of beforeRetry) expect(row.payload_sha256).toBe(row.id);

    await button(replay(page), 'Exit replay').click();
    await instrument(page, 'GBPUSD', 'EURUSD');
    const foreign = await startSession(page, 'GBPUSD', 'EURUSD');
    expect(foreign).not.toBe(session);
    await moveChart(page, finalChartCutoff);
    await panel(page).locator('summary').first().click();
    expect((await list(page, foreign, finalChartCutoff)).receipts).toEqual([]);
    for (const summary of saved.receipts) await expect(receiptOption(page, summary.receipt_id)).toHaveCount(0);
    const example = saved.receipts[0];
    const foreignQuery = new URLSearchParams({cutoff: String(example.cutoff), profile: example.profile,
      receipt_id: example.receipt_id});
    const foreignPath = browserPath(foreign) + '?' + foreignQuery;
    expect((await page.request.get(foreignPath)).status()).toBe(409);
    expect((await page.request.post(foreignPath, {data: {}})).status()).toBe(409);
    expect(persistedRows(foreign)).toEqual([]);
    record('discovery-two-tabs', {session, foreign, proof, immutable_rows: beforeRetry});
  });
});

test('@phase-discovery@end refreshes after a save completes across collapse and ignores a delayed older list', async ({baseURL}) => {
  test.setTimeout(120_000);
  await profileRun(baseURL, async page => {
    await configure(page);
    const session = await startSession(page, 'EURUSD', 'GBPUSD');
    await moveChart(page, finalChartCutoff);
    const toggle = panel(page).locator('summary').first();
    if (!(await panel(page).evaluate(details => (details as HTMLDetailsElement).open))) await toggle.click();
    const receipt = await inspectAt(page, chosen.cutoff - 1);
    const hold = await holdPost(page, receipt);
    const deliveredList = gate();
    const staleFetched = gate();
    const staleReturned = gate();
    const target = (url: URL) => isSavedList(url) && url.searchParams.get('cutoff') === String(finalChartCutoff);
    let holdNext = true;
    try {
      const saved = responseAt(page, receipt.cutoff, receipt.receipt_id, 'POST');
      await button(selected(page), 'Save exact receipt').click();
      await hold.arrived;
      await toggle.click();
      await expect(scope(page)).toHaveCount(0);
      await page.route(target, async route => {
        if (!holdNext) {
          await route.continue();
          return;
        }
        holdNext = false;
        const response = await route.fetch();
        const before = await response.json() as ConfluenceReceiptPage;
        expect(before.receipts.some(item => item.receipt_id === receipt.receipt_id)).toBe(false);
        staleFetched.open();
        await deliveredList.wait;
        await route.fulfill({response});
        staleReturned.open();
      });
      await toggle.click();
      await staleFetched.wait;
      hold.release();
      expect((await saved).receipt).toEqual(receipt);
      await expect(receiptOption(page, receipt.receipt_id)).toHaveCount(1);
      deliveredList.open();
      await staleReturned.wait;
      await expect(receiptOption(page, receipt.receipt_id)).toHaveCount(1);

      const exact = responseAt(page, receipt.cutoff, receipt.receipt_id);
      await receiptSelect(page).selectOption(receipt.receipt_id);
      expect((await exact).receipt).toEqual(receipt);
      await ready(page, receipt);
      await moveChart(page, beforeChartCutoff);
      await expect(receiptOption(page, receipt.receipt_id)).toHaveCount(0);
      expect((await list(page, session, beforeChartCutoff)).receipts.map(item => item.receipt_id))
        .not.toContain(receipt.receipt_id);
      await expect(selected(page)).toHaveCount(0);
      await moveChart(page, finalChartCutoff);
      await ready(page, receipt);
      record('discovery-delayed-save', {session, receipt_id: receipt.receipt_id, before_chart_cutoff: beforeChartCutoff,
        final_chart_cutoff: finalChartCutoff, immutable_rows: persistedRows(session)});
    } finally {
      deliveredList.open();
      await hold.dispose();
      await page.unroute(target);
    }
  });
});

test('@phase-quiet-recovery@end shows the exact recovery witness and the old receipt limitation', async ({baseURL, browser}) => {
  const fixture = process.env.TRADEFORGE_T10_CONFLUENCE_QUIET_WRITER;
  test.skip(!fixture, 'Set TRADEFORGE_T10_CONFLUENCE_QUIET_WRITER to the old-writer fixture output');
  test.setTimeout(120_000);
  const plan: {session: string; saved: {summary: ConfluenceReceipt; original_payload_utf8: string}[];
    witnesses: ConfluenceCoverageRecovery[]} = JSON.parse(readFileSync(fixture!, 'utf8'));
  const witness = plan.witnesses[0];
  const legacy = plan.saved.find(item => item.summary.cutoff === witness.available_at)!;
  const context = await browser.newContext({baseURL, serviceWorkers: 'block'});
  try {
    const page = await context.newPage();
    await resumeInTab(page, plan.session, witness.available_at);
    expect(await page.evaluate(() => Object.keys(localStorage).filter(key => key.includes('confluence-receipts')))).toEqual([]);
    await panel(page).locator('summary').first().click();
    const before = await inspectAt(page, witness.available_at - 1);
    expect(before.status).toBe('INCOMPLETE');
    expect(before.first_available_at).toBeNull();
    expect(before.coverage_recoveries).toEqual([]);
    await expect(page.getByText(/Coverage recovery witnesses/)).toHaveCount(0);
    const current = await inspectAt(page, witness.available_at);
    expect(current.status).toBe('ALIGNED');
    expect(current.first_available_at).toBe(witness.available_at);
    expect(current.coverage_recoveries).toEqual(plan.witnesses);
    await expect(selected(page).getByText('Coverage recovery witnesses (1)', {exact: true})).toBeVisible();
    const save = responseAt(page, current.cutoff, current.receipt_id, 'POST');
    await button(selected(page), 'Save exact receipt').click();
    expect((await save).receipt).toEqual(current);

    const exactLegacy = responseAt(page, legacy.summary.cutoff, legacy.summary.receipt_id);
    await receiptSelect(page).selectOption(legacy.summary.receipt_id);
    const {receipt: oldReceipt} = await exactLegacy;
    expect(oldReceipt).toEqual({...JSON.parse(legacy.original_payload_utf8), receipt_id: legacy.summary.receipt_id});
    await ready(page, oldReceipt);
    await expect(selected(page).getByText('Coverage recovery evidence is unavailable in this version 1 receipt.',
      {exact: true})).toBeVisible();

    const exactCurrent = responseAt(page, current.cutoff, current.receipt_id);
    await receiptSelect(page).selectOption(current.receipt_id);
    expect((await exactCurrent).receipt).toEqual(current);
    await ready(page, current);
    await selected(page).getByText('Coverage recovery witnesses (1)', {exact: true}).click();
    const proof = selected(page).getByRole('list', {name: 'Coverage recovery witnesses'});
    await expect(field(proof, 'Symbol')).toHaveText(witness.symbol);
    await expect(field(proof, 'Candle open')).toHaveText(utc(witness.time));
    await expect(field(proof, 'Nominal close')).toHaveText(utc(witness.nominal_close));
    await expect(field(proof, 'Available')).toHaveText(utc(witness.available_at));
    await expect(field(proof, 'Availability basis')).toHaveText(words(witness.availability_basis));
    await expect(field(proof, 'Source ID')).toHaveText(witness.source_id);
    for (const pin of witness.partitions) {
      await expect(proof.getByText(pin.id, {exact: true})).toBeVisible();
      await expect(proof.getByText(pin.sha256, {exact: true})).toBeVisible();
    }
    await proof.screenshot({path: join(output, 'quiet-coverage-recovery.png')});
    const retry = await page.request.post(browserPath(plan.session) + '?' + exactQuery(oldReceipt), {data: {}});
    expect(await retry.json()).toEqual(oldReceipt);
    const rows = persistedRows(plan.session);
    expect(rows).toHaveLength(3);
    for (const row of rows) expect(row.payload_sha256).toBe(row.id);
    record('quiet-coverage-recovery', {session: plan.session, before, current, legacy: oldReceipt, immutable_rows: rows});
  } finally {
    await context.close();
  }
});

test('@phase-discovery@end loads older pages and restores an older selected receipt', async ({baseURL}) => {
  test.setTimeout(120_000);
  await profileRun(baseURL, async page => {
    await configure(page);
    const session = await startSession(page, 'EURUSD', 'GBPUSD');
    await moveChart(page, finalChartCutoff);
    const toggle = panel(page).locator('summary').first();
    if (await panel(page).evaluate(details => (details as HTMLDetailsElement).open)) await toggle.click();
    const path = browserPath(session);
    const inserted: ConfluenceReceipt[] = [];
    for (let index = 0; index < 25; index++) {
      const result = await page.request.get(path, {params: {cutoff: chosen.cutoff - 100 + index}});
      expect(result.status(), await result.text()).toBe(200);
      const receipt = await result.json() as ConfluenceReceipt;
      const saved = await page.request.post(path + '?' + exactQuery(receipt), {data: {}});
      expect(saved.status(), await saved.text()).toBe(200);
      inserted.push(receipt);
    }
    const earliest = inserted[0];
    await toggle.click();
    const loadOlder = button(scope(page), 'Load older saved receipts');
    const firstPage = await list(page, session, finalChartCutoff);
    expect(firstPage.receipts).toHaveLength(24);
    expect(firstPage.receipts.some(item => item.receipt_id === earliest.receipt_id)).toBe(false);
    const newest = firstPage.receipts[0].receipt_id;
    await expect(loadOlder).toBeVisible();
    await expect(receiptOption(page, earliest.receipt_id)).toHaveCount(0);
    await loadOlder.click();
    await expect(receiptOption(page, earliest.receipt_id)).toHaveCount(1);
    const exact = responseAt(page, earliest.cutoff, earliest.receipt_id);
    await receiptSelect(page).selectOption(earliest.receipt_id);
    expect((await exact).receipt).toEqual(earliest);
    await ready(page, earliest);
    await moveChart(page, beforeChartCutoff);
    await expect(receiptOption(page, earliest.receipt_id)).toHaveCount(0);
    await moveChart(page, finalChartCutoff);
    await expect(receiptOption(page, newest)).toHaveCount(1);
    await ready(page, earliest);
    await toggle.click();
    await expect(scope(page)).toHaveCount(0);
    const restored = responseAt(page, earliest.cutoff, earliest.receipt_id);
    await toggle.click();
    expect((await restored).receipt).toEqual(earliest);
    await expect(receiptSelect(page)).toHaveValue(earliest.receipt_id);
    await ready(page, earliest);
    record('discovery-pagination', {session, first_page: firstPage, selected: earliest.receipt_id});
  });
});


test('@phase-quiet-reopen@end restores the saved coverage witness after restarting the API and browser', async ({baseURL, browser}) => {
  const original = JSON.parse(readFileSync(join(output, 'quiet-coverage-recovery.json'), 'utf8')) as {
    session: string; current: ConfluenceReceipt; legacy: ConfluenceReceipt; immutable_rows: {id: string; payload_sha256: string}[];
  };
  const context = await browser.newContext({baseURL, serviceWorkers: 'block'});
  try {
    const page = await context.newPage();
    await resumeInTab(page, original.session, original.current.cutoff);
    await panel(page).locator('summary').first().click();
    for (const receipt of [original.legacy, original.current]) {
      await expect(receiptOption(page, receipt.receipt_id)).toHaveCount(1);
      const response = responseAt(page, receipt.cutoff, receipt.receipt_id);
      await receiptSelect(page).selectOption(receipt.receipt_id);
      expect((await response).receipt).toEqual(receipt);
      await ready(page, receipt);
    }
    await selected(page).getByText('Coverage recovery witnesses (1)', {exact: true}).click();
    const proof = selected(page).getByRole('list', {name: 'Coverage recovery witnesses'});
    const witness = original.current.coverage_recoveries![0];
    await expect(field(proof, 'Source ID')).toHaveText(witness.source_id);
    await expect(field(proof, 'Available')).toHaveText(utc(witness.available_at));
    for (const partition of witness.partitions) {
      await expect(proof.getByText(partition.id, {exact: true})).toBeVisible();
      await expect(proof.getByText(partition.sha256, {exact: true})).toBeVisible();
    }
    expect(persistedRows(original.session)).toEqual(original.immutable_rows);
    record('quiet-reopened', {session: original.session, cutoff: original.current.cutoff,
      original_id: original.legacy.receipt_id, recovered_id: original.current.receipt_id,
      witness, immutable_rows: original.immutable_rows});
  } finally {
    await context.close();
  }
});
