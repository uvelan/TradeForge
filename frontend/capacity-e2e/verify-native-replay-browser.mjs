import assert from 'node:assert/strict';
import {execFileSync, spawn} from 'node:child_process';
import {once} from 'node:events';
import {closeSync, mkdirSync, mkdtempSync, openSync, readFileSync, writeFileSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {chromium, expect} from '@playwright/test';
import {drawingJourney, verifyReopenedDrawings} from './native-drawing-journey.mjs';
import {installProbe} from '../e2e/workspace-probe.ts';

import {cutoffJourney, installCutoffClock, createBoundedSession, checkServerSessions} from './native-drawing-cutoff.mjs';

const {values: args} = parseArgs({options: {
  cutoff: {type: 'boolean'},
  drawings: {type: 'boolean'},
  'forex-decisions': {type:'boolean'},
  'decision-boundaries': {type:'boolean'},
  output: {type: 'string'},
  workspace: {type: 'string'},
  bi5: {type: 'string'},
  csv: {type: 'string'},
  'api-port': {type: 'string', default: '8793'},
  'ui-port': {type: 'string', default: '4793'},
}});
if(args.cutoff&&!args.drawings)throw Error('--cutoff requires --drawings');
if (!args.output || !args.workspace || (!args['decision-boundaries'] && (!args.csv || (!args.drawings && !args.bi5)))) {
  throw Error('Supply --output and --workspace, plus source paths for the selected replay journey.');
}
for (const port of [args['api-port'], args['ui-port']]) {
  assert(/^\d{1,5}$/.test(port) && Number(port) >= 1 && Number(port) <= 65535);
}
const root = fileURLToPath(new URL('../../', import.meta.url));
const frontend = join(root, 'frontend');
const python = process.env.TRADEFORGE_E2E_PYTHON ?? join(root, '.venv', 'Scripts', 'python.exe');
const harness = process.env.TRADEFORGE_NATIVE_FIXTURE_SCRIPT ?? join(root, 'scripts', 'verify_native_replay.py');
const apiOrigin = 'http://127.0.0.1:' + args['api-port'] + '/research';
const uiOrigin = 'http://127.0.0.1:' + args['ui-port'];
mkdirSync(resolve(args.workspace), {recursive: true});
const storage = mkdtempSync(join(resolve(args.workspace), 'native-replay-'));
const env = {
  ...process.env,
  TRADEFORGE_STORAGE: storage,
  TRADEFORGE_DATA_DIR: join(storage, 'research'),
  TRADEFORGE_E2E_API_PORT: args['api-port'],
  TRADEFORGE_E2E_UI_PORT: args['ui-port'],
};
const iso = epoch => new Date(epoch * 1000).toISOString();
const errors = [];
const uiActions = [];

function start(executable, parameters, cwd, log) {
  const logfile = openSync(join(storage, log), 'a');
  try {
    return spawn(executable, parameters, {cwd, env, windowsHide: true, stdio: ['ignore', logfile, logfile]});
  } finally {
    closeSync(logfile);
  }
}
function startApi() {
  return start(python, ['-m', 'uvicorn', 'tradeforge.api:app', '--host', '127.0.0.1',
    '--port', args['api-port']], root, 'api.log');
}
function startVite() {
  return start(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--strictPort',
    '--config', 'e2e/vite.config.ts', '--port', args['ui-port']], frontend, 'vite.log');
}
async function stop(child) {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
  const exit = once(child, 'exit');
  if (process.platform === 'win32') {
    // The Windows virtualenv launcher can delegate to another Python process.
    execFileSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {stdio: 'ignore', windowsHide: true});
  } else {
    child.kill('SIGTERM');
  }
  await exit;
}
async function awaitReady(origin, path, child) {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    assert.equal(child.exitCode, null, 'Private server exited during startup');
    const response = await fetch(origin + path, {signal: AbortSignal.timeout(1500)}).catch(() => undefined);
    if (response?.ok) return;
    await delay(100);
  }
  throw Error('Private server unavailable at ' + origin + '. Logs in ' + storage);
}
function fixture(mode, more = []) {
  const output = join(storage, mode + '.json');
  execFileSync(python, [harness, mode, '--api', apiOrigin, '--output', output, ...more], {
    cwd: root, env, windowsHide: true, stdio: 'inherit',
  });
  return {output, details: JSON.parse(readFileSync(output, 'utf8'))};
}
async function request(path) {
  const response = await fetch(apiOrigin + path);
  if (!response.ok) throw Error(await response.text());
  return response.json();
}
async function tabTo(page, locator) {
  await expect(locator).toBeVisible();
  for (let count = 0; count < 220; count++) {
    if (await locator.evaluate(element => element === document.activeElement)) return;
    await page.keyboard.press('Tab');
  }
  throw Error('Could not reach expected control with Tab: ' + locator);
}
async function activate(page, name) {
  await tabTo(page, page.getByRole('button', {name, exact: true}));
  await page.keyboard.press('Enter');
}
async function choose(page, label, value) {
  const control = page.getByRole('combobox', {name: new RegExp('^' + label)});
  // Keep the picker open while traversing options. Commit the explicit choice with Enter.
  await tabTo(page, control);
  const index = await control.locator('option').evaluateAll((options, value) => {
    return options.filter(option => !option.disabled).findIndex(option => option.value === value);
  }, value);
  assert(index >= 0, 'Expected selectable option in ' + label);
  await page.keyboard.press('Space');
  await page.keyboard.press('Home');
  for (let count = 0; count < index; count++) await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
}
async function configure(page, expected, reopen = false) {
  const {request: scope, panes} = expected;
  const settings = {
    source: scope.source,
    timeframe: scope.timeframe,
    'forex-symbol': scope.symbol,
    'chart-views': reopen ? '1' : String(Object.keys(panes).length),
    'chart-comparison': 'none',
    'link-instrument': 'off',
    'link-timeframe': 'off',
    'link-replay': reopen ? 'off' : 'on',
  };
  for (const [view, pane] of Object.entries(panes)) {
    if (view === 'primary') continue;
    settings['view-' + view + '-symbol'] = reopen ? scope.symbol : pane.symbol;
    settings[view === '2' ? 'secondary-timeframe' : 'view-' + view + '-frame'] = reopen ? 'M5' : pane.timeframe;
  }
  await installProbe(page);
  await page.addInitScript(settings => {
    for (const [key, value] of Object.entries(settings)) {
      localStorage.setItem('tradeforge.workspace.' + key, value);
    }
  }, settings);
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(uiOrigin);
  await activate(page, 'Market Terminal');
  await expect(page.getByLabel('Market data source')).toHaveValue(scope.source);
  await page.evaluate(panes => {window.nativeReplayOracle = panes;}, panes);
}
async function freeze(page, scope, sessionId) {
  for (const [dateLabel, timeLabel, epoch] of [
    ['From UTC', 'From time (UTC)', scope.start],
    ['Until UTC (exclusive)', 'Until time (UTC)', scope.end],
  ]) {
    await page.getByLabel(dateLabel, {exact: true}).fill(iso(epoch).slice(0, 10));
    await page.getByLabel(timeLabel, {exact: true}).fill(iso(epoch).slice(11, 16));
  }
  await page.getByRole('button', {name: 'Load saved range', exact: true}).click();
  const [response] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith('/api/replay/sessions') &&
      response.request().method() === 'POST'),
    page.getByRole('button', {name: 'Freeze range for replay', exact: true}).click(),
  ]);
  if (response.status() !== 201) throw Error(await response.text());
  const result = await response.json();
  assert.equal(result.id, sessionId);
  assert.equal(result.request.window_bars, scope.window_bars);
}
async function resume(page, sessionId) {
  const [response] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith('/api/replay/sessions/' + sessionId)),
    choose(page, 'Resume saved replay', sessionId),
  ]);
  assert.equal(response.status(), 200);
  await expect(page.getByLabel('Replay timeline')).toBeVisible();
}
async function verifyBars(page, panes, cutoff) {
  const expected = Object.fromEntries(Object.entries(panes).map(([view, pane]) => [
    view, {count: pane.candles.filter(bar => bar.available_at <= cutoff).length, mismatch: -1},
  ]));
  await expect.poll(() => page.evaluate(cutoff => {
    return Object.fromEntries(Object.entries(window.nativeReplayOracle).map(([view, pane]) => {
      const selector = view === 'primary' ? '.primary-chart .chart-input'
        : '[aria-label="Chart view ' + view + '"] .chart-input';
      const actual = window.workspaceProbe.series(selector).flat()
        .find(series => series.type === 'Candlestick')?.data ?? [];
      const expected = pane.candles.filter(bar => bar.available_at <= cutoff);
      const mismatch = actual.findIndex((bar, index) => {
        const desired = expected[index];
        return !desired || ['time', 'open', 'high', 'low', 'close'].some(key => bar[key] !== desired[key]);
      });
      return [view, {count: actual.length, mismatch}];
    }));
  }, cutoff), {timeout: 45_000}).toEqual(expected);
  for (const [view, pane] of Object.entries(panes)) {
    const selector = view === 'primary' ? '.primary-chart .pane-label'
      : '[aria-label="Chart view ' + view + '"] .pane-label';
    await expect(page.locator(selector)).toContainText(pane.symbol);
    await expect(page.locator(selector)).toContainText(pane.timeframe);
  }
  return Object.fromEntries(Object.entries(expected).map(([view, result]) => [view, result.count]));
}
async function verifySelection(page, pane, candle) {
  const detail = page.getByLabel('Selected candle', {exact: true});
  await expect(detail).toContainText(iso(candle.time).slice(0, 19));
  const ohlc = detail.getByLabel('Selected candle OHLC', {exact: true});
  const digits = ['USDJPY', 'XAUUSD'].includes(pane.symbol) ? 3 : 5;
  for (const key of ['open', 'high', 'low', 'close']) {
    await expect(ohlc).toContainText(candle[key].toFixed(digits));
  }
  const source = detail.locator('details').filter({hasText: 'Selected candle source'});
  if (!await source.evaluate(element => element.open)) {
    await tabTo(page, source.getByText('Selected candle source', {exact: true}));
    await page.keyboard.press('Enter');
  }
  await expect(source).toContainText(iso(candle.available_at).slice(0, 19));
  await expect(source).toContainText(candle.availability_basis === 'ARCHIVE_SUPPLIED'
    ? 'Archive supplied' : 'Modeled nominal close');
  for (const index of candle.source_partition_indexes) {
    const pin = pane.partitions[index];
    await expect(source).toContainText(pin.id);
    await expect(source).toContainText(pin.sha256);
  }
}
async function checkFixture(browser, fixture, pinChange = false) {
  const {details} = fixture;
  const context = await browser.newContext({viewport: {width: 1536, height: 900}});
  try {
    const page = await context.newPage();
    await configure(page, details);
    await freeze(page, details.request, details.session_id);
    await expect(page.getByLabel('Replay timeline')).toHaveValue(String(details.checkpoint.position));
    const end = details.request.end;
    const full = await verifyBars(page, details.panes, end);
    if (pinChange) {
      await activate(page, 'Exit replay');
      await resume(page, details.session_id);
      const view = '4';
      const pane = details.panes[view];
      const selected = pane.candles.at(-2);
      await choose(page, 'Inspection pane', view);
      await tabTo(page, page.getByLabel('Inspect candle', {exact: true}));
      await page.keyboard.press('End');
      await page.keyboard.press('ArrowLeft');
      await verifySelection(page, pane, selected);
      await activate(page, 'Previous candle');
      await activate(page, 'Bookmark candle');
      await activate(page, 'Last candle');
      await activate(page, 'Save replay position');
      await expect(page.getByText('Replay position saved', {exact: true})).toBeVisible();
      details.checkpoint.bookmarks.splice(1, 0, details.checkpoint.position - 1);
      details.checkpoint.selected = {view, time: selected.time};
      assert.deepEqual((await request('/api/replay/sessions/' + details.session_id)).checkpoint, details.checkpoint);
      writeFileSync(fixture.output, JSON.stringify(details) + '\n');
    }
    const result = {source: details.source_kind, session: details.session_id, full};
    if (details.source_kind === 'CONTROLLED_ARCHIVE_AVAILABILITY') {
      const primary = details.panes.primary.candles;
      await verifySelection(page, details.panes.primary, primary[0]);
      await expect(page.getByText('End of the requested range', {exact: true})).toBeVisible();
      await activate(page, 'Restart');
      result.firstRelease = await verifyBars(page, details.panes, primary[1].available_at);
      await expect(page.getByLabel('Selected candle OHLC', {exact: true})).toHaveCount(0);
      await activate(page, 'Next candle');
      result.tiedRelease = await verifyBars(page, details.panes, primary[0].available_at);
      await verifySelection(page, details.panes.primary, primary[0]);
      await tabTo(page, page.getByLabel('Inspect candle', {exact: true}));
      await page.keyboard.press('ArrowRight');
      await verifySelection(page, details.panes.primary, primary[1]);
      await activate(page, 'Next candle');
      assert.deepEqual(await verifyBars(page, details.panes, primary[0].available_at), result.tiedRelease);
      await activate(page, 'Next candle');
      result.lastPrimaryRelease = await verifyBars(page, details.panes, primary.at(-1).available_at);
      await activate(page, 'Last candle');
      result.exactTerminalRelease = await verifyBars(page, details.panes, end);
      await expect(page.getByText('End of the requested range', {exact: true})).toBeVisible();
    }
    uiActions.push(result);
  } finally {
    await context.close();
  }
}

