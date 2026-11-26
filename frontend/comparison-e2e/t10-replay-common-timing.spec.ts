import {expect, test} from '@playwright/test';
import {writeFileSync} from 'node:fs';

test('time the saved replay controls on the recorded paired session', async ({browser, request, baseURL}) => {
  test.setTimeout(180_000);
  const start = Date.parse('2026-11-13T00:00:00Z') / 1000;
  const path = '/research/api/replay/sessions';
  const made = await request.post(path, {data: {
    source: 'DUKASCOPY_SAVED', symbol: 'EURUSD', timeframe: 'M5', start, end: start + 22 * 3600,
    views: [{view: '2', symbol: 'GBPUSD', timeframe: 'M5'}],
  }});
  expect(made.status()).toBe(201);
  const session = await made.json();
  expect(session.market.candles).toHaveLength(264);
  expect(session.views['2'].candles).toHaveLength(264);
  const position = 167; // 14:00 UTC as-of
  expect((await request.post(path + '/' + session.id + '/checkpoint', {data: {position, bookmarks: []}})).ok()).toBeTruthy();
  const samples: Record<string, number[]> = {reopen: [], step: [], rewind: [], inspect: []};
  for (let cycle = 0; cycle < 8; cycle++) {
    const context = await browser.newContext({baseURL, viewport: {width: 1536, height: 960}});
    try {
      const page = await context.newPage();
      await page.goto('/?timezone=UTC');
      await page.getByRole('button', {name: 'Market Terminal', exact: true}).click();
      await page.getByRole('combobox', {name: 'Market data source', exact: true}).selectOption('DUKASCOPY_SAVED');
      const resume = page.getByRole('combobox', {name: 'Resume saved replay'});
      await expect(resume.locator('option[value="' + session.id + '"]')).toHaveCount(1);
      const replay = page.getByLabel('Historical replay', {exact: true});
      const panel = page.getByRole('region', {name: 'H1 replay evidence'});
      const article = panel.getByRole('article', {name: 'Selected H1 interval'});
      const action = async (key: string, cutoff: number, doAction: () => Promise<unknown>) => {
        const settled = page.waitForResponse(r => r.url().includes('/higher-timeframe?') &&
          new URL(r.url()).searchParams.get('cutoff') === String(cutoff) && r.status() === 200);
        const clock = performance.now();
        await doAction();
        await settled;
        await expect(article.getByText('12 source candles', {exact: true})).toBeVisible();
        samples[key].push(performance.now() - clock);
      };
      let cutoff = start + 14 * 3600;
      await action('reopen', cutoff, () => resume.selectOption(session.id));
      for (const direction of ['step', 'rewind']) {
        for (let j = 0; j < 3; j++) {
          cutoff += direction === 'step' ? 300 : -300;
          await action(direction, cutoff, () => replay.getByRole('button', {name:
            direction === 'step' ? 'Next candle' : 'Previous candle', exact: true}).click());
        }
      }
      const clock = performance.now();
      await article.locator('summary').click();
      await expect(article.getByRole('table').locator('tbody tr')).toHaveCount(12);
      samples.inspect.push(performance.now() - clock);
    } finally { await context.close(); }
  }
  writeFileSync(process.env.TRADEFORGE_UI_TIMINGS!, JSON.stringify({session_id: session.id, samples}, null, 2));
});
