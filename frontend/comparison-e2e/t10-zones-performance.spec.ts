import {readFileSync, writeFileSync} from 'node:fs';
import {expect, test, type Page} from '@playwright/test';
import {
  baseline, button, configurePair, end, frame, iso, monitorRequests, openTerminal, prepareSeek, receipt, reopen, replay,
  seek, select, startSession, stored, waitForEvidence, type View,
} from './t10-zones-support';

type Surface = {
  endpoint: string; title: string; selectName: string; article: string; selection: string;
};
type Selection = {id: string; zone_id?: string; type: string; available_at: number; evidence: unknown[]};
type Result = {events?: Selection[]; zones?: Selection[]; selected: Selection | null};
const structure = {
  endpoint: 'structure-liquidity', title: 'Replay structure and liquidity', selectName: 'Replay event',
  article: 'Selected replay event', selection: 'event_id',
};
const zones = {
  endpoint: 'zones', title: 'Replay FVG and order blocks', selectName: 'Replay zone',
  article: 'Selected replay zone', selection: 'zone_id',
};
const surfaces = baseline ? [structure] : [structure, zones];
const labels = (page: Page, surface: Surface) => {
  const panel = page.getByRole('region', {name: surface.title, exact: true});
  return {panel, menu: select(panel, surface.selectName), article: panel.getByRole('article', {name: surface.article}),
    disclosure: page.locator('summary').getByText(surface.title, {exact: true})};
};
const sampleCount = 8;
const domain = baseline ? 'baseline' : 'candidate';

type Samples = Record<string, number[]>;
type Rows = {collapsed: number; expanded: number; afterClose: number; source: string};
type Saved = {id: string; selections: Record<string, string>};
function stats(samples: Samples) {
  return Object.fromEntries(Object.entries(samples).map(([action, values]) => {
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return [action, {count: sorted.length, median: sorted.length % 2 ? sorted[middle] :
      (sorted[middle - 1] + sorted[middle]) / 2, p95: sorted[Math.ceil(sorted.length * .95) - 1], samples: values}];
  }));
}
async function measure(samples: Samples, label: string, action: () => Promise<void>) {
  const start = performance.now();
  await action();
  (samples[label] ??= []).push(performance.now() - start);
}

function ready(page: Page, surface: Surface, cutoff: number, id?: string | null, view: View = 'primary') {
  return waitForEvidence<Result>(page, surface.endpoint, cutoff, view, id === undefined ? undefined : [surface.selection, id]);
}

async function sampleSurface(page: Page, surface: Surface, saved: Saved) {
  const {panel, menu, article, disclosure} = labels(page, surface);
  const requests = monitorRequests(page, surface.endpoint);
  const samples: Samples = {};
  const observations: Rows[] = [];
  for (let iteration = 0; iteration < sampleCount; iteration++) {
    await seek(page, end - frame);
    const before = requests.length;
    await measure(samples, 'collapsed step', async () => {
      await button(replay(page), 'Next candle').click();
      await expect(replay(page).locator('output')).toContainText(iso(end));
      await expect(panel).toHaveCount(0);
    });
    expect(requests).toHaveLength(before);

    const request = ready(page, surface, end);
    let pageResult!: Result;
    await measure(samples, 'list', async () => {
      await disclosure.click();
      pageResult = await request;
      await expect(menu).toBeVisible();
    });
    expect(requests).toHaveLength(before + 1);
    const choices = pageResult.events ?? pageResult.zones!;
    if (await menu.inputValue()) {
      const cleared = ready(page, surface, end, null);
      await menu.selectOption('');
      await waitUntilUnselected(page, surface, cleared);
    }
    const choice = choices.find(item => item.type === (surface === structure ? 'BOS' : 'FVG'))!;
    expect(choice).toBeDefined();
    const id = choice.zone_id ?? choice.id;
    saved.selections[surface.endpoint] = id;
    const selected = ready(page, surface, end, id);
    let detail!: Selection;
    await measure(samples, 'select', async () => {
      await menu.selectOption(id);
      detail = (await selected).selected!;
      expect(detail.zone_id ?? detail.id).toBe(id);
      await expect(article).toBeVisible();
    });

    const sourceDetails = article.locator('details').filter({
      hasText: surface === structure ? /calculation context candles/ : /direct source candles/,
    });
    const collapsed = await article.locator('tbody tr').count();
    await measure(samples, 'source expansion', async () => {
      await sourceDetails.locator('summary').click();
      await expect(sourceDetails.getByRole('table').locator('tbody tr').first()).toBeVisible();
    });
    const expanded = await article.locator('tbody tr').count();
    await sourceDetails.locator('summary').click();
    const afterClose = await article.locator('tbody tr').count();
    observations.push({collapsed, expanded, afterClose, source: detail.zone_id ?? detail.id});
    if (!baseline) {
      expect(collapsed).toBe(0);
      expect(expanded).toBeGreaterThan(0);
      await expect(article.locator('tbody tr')).toHaveCount(0);
    }

    const earlier = detail.available_at - frame;
    await prepareSeek(page, earlier);
    const rewound = ready(page, surface, earlier, id);
    await measure(samples, 'rewind', async () => {
      await button(replay(page), 'Seek').click();
      await rewound;
      await expect(replay(page).locator('output')).toContainText(iso(earlier));
      await expect(panel).toContainText('replay cutoff');
      if (surface === structure) await expect(article).toHaveCount(0);
    });
    if (iteration < sampleCount - 1) {
      await disclosure.click();
      await expect(panel).toHaveCount(0);
    }
  }
  await seek(page, end);
  await expect(article).toBeVisible();
  return {timings_ms: stats(samples), source_table_dom_rows: observations, collapsed_request_count: 0};
}

