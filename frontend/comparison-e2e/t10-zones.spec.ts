import {readFileSync, writeFileSync} from 'node:fs';
import {expect, test, type Locator, type Page} from '@playwright/test';
import type {ReplaySourceCandle} from '../src/ReplayEvidenceSources';
import type {ReplayZoneMitigation, ReplayZonePage, ReplayZoneReceipt, ReplayZoneRevision} from '../src/replayZoneTypes';
import {
  artifact, baseline, button, configurePair, end, frame, iso, monitorRequests, openTerminal, receipt, reference, reopen,
  replay, seek, select, setOppositePeer, start, startSession, stored, utc, type View,
} from './t10-zones-support';

type Symbol = 'EURUSD' | 'GBPUSD';
type Numeric = 'lower' | 'upper' | 'remaining_lower' | 'remaining_upper' | 'mitigation_pct' | 'observed_price';
type OracleBreak = {type: string; side: string; level_price: string; break_price: string; break_time: number;
  confirmation_time: number; available_at: number; pivot_time: number; pivot_available_at: number;
  pivot_confirmation_bar_time: number};
type Expected = Omit<ReplayZoneRevision, Numeric | 'id' | 'zone_id' | 'algorithm_version' | 'identity_policy_version'> &
  Record<Exclude<Numeric, 'observed_price'>, string> & {observed_price: string | null;
    formation_refs: [number, string][]; structure: OracleBreak | null};
type Oracle = {symbols: Record<Symbol, {modes: Record<ReplayZoneMitigation, Expected[][]>}>};
type Observed = {formation: ReplayZoneReceipt; terminal: ReplayZoneReceipt; alternate: ReplayZoneReceipt};
type Saved = {session_id: string; cutoff: number; fvg: Observed; ob: Observed};
const oracle: Oracle | undefined = baseline ? undefined :
  JSON.parse(readFileSync(process.env.TRADEFORGE_T10_ZONES_REFERENCE!, 'utf8'));

const title = 'Replay FVG and order blocks';
const words = (value: string) => value.replaceAll('_', ' ');
const panel = (page: Page) => page.getByRole('region', {name: title, exact: true});
const disclosure = (page: Page) => page.locator('summary').getByText(title, {exact: true});
const zones = (page: Page) => select(panel(page), 'Replay zone');
const pane = (page: Page) => select(panel(page), 'Zone chart pane');
const mitigation = (page: Page) => select(panel(page), 'Zone mitigation');
const selected = (page: Page) => panel(page).getByRole('article', {name: 'Selected replay zone'});
const dataFields = ['open', 'high', 'low', 'close'] as const;
const formation = start + 6 * frame;
const terminal = formation + frame;

function waitForZone(page: Page, cutoff: number, view: View, mode: ReplayZoneMitigation, id?: string) {
  return page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname.endsWith('/zones') && url.searchParams.get('view') === view &&
      url.searchParams.get('cutoff') === String(cutoff) && url.searchParams.get('mitigation') === mode &&
      (id === undefined || url.searchParams.get('zone_id') === id);
  }).then(async response => {
    expect(response.status(), await response.text()).toBe(200);
    const result = await response.json() as ReplayZonePage;
    expect(result).toMatchObject({cutoff, view, timeframe: 'M5', price_basis: 'BID', authority: 'RESEARCH_ONLY',
      rules: {fvg: {mitigation: mode}, ob: {mitigation: mode}}});
    return result;
  });
}

function chain(symbol: Symbol, mode: ReplayZoneMitigation, type: 'FVG' | 'OB') {
  const match = oracle!.symbols[symbol].modes[mode].filter(rows => rows[0].type === type &&
    rows[0].formation_available_at === formation && rows[0].direction === 'BULLISH');
  expect(match).toHaveLength(1);
  return match[0];
}

function verifyRevision(actual: ReplayZoneRevision, expected: Expected) {
  expect(actual).toMatchObject({
    type: expected.type, direction: expected.direction, state: expected.state, reason: expected.reason,
    revision: expected.revision, anchor_time: expected.anchor_time, formation_time: expected.formation_time,
    formation_confirmation_time: expected.formation_confirmation_time, formation_available_at: expected.formation_available_at,
    observation_time: expected.observation_time, confirmation_time: expected.confirmation_time, available_at: expected.available_at,
    observed_price: expected.observed_price === null ? null : Number(expected.observed_price),
    lower: Number(expected.lower), upper: Number(expected.upper),
    remaining_lower: Number(expected.remaining_lower), remaining_upper: Number(expected.remaining_upper),
  });
  expect(actual.mitigation_pct).toBeCloseTo(Number(expected.mitigation_pct), 5);
}

