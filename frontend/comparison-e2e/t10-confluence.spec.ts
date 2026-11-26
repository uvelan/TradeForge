import {readFileSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {chromium, expect, test, type Page, type Locator, type Response} from '@playwright/test';
import {confluenceProfile, type ConfluenceCandle, type ConfluenceDirection, type ConfluenceReceipt,
  type ConfluenceStatus} from '../src/replayConfluenceTypes';
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
  expect(receipt).toMatchObject({profile: confluenceProfile, rule_version: 1, authority: 'RESEARCH_ONLY'});
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
    page.on('request', request => {if (isConfluence(new URL(request.url()))) requests.push(request.url());});
    await configure(page);
    const started = performance.now();
    const session = await startSession(page, 'EURUSD', 'GBPUSD');
    await moveChart(page, finalChartCutoff);
    await expect(panel(page)).toHaveJSProperty('open', false);
    const hidden = await measure(started, page);
    expect(hidden.inspector_nodes).toBe(1);
    expect(hidden.source_rows).toBe(0);
    expect(requests).toHaveLength(0);

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
