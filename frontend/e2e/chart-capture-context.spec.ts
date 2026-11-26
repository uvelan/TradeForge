import {expect, test} from '@playwright/test';

for (const scenario of ['timeframe', 'same-key return', 'source', 'comparison error', 'out-of-order', 'replay cutoff'] as const) {
  test(`chart evidence ignores a pending response after a ${scenario} change`, async ({page}) => {
    const firstSave = Promise.withResolvers<{timeframe:string;source:string;cutoff?:number}>();
    const release = Promise.withResolvers<void>();
    let posted = 0;
    await page.route('**/research/api/chart-snapshots', async route => {
      if (++posted !== 1) return route.continue();
      firstSave.resolve(route.request().postDataJSON());
      if(scenario === 'comparison error') {
        await release.promise;
        return route.fulfill({status: 422, json: {detail:'Obsolete chart save error'}});
      }
      const response = await route.fetch();
      await release.promise;
      return route.fulfill({response});
    });
    if (scenario === 'source') {
      await page.route('**/research/api/market/candles?*', route => {
        const params = new URL(route.request().url()).searchParams;
        if (params.get('source') !== 'DUKASCOPY_SAVED') return route.continue();
        const candles = Array.from({length:10}, (_, i)=>({
          time:1_700_000_100+i*300, open:1.1, close:1.1, high:1.2, low:1.0,
        }));
        return route.fulfill({json: {symbol:'EURUSD', timeframe:'M5', source:'DUKASCOPY_SAVED',
          price_basis:'BID', feed_id:'capture-fixture', candles}});
      });
    }

    await page.goto('/');
    await page.getByRole('button', {name:'Market Terminal', exact:true}).click();
    const source = page.getByRole('combobox', {name:'Market data source'});
    await source.selectOption('DEMO');
    const m5 = page.getByRole('button', {name:'M5', exact:true});
    const m15 = page.getByRole('button', {name:'M15', exact:true});
    const save = page.getByRole('button', {name:'Save chart evidence'});
    const lesson = page.getByRole('button', {name:'Record chart lesson'});
    await m5.click();
    if (scenario === 'replay cutoff') {
      await page.getByRole('button', {name:'Bar replay', exact:true}).click();
      await expect(page.getByLabel('Historical replay')).toBeVisible();
    }
    await save.click();
    const original = await firstSave.promise;
    expect(original).toMatchObject({source:'DEMO', timeframe:'M5'});

    if (scenario === 'source') {
      await source.selectOption('DUKASCOPY_SAVED');
      await expect(page.locator('.terminal-pair')).toContainText('DUKASCOPY SAVED');
    } else if (scenario === 'comparison error') {
      await page.getByRole('combobox', {name:'SMT comparison'}).selectOption('GBPUSD');
      await expect(page.getByLabel('Chart data',{exact:true})).toContainText('GBPUSD');
    } else if (scenario === 'replay cutoff') {
      expect(original.cutoff).toBeGreaterThan(0);
      await page.getByLabel('Historical replay').getByRole('button', {name:'Next candle'}).click();
      await expect(save).toBeEnabled();
    } else {
      await m15.click();
      await expect(page.locator('.primary-chart .pane-label')).toContainText('M15');
      await expect(save).toBeVisible();
      if (scenario === 'same-key return') {
        await m5.click();
        await expect(page.locator('.primary-chart .pane-label')).toContainText('M5');
      }
    }

    let currentId: string | undefined;
    if (scenario === 'out-of-order' || scenario === 'replay cutoff') {
      const latest = page.waitForResponse(r=>r.ok() && r.url().endsWith('/chart-snapshots'));
      await save.click();
      const response = await latest;
      if (scenario === 'replay cutoff') {
        expect(response.request().postDataJSON().cutoff).toBeGreaterThan(original.cutoff!);
      }
      currentId = (await response.json()).id;
      await expect(lesson).toBeVisible();
    }

    const settled = page.waitForResponse(r=>r.url().endsWith('/chart-snapshots') && r.request().method()==='POST');
    release.resolve();
    const obsolete = await settled;
    await obsolete.finished();
    if (scenario !== 'comparison error') {
      expect(obsolete.ok()).toBeTruthy();
      const {id} = await obsolete.json();
      expect(id).toBeTruthy();
      if (currentId) expect(id).not.toBe(currentId);
      expect((await page.request.get(`/research/api/chart-snapshots/${id}/image`)).ok()).toBeTruthy();
    }
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    if (currentId) {
      await page.getByRole('button', {name:'Create trade plan'}).click();
      await expect(page.getByRole('link', {name:'Open linked chart capture'}))
        .toHaveAttribute('href', `/research/api/chart-snapshots/${currentId}/image`);
    } else {
      await expect(lesson).toHaveCount(0);
      await expect(page.getByText('Obsolete chart save error')).toHaveCount(0);
    }

    if (scenario === 'comparison error') {
      await save.click();
      await expect(lesson).toBeVisible();
      await page.getByRole('combobox', {name:'SMT comparison'}).selectOption('none');
      await expect(lesson).toHaveCount(0);
      await expect(save).toBeEnabled();
      await expect(page.getByText('Chart capture saved')).toHaveCount(0);
    }

    if (scenario === 'timeframe') {
      const current = page.waitForResponse(r=>r.ok() && r.url().endsWith('/chart-snapshots'));
      await save.click();
      const response = await current;
      expect(response.request().postDataJSON()).toMatchObject({source:'DEMO', timeframe:'M15'});
      const {id} = await response.json();
      await expect(lesson).toBeVisible();
      await lesson.click();
      await expect(page.getByRole('link', {name:'Open attached chart capture'}))
        .toHaveAttribute('href', `/research/api/chart-snapshots/${id}/image`);
    }
  });
}

test('returning to a pending comparison cannot save the older loaded pane', async ({page}) => {
  const secondPane = Promise.withResolvers<void>();
  const returnedPane = Promise.withResolvers<void>();
  const releaseSecond = Promise.withResolvers<void>();
  const releaseReturn = Promise.withResolvers<void>();
  let reentering = false;
  await page.route('**/research/api/market/candles?*', async route => {
    const params = new URL(route.request().url()).searchParams;
    if (params.get('symbol') === 'USDJPY') {
      secondPane.resolve();
      await releaseSecond.promise;
    }
    if (params.get('symbol') === 'GBPUSD' && reentering) {
      returnedPane.resolve();
      await releaseReturn.promise;
    }
    return route.continue();
  });
  await page.goto('/');
  await page.getByRole('button', {name:'Market Terminal', exact:true}).click();
  await page.getByRole('combobox', {name:'Market data source'}).selectOption('DEMO');
  const compare = page.getByRole('combobox', {name:'SMT comparison'});
  const save = page.getByRole('button', {name:'Save chart evidence'});
  await compare.selectOption('GBPUSD');
  await expect(page.getByLabel('Chart data',{exact:true})).toContainText('GBPUSD');
  await expect(save).toBeEnabled();
  await compare.selectOption('USDJPY');
  await secondPane.promise;
  reentering = true;
  await compare.selectOption('GBPUSD');
  await returnedPane.promise;
  await expect(save).toHaveCount(0);
  releaseReturn.resolve();
  await expect(save).toBeEnabled();
  await expect(page.getByLabel('Chart data',{exact:true})).toContainText('GBPUSD');
  releaseSecond.resolve();
  await save.click();
  await expect(page.getByRole('button', {name:'Record chart lesson'})).toBeVisible();
});
