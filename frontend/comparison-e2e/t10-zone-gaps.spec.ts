import {readFileSync, writeFileSync} from 'node:fs';
import {expect, test, type Locator, type Page} from '@playwright/test';
import type {ReplayZoneMitigation, ReplayZonePage, ReplayZoneReceipt} from '../src/replayZoneTypes';
import {artifact, button, configurePair, end, frame, iso, openTerminal, receipt, reference,
  replay, seek, select, startSession, stored, utc, type View} from './t10-zones-support';

type Lane = typeof reference.symbols.EURUSD & {
  type: 'FVG' | 'OB'; anchor: number; formation: number; previous: number; successor: number;
  T: number; missing: {start: number; end: number}[];
};
type Sample = {before: ReplayZoneReceipt; at: ReplayZoneReceipt};
type Checkpoint = {
  sessionId: string;
  fvg: Record<ReplayZoneMitigation, Sample>;
  ob: Record<ReplayZoneMitigation, Sample>;
};

const fvg = reference.symbols.EURUSD as Lane;
const ob = reference.symbols.GBPUSD as Lane;
const title = 'Replay FVG and order blocks';
const panel = (page: Page) => page.getByRole('region', {name: title, exact: true});
const zones = (page: Page) => select(panel(page), 'Replay zone');
const pane = (page: Page) => select(panel(page), 'Zone chart pane');
const mitigation = (page: Page) => select(panel(page), 'Zone mitigation');
const selected = (page: Page) => panel(page).getByRole('article', {name: 'Selected replay zone'});
const gap = (page: Page) => selected(page).getByRole('region', {name: 'Missing open candles evidence'});
const receiptField = (scope: Locator, label: string) => scope.getByText(label, {exact: true}).locator('..').locator('dd');

function waitForSelection(page: Page, cutoff: number, view: View, mode: ReplayZoneMitigation, id: string) {
  return page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname.endsWith('/zones') && url.searchParams.get('cutoff') === String(cutoff) &&
      url.searchParams.get('view') === view && url.searchParams.get('mitigation') === mode &&
      url.searchParams.get('zone_id') === id;
  }).then(async response => {
    expect(response.status(), await response.text()).toBe(200);
    const result = await response.json() as ReplayZonePage;
    expect(result).toMatchObject({cutoff, view, authority: 'RESEARCH_ONLY',
      rules: {fvg: {mitigation: mode}, ob: {mitigation: mode}}});
    expect(result.selected?.zone_id).toBe(id);
    return result.selected!;
  });
}

async function verifyBefore(page: Page, before: ReplayZoneReceipt) {
  expect(before).toMatchObject({revision: 1, state: 'ACTIVE'});
  expect(before.revisions).toHaveLength(1);
  expect(before.revisions[0].continuity).toBeUndefined();
  await expect(selected(page)).toHaveAttribute('data-revision-id', before.id);
  await expect(receiptField(selected(page), 'State')).toHaveText('ACTIVE');
  await expect(gap(page)).toHaveCount(0);
}

async function verifyGap(page: Page, lane: Lane, value: ReplayZoneReceipt) {
  expect(value).toMatchObject({revision: 2, state: 'INVALIDATED', reason: 'MISSING_OPEN_CANDLES',
    anchor_time: lane.anchor, formation_time: lane.formation, observation_time: lane.successor, available_at: lane.T});
  expect(value.revisions).toHaveLength(2);
  const revision = value.revisions[1];
  expect(revision.id).toBe(value.id);
  expect(revision.continuity).toMatchObject({
    previous_observed_open: lane.previous, next_observed_open: lane.successor,
    missing_open_intervals: lane.missing,
  });
  await expect(selected(page)).toHaveAttribute('data-revision-id', revision.id);
  await expect(receiptField(selected(page), 'State')).toHaveText('INVALIDATED');
  await expect(receiptField(selected(page), 'Reason')).toHaveText('MISSING OPEN CANDLES');
  await expect(gap(page)).toHaveAttribute('data-revision-id', revision.id);
  await expect(receiptField(gap(page), 'Missing candle opens [start, end) · UTC').locator('div'))
    .toHaveText(lane.missing.map(({start, end}) => '[' + utc(start) + ', ' + utc(end) + ')'));
  const pinned = async (time: number, role: string, label: string) => {
    const id = lane.partition.id + ':' + time;
    expect(value.evidence.find(row => row.source_id === id)).toMatchObject({
      time, partitions: [lane.partition], roles: expect.arrayContaining([{role, event_id: revision.id}]),
    });
    const boundary = receiptField(gap(page), label);
    await expect(boundary.getByText(utc(time), {exact: true})).toBeVisible();
    await expect(boundary.getByText(id, {exact: true})).toBeVisible();
    await expect(boundary.getByText(lane.partition.id, {exact: true})).toBeVisible();
    await expect(boundary.getByText(lane.partition.sha256, {exact: true})).toBeVisible();
  };
  await pinned(lane.previous, 'GAP_PREVIOUS_OBSERVED', 'Previous observed candle open');
  await pinned(lane.successor, 'GAP_NEXT_OBSERVED', 'Next observed candle open');
}