function verifyPinnedRows(rows: ReplaySourceCandle[], symbol: Symbol, cutoff: number) {
  const {bars, partition} = reference.symbols[symbol];
  const byOpen = new Map(bars.map(bar => [bar.time, bar]));
  expect(rows.length).toBeGreaterThan(0);
  for (const row of rows) {
    const bar = byOpen.get(row.time);
    expect(bar).toBeDefined();
    expect(row).toMatchObject({source_id: partition.id + ':' + row.time, partitions: [partition],
      availability_basis: 'MODELED_NOMINAL_CLOSE', available_at: bar!.available_at});
    expect(row.available_at).toBeLessThanOrEqual(cutoff);
    for (const key of dataFields) expect(row[key]).toBe(Number(bar![key]));
  }
}

async function readFields(receipt: Locator, fields: Record<string, string>) {
  for (const [label, expected] of Object.entries(fields)) {
    await expect(receipt.getByText(label, {exact: true}).locator('..').locator('dd')).toHaveText(expected);
  }
}

async function verifyTable(details: Locator, name: string, sources: ReplaySourceCandle[], symbol: Symbol) {
  const rows = details.getByRole('table', {name, exact: true}).locator('tbody tr');
  await expect(rows).toHaveCount(sources.length);
  const partition = reference.symbols[symbol].partition;
  await expect(details.locator('li')).toHaveText(['Dataset ' + partition.id + ' · SHA-256 ' + partition.sha256]);
  const shown = await rows.evaluateAll(elements => elements.map(element => {
    const cells = [...(element as HTMLTableRowElement).cells];
    return {values: cells.slice(0, 8).map(cell => cell.innerText.trim()),
      roles: [...cells[8].children].map(role => ({
        role: role.firstChild?.textContent?.trim(), event_id: role.querySelector('code')?.title,
      }))};
  }));
  expect(shown).toEqual(sources.map(row => ({values: [utc(row.time), utc(row.available_at), words(row.availability_basis),
    ...dataFields.map(key => String(row[key])), row.source_id],
    roles: row.roles.map(role => ({role: words(role.role).toLowerCase(), event_id: role.event_id}))})));
}

