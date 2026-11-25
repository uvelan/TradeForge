import {expect,test} from '@playwright/test';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';

test('saved lessons reopen with the same ordered archives and image after API restart',async({page,request})=>{
  const receipts=JSON.parse(readFileSync(process.env.TRADEFORGE_PAIR_RECEIPTS!,'utf8'));
  expect(receipts).toHaveLength(2);
  await page.goto('/');
  await page.getByRole('button',{name:'Learning Journal',exact:true}).click();
  for(const {meta,title,digest} of receipts){
    const item=page.locator('.journal-item').filter({hasText:title});
    await item.getByText('Chart capture details',{exact:true}).click();
    await expect(item.locator('details')).toContainText('Frozen chart cutoff');
    await expect(item.locator('details')).toContainText('2026-11-13T14:05:00.000Z');
    for(const [index,lane] of meta.lanes.entries()){
      const section=item.locator('details section').nth(index);
      for(const value of [lane.role,lane.symbol,lane.timeframe,lane.feed_id,lane.price_basis,
        lane.partitions[0].id,lane.partitions[0].sha256,String(lane.last_bar.close)])await expect(section).toContainText(value);
    }
    const link=item.getByRole('link',{name:'Open chart capture'});
    const url='/research/api/chart-snapshots/'+meta.id;
    await expect(link).toHaveAttribute('href',url+'/image');
    expect(await (await request.get(url)).json()).toEqual(meta);
    const image=await request.get(url+'/image');expect(image.status()).toBe(200);
    expect(createHash('sha256').update(await image.body()).digest('hex')).toBe(digest);
  }
  const {meta,digest}=JSON.parse(readFileSync(process.env.TRADEFORGE_PAIR_RECEIPTS+'.signal','utf8'));
  await page.getByRole('button',{name:'Signal Explorer',exact:true}).click();
  await page.getByLabel('Signal, dataset or trade ID').fill(meta.signal_reference.event_key);
  await page.getByRole('button',{name:'Search signals'}).click();await page.locator('.signal-results .signal-row').click();
  await page.getByLabel('Chart capture evidence').selectOption(meta.id);
  await page.getByText('Chart capture details',{exact:true}).click();
  const details=page.getByLabel('Evidence Inspector').locator('details').filter({hasText:'Chart capture details'});
  await expect(details).toContainText('Retrospective signal chart');
  await expect(details).toContainText('Signal transition: '+new Date(meta.signal_transition_at*1000).toISOString());
  await expect(details).toContainText('Latest plotted candle availability: '+new Date(meta.chart_evidence_through*1000).toISOString());
  for(const lane of meta.lanes)await expect(details).toContainText(lane.partitions[0].sha256);
  const href=await page.getByRole('link',{name:'Open saved chart capture'}).getAttribute('href');
  expect(href).toBe('/research/api/chart-snapshots/'+meta.id+'/image');
  expect(createHash('sha256').update(await (await request.get(href!)).body()).digest('hex')).toBe(digest);
  expect(await (await request.get('/research/api/chart-snapshots/'+meta.id)).json()).toEqual(meta);
});
