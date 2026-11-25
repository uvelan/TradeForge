import {expect,test} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';

test('restore paired watch and inspect the original confirmation',async({page})=>{
  test.setTimeout(90000);
  const lanes=JSON.parse(readFileSync(resolve('../docs/implementation/evidence/TF-I10A1-dukascopy-nov13/pin.json'),'utf8')).run.selection.series;
  await page.goto('/');
  await page.getByRole('button',{name:'Data Library',exact:true}).click();
  if(process.env.TRADEFORGE_PAIR_REOPEN!=='1'){
    for(const [symbol,key] of [['EURUSD','PRIMARY:M5'],['GBPUSD','COMPARISON:M5']]){
      await page.getByLabel(symbol+' M5 BID partition').selectOption(lanes[key].dataset_id);
    }
    const panel=page.locator('section.card').filter({has:page.getByRole('heading',{name:'Historical SMT watch'})});
    await panel.getByLabel('Start UTC').fill('2026-11-13T12:15');
    await panel.getByLabel('End UTC').fill('2026-11-13T13:40');
    await panel.getByRole('button',{name:'Watch exact M5 pairs'}).click();
  }
  await expect(page.getByText(/EURUSD \/ GBPUSD · COMPLETE · Verified through 2026-11-13T13:40/)).toBeVisible({timeout:45000});
  await page.getByRole('button',{name:'Signal Explorer',exact:true}).click();
  const family=page.locator('.signal-results li').filter({hasText:'bearish'}).filter({hasText:'Invalidated'}).first();
  await family.getByRole('button',{name:/^Confirmed ·/}).click();
  const inspector=page.getByRole('complementary',{name:'Evidence Inspector'});
  await expect(inspector).toContainText('7e9485b627eb7c327f80ae78f4e524920c33ef6ffe6802ecdd76397c53985e43');
  for(const key of ['PRIMARY:M5','COMPARISON:M5']){
    const pin=lanes[key].partitions[0];
    await inspector.getByText('Dataset '+pin.id.slice(0,12)).click();
    await expect(inspector.getByText(pin.sha256,{exact:false})).toBeVisible();
  }
  const loaded=page.waitForResponse(r=>r.url().includes('/candles?status=SMT_CONFIRMED'));
  await inspector.getByRole('button',{name:'Open recorded chart'}).click();
  const candles=await (await loaded).json();
  for(const side of ['primary','comparison'])expect(candles[side].at(-1).time+300).toBe(Date.parse('2026-11-13T13:25:00Z')/1000);
  await expect(inspector.getByRole('button',{name:'Recorded chart loaded'})).toBeVisible();
});