async function verifySelected(page: Page, value: ReplayZoneReceipt, symbol: Symbol, mode: ReplayZoneMitigation,
  expected: Expected[], cutoff: number, expand: boolean) {
  const revisions = expected.filter(item => item.available_at <= cutoff);
  const latest = revisions.at(-1)!;
  verifyRevision(value, latest);
  expect(value.revisions).toHaveLength(revisions.length);
  revisions.forEach((item, index) => verifyRevision(value.revisions[index], item));
  verifyPinnedRows(value.evidence, symbol, cutoff);
  for (const [time, role] of latest.formation_refs) {
    expect(value.evidence.find(row => row.time === time)?.roles.some(item => item.role === role)).toBe(true);
  }
  const article = selected(page);
  await expect(article).toHaveAttribute('data-zone-id', value.zone_id);
  await expect(article).toHaveAttribute('data-revision-id', value.id);
  await expect(article).toHaveAttribute('data-available-at', String(value.available_at));
  await readFields(article.locator('dl[aria-label="Zone receipt"]'), {
    'Zone': latest.type === 'FVG' ? 'FVG' : 'Order block', 'Direction': latest.direction,
    'Bounds': Number(latest.lower) + ' – ' + Number(latest.upper),
    'Remaining bounds': Number(latest.remaining_lower) + ' – ' + Number(latest.remaining_upper),
    'State': words(latest.state), 'Reason': words(latest.reason), 'Mitigation': value.mitigation_pct + '% · ' + mode,
    'Revision': String(latest.revision), 'Anchor candle': utc(latest.anchor_time), 'Formation candle': utc(latest.formation_time),
    'Formation confirmed': utc(latest.formation_confirmation_time), 'First available': utc(latest.formation_available_at),
    'Revision observed': utc(latest.observation_time), 'Revision confirmed': utc(latest.confirmation_time),
    'Revision available': utc(latest.available_at), 'Algorithm version': String(value.algorithm_version),
    'Identity policy version': String(value.identity_policy_version), 'Zone ID': value.zone_id, 'Revision event ID': value.id,
  });
  await expect(article.locator('tbody tr')).toHaveCount(0);
  if (!expand) return;

  const history = article.locator('details').filter({hasText: /lifecycle revisions/});
  await history.locator('summary').click();
  const historyRows = history.getByRole('list', {name: 'Zone lifecycle revisions'}).locator('li');
  await expect(historyRows).toHaveText(revisions.map(item =>
    [utc(item.available_at), words(item.state), words(item.reason)].join(' · ') + ' · ' +
    value.revisions[item.revision - 1].mitigation_pct + '% · remaining ' +
    Number(item.remaining_lower) + ' – ' + Number(item.remaining_upper)));
  await history.locator('summary').click();

  const direct = article.locator('details').filter({hasText: /direct source candles/});
  await direct.locator('summary').click();
  await verifyTable(direct, 'Direct zone source candles', value.evidence, symbol);
  await direct.locator('summary').click();
  await expect(article.locator('tbody tr')).toHaveCount(0);

  const expectedBreak = latest.structure;
  if (!expectedBreak) {
    expect(value.structure).toBeNull();
    return;
  }
  const qualified = value.structure!;
  expect(qualified).toMatchObject({type: expectedBreak.type, side: expectedBreak.side,
    level_price: Number(expectedBreak.level_price), break_price: Number(expectedBreak.break_price),
    break_time: expectedBreak.break_time, confirmation_time: expectedBreak.confirmation_time, available_at: expectedBreak.available_at,
    pivot: {pivot_time: expectedBreak.pivot_time, available_at: expectedBreak.pivot_available_at,
      confirmation_bar_time: expectedBreak.pivot_confirmation_bar_time}});
  expect(qualified.broken_pivot_id).toBe(qualified.pivot.id);
  verifyPinnedRows(qualified.evidence, symbol, qualified.available_at);
  const proof = article.locator('details').filter({hasText: /Qualifying structure event/});
  await proof.locator(':scope > summary').click();
  const pivot = qualified.pivot;
  await readFields(proof.locator('dl[aria-label="Qualifying structure receipt"]'), {
    'Event': expectedBreak.type, 'Direction': latest.direction,
    'Broken level': String(Number(expectedBreak.level_price)), 'Level first available': utc(expectedBreak.pivot_available_at),
    'Crossing price': String(Number(expectedBreak.break_price)), 'Break candle': utc(expectedBreak.break_time),
    'Confirmed': utc(expectedBreak.confirmation_time), 'First available': utc(expectedBreak.available_at),
    'Structure event ID': qualified.id, 'Broken pivot ID': qualified.broken_pivot_id,
    'Pivot': words(pivot.side) + ' · ' + words(pivot.classification) + ' · ' + pivot.price,
    'Pivot candle': utc(expectedBreak.pivot_time), 'Pivot confirmed': utc(pivot.confirmation_time),
    'Pivot first available': utc(expectedBreak.pivot_available_at),
  });
  await expect(article.locator('tbody tr')).toHaveCount(0);
  const witness = proof.locator('details').filter({hasText: /qualifying structure source candles/});
  await witness.locator('summary').click();
  await verifyTable(witness, 'Qualifying structure source candles', qualified.evidence, symbol);
  await proof.locator(':scope > summary').click();
  await expect(article.locator('tbody tr')).toHaveCount(0);
}

function verifyList(result: ReplayZonePage, symbol: Symbol, mode: ReplayZoneMitigation, cutoff: number, type: 'FVG' | 'OB') {
  expect(result.symbol).toBe(symbol);
  const choices = result.zones.filter(item => item.type === type && item.formation_available_at === formation &&
    item.direction === 'BULLISH');
  if (cutoff < formation) {
    expect(choices).toHaveLength(0);
    return undefined;
  }
  expect(choices).toHaveLength(1);
  verifyRevision(choices[0], chain(symbol, mode, type).filter(item => item.available_at <= cutoff).at(-1)!);
  return choices[0].zone_id;
}

async function choose(page: Page, cutoff: number, view: View, mode: ReplayZoneMitigation, id: string) {
  const response = waitForZone(page, cutoff, view, mode, id);
  await zones(page).selectOption(id);
  const value = (await response).selected;
  expect(value?.zone_id).toBe(id);
  return value!;
}