let api, vite, browser;
async function run() {
  for (const origin of [apiOrigin, uiOrigin]) {
    assert(!await fetch(origin, {signal: AbortSignal.timeout(1000)}).catch(() => undefined),
      'Private service port is already in use: ' + origin);
  }
  api = startApi();
  await awaitReady(apiOrigin, '/api/health', api);
  const pids = [api.pid];
  if (args.drawings) {
    const {details, output} = fixture('genuine-csv', ['--csv', resolve(args.csv), '--storage', storage]);
    vite = startVite();
    await awaitReady(uiOrigin, '/', vite);
    browser = await chromium.launch();
    let page = await browser.newPage({viewport: {width: 1536, height: 900}});
    await configure(page, details);
    if(args.cutoff)await installCutoffClock(page,details);
    await freeze(page, details.request, details.session_id);
    const bars = await verifyBars(page, details.panes, details.request.end);
    const journey = args.cutoff ? await cutoffJourney(page,details,verifyBars) : await drawingJourney(page, details, verifySelection);
    const boundedId=args.cutoff?await createBoundedSession(page,details):undefined;
    await browser.close();
    browser = undefined;
    await stop(api);
    api = startApi();
    await awaitReady(apiOrigin, '/api/health', api);
    assert.notEqual(api.pid, pids[0]);
    fixture('reopen', ['--fixture', output]);
    browser = await chromium.launch();
    page = await browser.newPage({viewport: {width: 1536, height: 900}});
    await configure(page, details, true);
    if(args.cutoff)await installCutoffClock(page,details);
    await resume(page, details.session_id);
    await verifyBars(page, details.panes, details.request.end);
    if(args.cutoff)journey.reopened=await cutoffJourney(page,details,verifyBars,journey.entries);
    if(boundedId){
      await page.close();
      page=await browser.newPage({viewport:{width:1536,height:900}});
      await configure(page,details,true);
      journey.server_sessions=await checkServerSessions(page,details,boundedId,journey.entries,resume);
    }
    else await verifyReopenedDrawings(page, details, journey);
    assert.deepEqual(errors, []);
    mkdirSync(dirname(resolve(args.output)), {recursive: true});
    writeFileSync(resolve(args.output), JSON.stringify({
      session: details.session_id, source: details.source_kind, source_hashes: details.sources.map(row => row.sha256),
      bars, ...(args.cutoff?{chromium:browser.version(),elapsed_seconds:performance.now()/1000,
        clock:'Same-session rewind replaces the preceding clock slot with T-1. Server-session checks use unmodified replay responses.'}:{}),
      api_processes: 2, browser_processes: 2, uncaught_page_errors: errors, ...journey,
    }, null, 2) + '\n');
    if(args.cutoff)assert.deepEqual([...journey.leaks,...journey.reopened.leaks,
      ...journey.server_sessions.filter(sample=>sample.leaked)],[], 'Future drawings exposed');
    console.log('Genuine replay drawing journey and two-process reopen passed.');
    return;
  }
  if(args['forex-decisions'] || args['decision-boundaries']){
    if(!args['decision-boundaries'])fixture('genuine-csv', ['--csv', resolve(args.csv), '--storage', storage]);
    vite=startVite();
    await awaitReady(uiOrigin,'/',vite);
    const restart=async()=>{
      await stop(api);
      api=startApi();
      await awaitReady(apiOrigin,'/api/health',api);
    };
    let receipt;
    if(args['decision-boundaries']){
      const fixtureData=(...args)=>JSON.parse(execFileSync(python,['-m','scripts.decision_boundary_fixture',...args],
        {env,cwd:root,encoding:'utf8',windowsHide:true}));
      const {verifyDecisionBoundaries}=await import('./decision-boundaries.mjs');
      receipt=await verifyDecisionBoundaries(uiOrigin,fixtureData(),restart,()=>fixtureData('counts'));
    }else{
      const {verifyDecisions}=await import('./forex-decisions.mjs');
      receipt=await verifyDecisions(uiOrigin,restart);
    }
    mkdirSync(dirname(resolve(args.output)),{recursive:true});
    writeFileSync(resolve(args.output),JSON.stringify(receipt,null,2));
  }else{
  const controlled = fixture('prepare');
  const genuine = fixture('genuine-bi5', ['--bi5', resolve(args.bi5)]);
  const exports = fixture('genuine-csv', ['--csv', resolve(args.csv), '--storage', storage]);
  const delayed = fixture('delayed', ['--storage', storage]);
  vite = startVite();
  await awaitReady(uiOrigin, '/', vite);
  browser = await chromium.launch();
  await checkFixture(browser, controlled, true);
  await checkFixture(browser, genuine);
  await checkFixture(browser, exports);
  await checkFixture(browser, delayed);

  await browser.close();
  browser = undefined;
  await stop(api);
  api = startApi();
  await awaitReady(apiOrigin, '/api/health', api);
  pids.push(api.pid);
  assert.equal(new Set(pids).size, 2);
  fixture('reopen', ['--fixture', controlled.output]);

  browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 1536, height: 900}});
  const {details} = controlled;
  await configure(page, details, true);
  await resume(page, details.session_id);
  await expect(page.getByLabel('Replay timeline')).toHaveValue(String(details.checkpoint.position));
  const {view, time} = details.checkpoint.selected;
  const pane = details.panes[view];
  const selected = pane.candles.find(bar => bar.time === time);
  assert(selected);
  await expect(page.getByLabel('Inspection pane')).toHaveValue(view);
  await verifySelection(page, pane, selected);
  const full = await verifyBars(page, details.panes, details.panes.primary.candles.at(-1).available_at);
  const rewind = details.checkpoint.bookmarks[0];
  const bookmarks = page.getByRole('combobox', {name: /^Bookmarks/});
  await expect(bookmarks.locator('option')).toHaveCount(details.checkpoint.bookmarks.length + 1);
  await choose(page, 'Bookmarks', String(rewind + 1));
  const rewindCounts = await verifyBars(page, details.panes, details.panes.primary.candles[rewind].available_at);
  await expect(page.getByLabel('Selected candle', {exact: true}))
    .toContainText('The selected candle is beyond the replay cutoff.');
  await expect(page.getByLabel('Selected candle OHLC', {exact: true})).toHaveCount(0);
  await activate(page, 'Last candle');
  await verifySelection(page, pane, selected);
  await verifyBars(page, details.panes, details.panes.primary.candles.at(-1).available_at);
  assert.deepEqual(errors, []);
  mkdirSync(dirname(resolve(args.output)), {recursive: true});
  writeFileSync(resolve(args.output), JSON.stringify({
    api_processes: pids.length, browser_processes: 2, source_cases: uiActions,
    keyboard_journey: ['Exit replay', 'Resume saved replay', 'Pick another pane', 'Inspect older candle',
      'Bookmark candle', 'Save replay position', 'Resume after process restart', 'Rewind using bookmark',
      'Reveal the same candle by returning to the last cutoff'],
    checkpoint: details.checkpoint, reopened: full, rewind_position: rewind,
    rewind_bars: rewindCounts, selected_candle: selected, uncaught_page_errors: errors,
  }, null, 2) + '\n');
  console.log('Native archive freeze, two-process restart and all named panes verified.');
  }
}
try {
  await run();
} finally {
  if (browser) await browser.close();
  await stop(vite);
  await stop(api);
}
