import {readFileSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {expect, test, type Locator, type Page} from '@playwright/test';
import {installProbe} from '../e2e/workspace-probe';
import type {ReplaySession} from '../src/types';

const seriesNames = ['CCI', '%K', '%D'] as const;
const symbols = ['EURUSD', 'GBPUSD'] as const;
const chartSelectors = {EURUSD: '.primary-chart .chart-input', GBPUSD: '.secondary-chart .chart-input'};
const seriesCounts = {CCI: 5, '%K': 11, '%D': 9};

type SymbolName = typeof symbols[number];
type SeriesName = typeof seriesNames[number];
type Point = {time: number; value: number};
type Bar = {time: number; open: number; high: number; low: number; close: number};
type Series = Record<SeriesName, Point[]>;
type ChartSeries = {type: string; title: string; data: Point[] | Bar[]};
type Market = {
  symbol: SymbolName; timeframe: 'M5'; source: 'DUKASCOPY_SAVED'; feed_id: string;
  price_basis: 'BID'; partitions: {id: string; sha256: string}[];
};
type Oracle = {
  day: string; start: number; end: number; step: number; absoluteTolerance: number;
  lanes: Record<SymbolName, {market: Market; providerCsvSha256: string; bars: Bar[]; series: Series}>;
};
type Saved = {
  session: ReplaySession;
  checkpoint: NonNullable<ReplaySession['checkpoint']>;
  plotted: Record<SymbolName, Series>;
};

const storage = process.env.TRADEFORGE_PAIR_STORAGE!;
const reopen = process.env.TRADEFORGE_PAIR_REOPEN === '1';
const browserState = join(storage, 't10-indicators-browser-state.json');
const savedPath = join(storage, 't10-indicators-saved.json');
const oracle: Oracle = JSON.parse(readFileSync(join(storage, 't10-indicators-reference.json'), 'utf8'));
const position = 23;
const timeAt = (hm: string) => Date.parse(oracle.day + 'T' + hm + ':00Z') / 1000;
const replay = (page: Page) => page.getByLabel('Historical replay', {exact: true});
const button = (scope: Page | Locator, name: string) => scope.getByRole('button', {name, exact: true});
const select = (scope: Page | Locator, name: string) => scope.getByRole('combobox', {name, exact: true});

if (reopen) test.use({storageState: browserState});

const barFields = ({time, open, high, low, close}: Bar): Bar => ({time, open, high, low, close});
const pointFields = ({time, value}: Point): Point => ({time, value});
const utc = (t: number) => new Date(t * 1000).toISOString();
const closedAt = (t: number, cutoff: number) => t + oracle.step <= cutoff;

function assertSession(session: ReplaySession) {
  expect(session.id).toMatch(/^[0-9a-f]{64}$/);
  expect(session).toMatchObject({
    request: {
      symbol: 'EURUSD', timeframe: 'M5', source: 'DUKASCOPY_SAVED', start: oracle.start, end: oracle.end,
      views: [{view: '2', symbol: 'GBPUSD', timeframe: 'M5'}],
    },
  });
  expect(Object.keys(session.views)).toEqual(['2']);
  for (const [symbol, market] of [['EURUSD', session.market], ['GBPUSD', session.views['2']]] as const) {
    expect(market).toMatchObject({
      ...oracle.lanes[symbol].market, synthetic: false, latest_unconfirmed: false,
      coverage: {start: oracle.start, end: oracle.end, gaps: [], duplicate_bars: 0, truncated: false},
      candles: oracle.lanes[symbol].bars.map(bar => ({...bar, available_at: bar.time + oracle.step})),
    });
  }
}

async function indicators(scope: Page | Locator) {
  await select(scope, 'Indicator').selectOption('cci');
  const library = scope.locator('.indicator-library');
  await library.locator('summary').click();
  await select(library, 'Add indicator').selectOption('stochastic');
  await button(library, 'Enable indicator').click();
  await library.locator('summary').click();
}

async function readCharts(page: Page, cutoff: number): Promise<Record<SymbolName, Series>> {
  const count = (cutoff - oracle.start) / oracle.step;
  await expect(replay(page).locator('output')).toHaveText(count + ' / 24 ' + utc(cutoff));

  const observed = {} as Record<SymbolName, Series>;
  for (const symbol of symbols) {
    const {bars, series} = oracle.lanes[symbol];
    const expectedBars = bars.filter(({time}) => closedAt(time, cutoff));
    const expectedSeries = seriesNames.flatMap(title => {
      const times = series[title].filter(({time}) => closedAt(time, cutoff)).map(({time}) => time);
      return times.length ? [{title, times}] : [];
    });

    const chart = () => page.evaluate(selector => window.workspaceProbe.series(selector),
      chartSelectors[symbol]) as Promise<ChartSeries[][]>;
    await expect.poll(async () => {
      const all = (await chart()).flat();
      return {
        candles: all.filter(item => item.type === 'Candlestick').map(item => (item.data as Bar[]).map(barFields)),
        oscillatorTimes: seriesNames.flatMap(title => all.filter(item => item.type === 'Line' && item.title === title)
          .map(item => ({title, times: item.data.map(({time}) => time)}))),
      };
    }, {message: symbol + ' plotted candles and oscillator timestamps at ' + utc(cutoff), timeout: 10000})
      .toEqual({candles: [expectedBars], oscillatorTimes: expectedSeries});

    const all = (await chart()).flat();
    observed[symbol] = {} as Series;
    for (const title of seriesNames) {
      const expected = series[title].filter(({time}) => closedAt(time, cutoff));
      const trace = all.find(item => item.type === 'Line' && item.title === title);
      const points = ((trace?.data ?? []) as Point[]).map(pointFields);
      expect(points.map(({time}) => time)).toEqual(expected.map(({time}) => time));
      for (const [index, {time, value}] of points.entries()) {
        expect(Math.abs(value - expected[index].value), symbol + ' ' + title + ' ' + utc(time))
          .toBeLessThanOrEqual(oracle.absoluteTolerance);
      }
      observed[symbol][title] = points;
    }
  }
  return observed;
}

function responseFor(page: Page, suffix: string, method: string) {
  return page.waitForResponse(response => new URL(response.url()).pathname.endsWith(suffix) &&
    response.request().method() === method);
}

test('saves both genuine oscillator charts at the final 15:00 UTC checkpoint', async ({page, context}) => {
  test.skip(reopen);
  test.setTimeout(90000);
  await installProbe(page);
  await page.setViewportSize({width: 1536, height: 900});
  await page.goto('/?timezone=UTC');
  await button(page, 'Market Terminal').click();
  await select(page, 'Market data source').selectOption('DUKASCOPY_SAVED');
  await page.getByLabel('From UTC', {exact: true}).fill(oracle.day);
  await page.getByLabel('From time (UTC)', {exact: true}).fill('13:00');
  await page.getByLabel('Until UTC (exclusive)', {exact: true}).fill(oracle.day);
  await page.getByLabel('Until time (UTC)', {exact: true}).fill('15:00');
  await button(page, 'Load saved range').click();
  await indicators(page);
  await select(page, 'Chart layout').selectOption('2');
  const other = page.getByLabel('Chart view 2', {exact: true});
  await select(other, 'View 2 instrument').selectOption('GBPUSD');
  await select(other, 'View 2 timeframe').selectOption('M5');
  const disclosure = other.getByText('View 2 indicators', {exact: true});
  await disclosure.click();
  await indicators(other);
  await disclosure.click();

  const created = responseFor(page, '/replay/sessions', 'POST');
  await button(page, 'Bar replay').click();
  const createResponse = await created;
  expect(createResponse.status(), await createResponse.text()).toBe(201);
  const session: ReplaySession = await createResponse.json();
  assertSession(session);
  expect(session.checkpoint).toBeNull();
  await button(replay(page), 'Last candle').click();
  const plotted = await readCharts(page, oracle.end);
  for (const symbol of symbols) {
    for (const name of seriesNames) expect(plotted[symbol][name]).toHaveLength(seriesCounts[name]);
  }

  const saved = responseFor(page, '/replay/sessions/' + session.id + '/checkpoint', 'POST');
  await button(replay(page), 'Save replay position').click();
  const saveResponse = await saved;
  const checkpoint = {position, bookmarks: []};
  expect(saveResponse.status()).toBe(200);
  expect(saveResponse.request().postDataJSON()).toEqual(checkpoint);
  expect(await saveResponse.json()).toEqual(checkpoint);
  await expect(replay(page).getByText('Replay position saved', {exact: true})).toBeVisible();
  writeFileSync(savedPath, JSON.stringify({session, checkpoint, plotted} satisfies Saved));
  await context.storageState({path: browserState});
});

test('restores all plotted points after restart and recovers them after rewinding before warm-up',
  async ({page, browser}) => {
    test.skip(!reopen);
    test.setTimeout(90000);
    const saved: Saved = JSON.parse(readFileSync(savedPath, 'utf8'));
    await installProbe(page);
    await page.setViewportSize({width: 1536, height: 900});
    await page.goto('/');
    await button(page, 'Market Terminal').click();

    const pending = responseFor(page, '/replay/sessions/' + saved.session.id, 'GET');
    await select(page, 'Resume saved replay').selectOption(saved.session.id);
    const response = await pending;
    expect(response.status(), await response.text()).toBe(200);
    const restored: ReplaySession = await response.json();
    assertSession(restored);
    expect(restored).toEqual({...saved.session, checkpoint: saved.checkpoint});
    const reopened = await readCharts(page, oracle.end);
    expect(reopened).toEqual(saved.plotted);

    const verifyAt = async (cutoff: number) => {
      const plotted = await readCharts(page, cutoff);
      return {
        cutoffUtc: utc(cutoff),
        closedBarsPerChart: (cutoff - oracle.start) / oracle.step,
        observedSeriesCounts: Object.fromEntries(symbols.map(symbol =>
          [symbol, Object.fromEntries(seriesNames.map(name => [name, plotted[symbol][name].length]))])),
      };
    };
    const jump = async (hm: string) => {
      await replay(page).getByLabel('Jump to UTC', {exact: true}).fill(oracle.day + 'T' + hm);
      await button(replay(page), 'Seek').click();
    };
    await jump('14:05');
    const replaySteps = [await verifyAt(timeAt('14:05'))];
    expect(replaySteps[0].observedSeriesCounts).toEqual({
      EURUSD: {CCI: 0, '%K': 0, '%D': 0}, GBPUSD: {CCI: 0, '%K': 0, '%D': 0},
    });
    await button(replay(page), 'Next candle').click();
    replaySteps.push(await verifyAt(timeAt('14:10')));
    for (const hm of ['14:20', '14:40']) {
      await jump(hm);
      replaySteps.push(await verifyAt(timeAt(hm)));
    }
    await button(replay(page), 'Last candle').click();
    const recovered = await readCharts(page, oracle.end);
    expect(recovered).toEqual(reopened);

    const receipt = {
      status: 'PASS',
      chromiumVersion: browser.version(),
      startInclusiveUtc: utc(oracle.start),
      endExclusiveUtc: utc(oracle.end),
      savedCutoffUtc: utc(oracle.end),
      sessionId: restored.id,
      checkpoint: restored.checkpoint,
      expectedSeriesCountsPerChart: seriesCounts,
      reference: 'Exact rational arithmetic on checksummed provider M5 BID CSV decimals',
      absoluteTolerance: oracle.absoluteTolerance,
      replaySteps,
      lanes: Object.fromEntries(symbols.map(symbol => {
        const {market, providerCsvSha256, series} = oracle.lanes[symbol];
        return [symbol, {
          ...market, providerCsvSha256,
          series: Object.fromEntries(seriesNames.map(name => [name, series[name].map(({time, value}, index) => ({
            candleOpenUtc: utc(time),
            availableAtUtc: utc(time + oracle.step),
            reference: value,
            savedPlot: saved.plotted[symbol][name][index].value,
            reopenedPlot: reopened[symbol][name][index].value,
            recoveredPlot: recovered[symbol][name][index].value,
          }))])),
        }];
      })),
    };
    writeFileSync(join(process.env.TRADEFORGE_T10_CHART_OUTPUT!, 'dual-chart-indicators-checkpoint.json'),
      JSON.stringify(receipt, null, 2) + '\n');
  });