async function verifyUnavailable(page: Page, id: string) {
  await expect(zones(page)).toHaveValue(id);
  await expect(zones(page).locator('option:checked')).toHaveText('Selected zone unavailable');
  await expect(panel(page).getByRole('status')).toHaveText('Selected zone unavailable at this cutoff.');
  await expect(selected(page)).toHaveCount(0);
}

test('pinned zones, prefix visibility, pane and policy isolation, obsolete response, and saved cutoff', async ({page, context, browser}) => {
  test.skip(baseline || reopen);
  test.setTimeout(180_000);
  await configurePair(page);
  const requests = monitorRequests(page, 'zones');
  const session = await startSession(page, 'EURUSD', 'GBPUSD');
  await seek(page, formation - frame);
  await expect(panel(page)).toHaveCount(0);
  expect(requests).toHaveLength(0);

  const earlier = waitForZone(page, formation - frame, 'primary', 'wick');
  await disclosure(page).click();
  verifyList(await earlier, 'EURUSD', 'wick', formation - frame, 'FVG');
  const firstList = waitForZone(page, formation, 'primary', 'wick');
  await button(replay(page), 'Next candle').click();
  const fvgId = verifyList(await firstList, 'EURUSD', 'wick', formation, 'FVG')!;
  const fvgFormation = await choose(page, formation, 'primary', 'wick', fvgId);
  await verifySelected(page, fvgFormation, 'EURUSD', 'wick', chain('EURUSD', 'wick', 'FVG'), formation, true);
  const fvgEnd = waitForZone(page, terminal, 'primary', 'wick', fvgId);
  await button(replay(page), 'Next candle').click();
  const fvgTerminal = (await fvgEnd).selected!;
  await verifySelected(page, fvgTerminal, 'EURUSD', 'wick', chain('EURUSD', 'wick', 'FVG'), terminal, true);
  expect(fvgTerminal.state).toBe('FULLY_MITIGATED');
  const fvgClose = waitForZone(page, terminal, 'primary', 'close');
  await mitigation(page).selectOption('close');
  await expect(zones(page)).toHaveValue('');
  const fvgCloseId = verifyList(await fvgClose, 'EURUSD', 'close', terminal, 'FVG')!;
  const fvgAlternate = await choose(page, terminal, 'primary', 'close', fvgCloseId);
  await verifySelected(page, fvgAlternate, 'EURUSD', 'close', chain('EURUSD', 'close', 'FVG'), terminal, false);
  expect(fvgAlternate.state).toBe('ACTIVE');
  const restoredWick = waitForZone(page, terminal, 'primary', 'wick', fvgId);
  await mitigation(page).selectOption('wick');
  expect((await restoredWick).selected).toEqual(fvgTerminal);
  await expect(selected(page)).toHaveAttribute('data-zone-id', fvgId);

  const beforePeer = waitForZone(page, terminal, '2', 'wick');
  await pane(page).selectOption('2');
  expect((await beforePeer).selected).toBeNull();
  const beforeOb = waitForZone(page, formation - frame, '2', 'wick');
  await seek(page, formation - frame);
  verifyList(await beforeOb, 'GBPUSD', 'wick', formation - frame, 'OB');
  const obBegin = waitForZone(page, formation, '2', 'wick');
  await button(replay(page), 'Next candle').click();
  const obId = verifyList(await obBegin, 'GBPUSD', 'wick', formation, 'OB')!;
  const obFormation = await choose(page, formation, '2', 'wick', obId);
  await verifySelected(page, obFormation, 'GBPUSD', 'wick', chain('GBPUSD', 'wick', 'OB'), formation, true);
  const obEnd = waitForZone(page, terminal, '2', 'wick', obId);
  await button(replay(page), 'Next candle').click();
  const obTerminal = (await obEnd).selected!;
  await verifySelected(page, obTerminal, 'GBPUSD', 'wick', chain('GBPUSD', 'wick', 'OB'), terminal, true);
  expect(obTerminal.state).toBe('FULLY_MITIGATED');

  const obClose = waitForZone(page, terminal, '2', 'close');
  await mitigation(page).selectOption('close');
  const obCloseId = verifyList(await obClose, 'GBPUSD', 'close', terminal, 'OB')!;
  const obAlternate = await choose(page, terminal, '2', 'close', obCloseId);
  await verifySelected(page, obAlternate, 'GBPUSD', 'close', chain('GBPUSD', 'close', 'OB'), terminal, false);
  expect(obAlternate.state).toBe('PARTIALLY_MITIGATED');
  const restoredOb = waitForZone(page, terminal, '2', 'wick', obId);
  await mitigation(page).selectOption('wick');
  expect((await restoredOb).selected).toEqual(obTerminal);

  const held = Promise.withResolvers<ReplayZonePage>();
  const release = Promise.withResolvers<void>();
  const delivered = Promise.withResolvers<void>();
  const pattern = '**/zones?*';
  const staleCutoff = terminal + frame;
  await page.route(pattern, async route => {
    const query = new URL(route.request().url()).searchParams;
    if (query.get('view') !== '2' || query.get('zone_id') !== obId || query.get('cutoff') !== String(staleCutoff)) {
      return route.continue();
    }
    try {
      const upstream = await route.fetch();
      expect(upstream.status()).toBe(200);
      held.resolve(await upstream.json());
      await release.promise;
      await route.fulfill({response: upstream});
      delivered.resolve();
    } catch (reason) {
      held.reject(reason);
      delivered.reject(reason);
    }
  });
  try {
    const stale = waitForZone(page, staleCutoff, '2', 'wick', obId);
    await seek(page, staleCutoff);
    expect((await held.promise).selected).toEqual(obTerminal);
    const missing = waitForZone(page, formation - frame, '2', 'wick', obId);
    await seek(page, formation - frame);
    expect((await missing).selected).toBeNull();
    await verifyUnavailable(page, obId);
    const primaryMissing = waitForZone(page, formation - frame, 'primary', 'wick', fvgId);
    await pane(page).selectOption('primary');
    expect((await primaryMissing).selected).toBeNull();
    await verifyUnavailable(page, fvgId);
    release.resolve();
    await delivered.promise;
    expect((await stale).selected).toEqual(obTerminal);
    await verifyUnavailable(page, fvgId);
    await pane(page).selectOption('2');
    await verifyUnavailable(page, obId);
  } finally {
    release.resolve();
    await page.unroute(pattern);
  }
  const recoveredOb = waitForZone(page, formation, '2', 'wick', obId);
  await button(replay(page), 'Next candle').click();
  expect((await recoveredOb).selected).toEqual(obFormation);
  await expect(selected(page)).toHaveAttribute('data-revision-id', obFormation.id);
  const endOb = waitForZone(page, end, '2', 'wick', obId);
  await button(replay(page), 'Last candle').click();
  expect((await endOb).selected).toEqual(obTerminal);
  const endFvg = waitForZone(page, end, 'primary', 'wick', fvgId);
  await pane(page).selectOption('primary');
  const final = await endFvg;
  expect(final.selected).toEqual(fvgTerminal);
  expect(final.zones.some(zone => zone.zone_id === fvgId)).toBe(false);
  const older = waitForZone(page, end, 'primary', 'wick', fvgId);
  await button(panel(page), 'Older replay zones').click();
  expect((await older).selected).toEqual(fvgTerminal);
  const latest = waitForZone(page, end, 'primary', 'wick', fvgId);
  await button(panel(page), 'Latest replay zones').click();
  expect((await latest).selected).toEqual(fvgTerminal);
  await pane(page).selectOption('2');
  await expect(selected(page)).toHaveAttribute('data-zone-id', obId);
  await button(replay(page), 'Save replay position').click();
  await expect(replay(page).getByText('Replay position saved', {exact: true})).toBeVisible();
  await context.storageState({path: stored('zones-journey-state.json')});

  const checkpoint: Saved = {session_id: session.id, cutoff: end,
    fvg: {formation: fvgFormation, terminal: fvgTerminal, alternate: fvgAlternate},
    ob: {formation: obFormation, terminal: obTerminal, alternate: obAlternate}};
  writeFileSync(stored('zones-journey-checkpoint.json'), JSON.stringify(checkpoint));

  await button(replay(page), 'Exit replay').click();
  await page.locator('.market-watchlist').getByRole('button', {name: /^GBPUSD/}).click();
  await setOppositePeer(page, 'EURUSD');
  const swapped = await startSession(page, 'GBPUSD', 'EURUSD');
  expect(swapped.id).not.toBe(session.id);
  await seek(page, formation);
  const swappedPrimary = waitForZone(page, formation, 'primary', 'wick');
  await disclosure(page).click();
  const swappedPrimaryId = verifyList(await swappedPrimary, 'GBPUSD', 'wick', formation, 'OB')!;
  await expect(zones(page)).toHaveValue('');
  const swappedOb = await choose(page, formation, 'primary', 'wick', swappedPrimaryId);
  await verifySelected(page, swappedOb, 'GBPUSD', 'wick', chain('GBPUSD', 'wick', 'OB'), formation, false);
  const swappedPeer = waitForZone(page, formation, '2', 'wick');
  await pane(page).selectOption('2');
  const swappedPeerId = verifyList(await swappedPeer, 'EURUSD', 'wick', formation, 'FVG')!;
  await expect(zones(page)).toHaveValue('');
  const swappedFvg = await choose(page, formation, '2', 'wick', swappedPeerId);
  await verifySelected(page, swappedFvg, 'EURUSD', 'wick', chain('EURUSD', 'wick', 'FVG'), formation, false);
  receipt('zones-journey', {...checkpoint, chromium: browser.version(), stale_delivery: 'ignored after prefix and pane changes',
    swapped_session_id: swapped.id, swapped_primary: swappedOb.zone_id, swapped_peer: swappedFvg.zone_id,
    pinned_sources: Object.fromEntries(Object.entries(reference.symbols).map(([symbol, lane]) =>
      [symbol, {csv_sha256: lane.csv_sha256, partition: lane.partition}]))});
});