async function waitUntilUnselected(page: Page, surface: Surface, response: Promise<Result>) {
  expect((await response).selected).toBeNull();
  const {article} = labels(page, surface);
  await expect(article).toHaveCount(0);
}

test('browser timing and row mounts on each evidence surface', async ({page, context, browser}) => {
  test.skip(reopen);
  test.setTimeout(240_000);
  await configurePair(page);
  const session = await startSession(page, 'EURUSD', 'GBPUSD');
  const saved: Saved = {id: session.id, selections: {}};
  const measurements: Record<string, Awaited<ReturnType<typeof sampleSurface>>> = {};
  for (const surface of surfaces) {
    measurements[surface.endpoint] = await sampleSurface(page, surface, saved);
    await labels(page, surface).disclosure.click();
    await expect(labels(page, surface).panel).toHaveCount(0);
  }
  for (const surface of surfaces) {
    await labels(page, surface).disclosure.click();
    await expect(labels(page, surface).article).toBeVisible();
  }
  await button(replay(page), 'Save replay position').click();
  await expect(replay(page).getByText('Replay position saved', {exact: true})).toBeVisible();
  await context.storageState({path: stored('zone-performance-state.json')});
  writeFileSync(stored('zone-performance-checkpoint.json'), JSON.stringify(saved));
  receipt('zones-ui-' + domain, {chromium: browser.version(), session_id: saved.id, measurements});
});

test('cold browser contexts reopen the saved evidence after service restart', async ({browser}) => {
  test.skip(!reopen);
  test.setTimeout(240_000);
  const saved: Saved = JSON.parse(readFileSync(stored('zone-performance-checkpoint.json'), 'utf8'));
  const measured = Object.fromEntries(surfaces.map(surface => [surface.endpoint, {} as Samples]));
  const serviceRestart: Record<string, number> = {};
  for (let iteration = 0; iteration < sampleCount; iteration++) {
    const context = await browser.newContext({storageState: stored('zone-performance-state.json'), serviceWorkers: 'block'});
    try {
      const page = await context.newPage();
      await openTerminal(page);
      const began = performance.now();
      const results = surfaces.map(async surface => {
        const result = await ready(page, surface, end, saved.selections[surface.endpoint]);
        expect(result.selected?.zone_id ?? result.selected?.id).toBe(saved.selections[surface.endpoint]);
        await expect(labels(page, surface).article).toBeVisible();
        const elapsed = performance.now() - began;
        if (iteration === 0) serviceRestart[surface.endpoint] = elapsed;
        (measured[surface.endpoint]['cold browser reopen'] ??= []).push(elapsed);
      });
      await select(page, 'Resume saved replay').selectOption(saved.id);
      await Promise.all(results);
    } finally {
      await context.close();
    }
  }
  receipt('zones-ui-' + domain + '-restart', {chromium: browser.version(), session_id: saved.id,
    timings_ms: Object.fromEntries(Object.entries(measured).map(([surface, samples]) => [surface, stats(samples)])),
    first_reopen_after_service_restart_ms: serviceRestart});
});