const beforeCutoff = (lane: Lane) => fvg.bars.filter(bar => bar.available_at < lane.T).at(-1)!.available_at;

async function inspectMode(page: Page, lane: Lane, view: View, mode: ReplayZoneMitigation): Promise<Sample> {
  const previous = beforeCutoff(lane);
  await seek(page, previous);
  await mitigation(page).selectOption(mode);
  const option = zones(page).locator('option').filter({hasText: [
    lane.type === 'FVG' ? 'FVG' : 'Order block', 'BULLISH', 'ACTIVE', utc(lane.formation + frame),
  ].join(' · ')});
  await expect(option).toHaveCount(1);
  const id = (await option.getAttribute('value'))!;
  const beforeResponse = waitForSelection(page, previous, view, mode, id);
  await zones(page).selectOption(id);
  const before = await beforeResponse;
  await verifyBefore(page, before);
  const atResponse = waitForSelection(page, lane.T, view, mode, id);
  await button(replay(page), 'Next candle').click();
  const at = await atResponse;
  await verifyGap(page, lane, at);
  const rewind = waitForSelection(page, previous, view, mode, id);
  await button(replay(page), 'Previous candle').click();
  expect(await rewind).toEqual(before);
  await verifyBefore(page, before);
  const advance = waitForSelection(page, lane.T, view, mode, id);
  await button(replay(page), 'Next candle').click();
  expect(await advance).toEqual(at);
  await verifyGap(page, lane, at);
  return {before, at};
}

test('initial Chromium journey verifies both gap witnesses in wick and close modes', async ({page, context, browser}) => {
  test.setTimeout(120_000);
  await configurePair(page);
  const session = await startSession(page, 'EURUSD', 'GBPUSD');
  await page.locator('summary').getByText(title, {exact: true}).click();
  const fvgWick = await inspectMode(page, fvg, 'primary', 'wick');
  const fvgClose = await inspectMode(page, fvg, 'primary', 'close');
  await pane(page).selectOption('2');
  const obWick = await inspectMode(page, ob, '2', 'wick');
  const obClose = await inspectMode(page, ob, '2', 'close');
  await expect(replay(page).locator('output')).toContainText(iso(end));
  await button(replay(page), 'Save replay position').click();
  await expect(replay(page).getByText('Replay position saved', {exact: true})).toBeVisible();
  await context.storageState({path: stored('zone-gap-state.json')});
  const saved: Checkpoint = {
    sessionId: session.id, fvg: {wick: fvgWick, close: fvgClose}, ob: {wick: obWick, close: obClose},
  };
  writeFileSync(stored('zone-gap-checkpoint.json'), JSON.stringify(saved));
  receipt('zone-gap-browser-initial', {chromium: browser.version(), saved_cutoff: end, ...saved});
});

test('restart restores both saved selections and their revision-bound witnesses', async ({browser}) => {
  test.setTimeout(120_000);
  const saved: Checkpoint = JSON.parse(readFileSync(stored('zone-gap-checkpoint.json'), 'utf8'));
  const context = await browser.newContext({storageState: stored('zone-gap-state.json'), serviceWorkers: 'block'});
  try {
    const page = await context.newPage();
    await openTerminal(page);
    const resumed = waitForSelection(page, end, '2', 'close', saved.ob.close.at.zone_id);
    await select(page, 'Resume saved replay').selectOption(saved.sessionId);
    expect(await resumed).toEqual(saved.ob.close.at);
    await expect(replay(page).locator('output')).toContainText(iso(end));
    await expect(pane(page)).toHaveValue('2');
    await expect(mitigation(page)).toHaveValue('close');
    const verified: Record<string, ReplayZoneReceipt> = {};
    for (const [lane, view, mode, change] of [
      [ob, '2', 'close', null], [ob, '2', 'wick', 'mode'],
      [fvg, 'primary', 'wick', 'pane'], [fvg, 'primary', 'close', 'mode'],
    ] as const) {
      const expected = (view === '2' ? saved.ob : saved.fvg)[mode].at;
      let actual = expected;
      if (change) {
        const received = waitForSelection(page, end, view, mode, expected.zone_id);
        if (change === 'pane') await pane(page).selectOption(view);
        else await mitigation(page).selectOption(mode);
        actual = await received;
        expect(actual).toEqual(expected);
      }
      await verifyGap(page, lane, actual);
      verified[lane.type + '_' + mode] = actual;
      if (mode === 'close') await selected(page).screenshot({path: artifact('zone-gap-reopened-' + lane.type + '.png')});
    }
    receipt('zone-gap-browser-restart', {chromium: browser.version(), session_id: saved.sessionId,
      reopened_cutoff: end, verified});
  } finally {
    await context.close();
  }
});
