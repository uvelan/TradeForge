import {spawnSync} from 'node:child_process';
import {readFileSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {expect, type Locator, type Page} from '@playwright/test';
import type {ReplaySession} from '../src/types';

type Symbol = 'EURUSD' | 'GBPUSD';
export type View = 'primary' | '2';
export type Pin = {id: string; sha256: string};
export type Bar = Record<'open' | 'high' | 'low' | 'close', string | number> & {time: number; available_at: number};
type Reference = {date_utc: string; range: [number, number]; symbols: Record<Symbol, {
  csv_sha256: string; partition: Pin; bars: Bar[];
}>};

export const reference: Reference = JSON.parse(readFileSync(process.env.TRADEFORGE_T10_STRUCTURE_REFERENCE!, 'utf8'));
export const [start, end] = reference.range;
export const frame = 300;
export const baseline = process.env.TRADEFORGE_T10_ZONES_BASELINE === '1';
export const reopen = process.env.TRADEFORGE_PAIR_REOPEN === '1';
export const stored = (name: string) => join(process.env.TRADEFORGE_PAIR_STORAGE!, name);
export const artifact = (name: string) => join(process.env.TRADEFORGE_T10_CHART_OUTPUT!, name);
export function receipt(name: string, value: object) {
  const revision = spawnSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'});
  writeFileSync(artifact(name + '.json'), JSON.stringify({git_revision: revision.stdout?.trim() || null, ...value}, null, 2) + '\n');
}
export const iso = (time: number) => new Date(time * 1000).toISOString();
export const utc = (time: number) => iso(time).replace('T', ' ').replace('.000Z', ' UTC');
export const select = (scope: Page | Locator, name: string) => scope.getByRole('combobox', {name, exact: true});
export const button = (scope: Page | Locator, name: string) => scope.getByRole('button', {name, exact: true});
export const replay = (page: Page) => page.getByLabel('Historical replay', {exact: true});

export async function prepareSeek(page: Page, cutoff: number) {
  await replay(page).getByLabel('Jump to UTC', {exact: true}).fill(iso(cutoff).slice(0, 16));
}

export async function seek(page: Page, cutoff: number) {
  await prepareSeek(page, cutoff);
  await button(replay(page), 'Seek').click();
  await expect(replay(page).locator('output')).toContainText(iso(cutoff));
}

export async function openTerminal(page: Page) {
  await page.setViewportSize({width: 1536, height: 960});
  await page.goto('/?timezone=UTC');
  await button(page, 'Market Terminal').click();
}

export async function setOppositePeer(page: Page, peer: Symbol) {
  const chart = page.getByLabel('Chart view 2', {exact: true});
  await select(chart, 'View 2 instrument').selectOption(peer);
  await select(chart, 'View 2 timeframe').selectOption('M5');
}

export async function startSession(page: Page, primary: Symbol, peer: Symbol) {
  const created = page.waitForResponse(response => response.url().endsWith('/replay/sessions') &&
    response.request().method() === 'POST');
  await button(page, 'Bar replay').click();
  const response = await created;
  expect(response.status(), await response.text()).toBe(201);
  const session: ReplaySession = await response.json();
  expect(session.market.partitions).toEqual([reference.symbols[primary].partition]);
  expect(session.views['2']).toMatchObject({symbol: peer, partitions: [reference.symbols[peer].partition]});
  expect(session.market.candles.map(bar => bar.time)).toEqual(reference.symbols[primary].bars.map(bar => bar.time));
  expect(session.views['2'].candles.map(bar => bar.time)).toEqual(reference.symbols[peer].bars.map(bar => bar.time));
  return session;
}

export async function configurePair(page: Page) {
  await openTerminal(page);
  await select(page, 'Market data source').selectOption('DUKASCOPY_SAVED');
  await page.getByLabel('From UTC', {exact: true}).fill(reference.date_utc);
  await page.getByLabel('From time (UTC)', {exact: true}).fill(iso(start).slice(11, 16));
  await page.getByLabel('Until UTC (exclusive)', {exact: true}).fill(reference.date_utc);
  await page.getByLabel('Until time (UTC)', {exact: true}).fill(iso(end).slice(11, 16));
  await button(page, 'Load saved range').click();
  await select(page, 'Chart layout').selectOption('2');
  await setOppositePeer(page, 'GBPUSD');
}

export async function expectJson<T>(response: {status(): number; text(): Promise<string>; json(): Promise<unknown>}) {
  expect(response.status(), await response.text()).toBe(200);
  return await response.json() as T;
}

export function waitForEvidence<T>(page: Page, endpoint: string, cutoff: number, view: View, selection?: [string, string | null]) {
  return page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.pathname.endsWith('/' + endpoint) && url.searchParams.get('view') === view &&
      url.searchParams.get('cutoff') === String(cutoff) &&
      (selection === undefined || url.searchParams.get(selection[0]) === selection[1]);
  }).then(expectJson<T>);
}

export function monitorRequests(page: Page, endpoint: string) {
  const urls: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname.endsWith('/' + endpoint)) urls.push(request.url());
  });
  return urls;
}
