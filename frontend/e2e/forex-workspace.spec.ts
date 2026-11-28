import {expect,test} from '@playwright/test';

test('Forex workspace compares UTC M5 pivot opens and reports missing peer evidence',async({page})=>{
  const start=Date.UTC(2026,10,13,13)/1000;
  const primary=[10,11,14,11,10,11,16,11,10,11,12,11];
  const aligned=[22,24,29,24,22,24,27,24,22,24,23,22];
  const shifted=[22,24,29,24,22,24,24,27,22,24,23,22];
  let peer=aligned;
  let missing=false;
  await page.route('**/research/api/market/candles?*',route=>{
    const params=new URL(route.request().url()).searchParams;
    if(params.get('source')!=='DEMO')return route.continue();
    const symbol=params.get('symbol');
    const candles=(symbol==='EURUSD'?primary:peer).map((v,i)=>({
      time:start+300*i,open:v,high:v+1,low:v-1,close:v,
    })).filter((_,i)=>symbol==='EURUSD'||!missing||i!==5);
    return route.fulfill({json:{symbol,timeframe:'M5',source:'DEMO',synthetic:true,price_basis:'BID',candles}});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Market Terminal',exact:true}).click();
  await page.getByLabel('Market data source').selectOption('DUKASCOPY_SAVED');
  await expect(page.getByLabel('SMT comparison',{exact:true})).toHaveValue('GBPUSD');
  await page.getByLabel('Market data source').selectOption('DEMO');
  await page.getByLabel('SMT comparison',{exact:true}).selectOption('GBPUSD');
  await page.getByText('SMT rules',{exact:true}).click();
  await expect(page.getByLabel('UTC match (bars)')).toHaveValue('0');
  await page.getByLabel('Swing width').fill('2');
  await expect(page.getByText('1 SMT divergence in paired candles',{exact:true})).toBeVisible();
  peer=shifted;
  await page.getByRole('button',{name:'Refresh SMT data'}).click();
  await expect(page.getByText('No SMT divergence in the paired candles at this cutoff')).toBeVisible();
  missing=true;
  await page.getByRole('button',{name:'Refresh SMT data'}).click();
  await expect(page.getByText('Missing peer candle · GBPUSD · 2026-11-13T13:25:00.000Z')).toBeVisible();
  await page.getByLabel('UTC match (bars)').fill('1');
  await page.reload();
  await page.getByText('SMT rules',{exact:true}).click();
  await expect(page.getByLabel('UTC match (bars)')).toHaveValue('1');
});