test('restarts the normal Market Terminal with the saved pane, mode, and two terminal selections', async ({browser}) => {
  test.skip(baseline || !reopen);
  test.setTimeout(120_000);
  const saved: Saved = JSON.parse(readFileSync(stored('zones-journey-checkpoint.json'), 'utf8'));
  const {cutoff, ob, fvg} = saved;
  const context = await browser.newContext({storageState: stored('zones-journey-state.json'), serviceWorkers: 'block'});
  try {
    const page = await context.newPage();
    await openTerminal(page);
    const ready = waitForZone(page, cutoff, '2', 'wick', ob.terminal.zone_id);
    await select(page, 'Resume saved replay').selectOption(saved.session_id);
    expect((await ready).selected).toEqual(ob.terminal);
    await expect(replay(page).locator('output')).toContainText(iso(cutoff));
    await expect(pane(page)).toHaveValue('2');
    await expect(mitigation(page)).toHaveValue('wick');
    await verifySelected(page, ob.terminal, 'GBPUSD', 'wick', chain('GBPUSD', 'wick', 'OB'), cutoff, true);
    const article = selected(page);
    const proof = article.locator('details').filter({hasText: /Qualifying structure event/});
    await proof.locator(':scope > summary').click();
    await expect(proof.locator('dl[aria-label="Qualifying structure receipt"]')).toBeVisible();
    await expect(article.locator('tbody tr')).toHaveCount(0);
    await page.setViewportSize({width: 1536, height: 1280});
    await article.screenshot({path: artifact('zones-reopened-order-block.png'), animations: 'disabled'});

    const primary = waitForZone(page, cutoff, 'primary', 'wick', fvg.terminal.zone_id);
    await pane(page).selectOption('primary');
    expect((await primary).selected).toEqual(fvg.terminal);
    await verifySelected(page, fvg.terminal, 'EURUSD', 'wick', chain('EURUSD', 'wick', 'FVG'), cutoff, true);
    const missing = waitForZone(page, formation - frame, 'primary', 'wick', fvg.terminal.zone_id);
    await seek(page, formation - frame);
    expect((await missing).selected).toBeNull();
    await verifyUnavailable(page, fvg.terminal.zone_id);
    const recovered = waitForZone(page, formation, 'primary', 'wick', fvg.terminal.zone_id);
    await button(replay(page), 'Next candle').click();
    expect((await recovered).selected).toEqual(fvg.formation);
    await verifySelected(page, fvg.formation, 'EURUSD', 'wick', chain('EURUSD', 'wick', 'FVG'), formation, false);

    receipt('zones-journey-restart', {session_id: saved.session_id, saved_cutoff: cutoff, chromium: browser.version(),
      primary_zone_id: fvg.terminal.zone_id, peer_zone_id: ob.terminal.zone_id, rewind_before: formation - frame,
      recovered_formation: fvg.formation.id});
  } finally {
    await context.close();
  }
});
