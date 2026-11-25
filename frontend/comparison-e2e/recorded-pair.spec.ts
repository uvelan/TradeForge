import {execFileSync} from 'node:child_process';
import {resolve} from 'node:path';
import {expect,test} from '@playwright/test';
import {installProbe} from '../e2e/workspace-probe';

type SymbolName='EURUSD'|'GBPUSD';
type Bar={time:number;open:number;high:number;low:number;close:number};
type Lane={partition:{id:string;sha256:string};bars:Bar[];window:Bar[]};
const ohlc=({time,open,high,low,close}:Bar)=>({time,open,high,low,close});
const source='DUKASCOPY_SAVED',feed='dukascopy-web-m1-v1';

test('verified local M5 comparison follows the selected instruments and frozen cutoff',async({page,request})=>{
  test.setTimeout(90000);
  const day=process.env.TRADEFORGE_COMPARISON_DAY!;
  const refs:Record<SymbolName,Lane>=JSON.parse(execFileSync(resolve('../.venv/Scripts/python.exe'),
    ['-m','scripts.u01_comparison_archive',day],{cwd:resolve('..'),encoding:'utf8'}));
  const start=Date.parse(day+'T13:00:00Z')/1000;
  const symbols:SymbolName[]=['EURUSD','GBPUSD'];
  for(const symbol of symbols){
    const res=await request.get('/research/api/market/candles',{params:{source,symbol,timeframe:'M5',start,end:start+7200}});
    expect(res.status()).toBe(200);
    const body=await res.json();
    expect(body).toMatchObject({source,synthetic:false,feed_id:feed,price_basis:'BID',partitions:[refs[symbol].partition]});
    expect(body.candles.map(ohlc)).toEqual(refs[symbol].window);
  }
  expect(refs.EURUSD.window.map(b=>b.time)).toEqual(refs.GBPUSD.window.map(b=>b.time));
  expect(refs.EURUSD.window[12].close).not.toBe(refs.GBPUSD.window[12].close);
  await installProbe(page);
  await page.addInitScript(()=>{
    if(!localStorage.getItem('tradeforge.workspace.source')){
      for(const [key,value] of Object.entries({source:'DUKASCOPY_SAVED',timezone:'UTC',timeframe:'M5'}))
        localStorage.setItem('tradeforge.workspace.'+key,value);
    }
  });
  await page.setViewportSize({width:1536,height:900});
  const click=(name:string)=>page.getByRole('button',{name,exact:true}).click();
  const compare=page.getByRole('combobox',{name:'SMT comparison'});
  const read=()=>page.evaluate(()=>window.workspaceProbe.comparisonSeries());
  async function pair(a:SymbolName,b:SymbolName,cutoff=Infinity){
    await expect.poll(async()=>(await read()).map(series=>series.data.map(ohlc))).toEqual(
      [a,b].map(symbol=>refs[symbol].bars.filter(bar=>bar.time+300<=cutoff)));
    await expect(compare).toHaveValue(b);
    const series=await read();expect(series[1].title).toBe(b);
  }
  const legend=page.getByLabel('Chart data',{exact:true});
  async function inspect(a:SymbolName,b:SymbolName){
    await page.locator('.primary-chart .chart-input').scrollIntoViewIfNeeded();
    await page.evaluate(t=>window.workspaceProbe.viewRange(t-300,t+7200),start);
    for(const index of [0,12,23]){
      const stamp=refs[a].window[index].time;
      await expect.poll(async()=>{
        const xy=await page.evaluate(t=>window.workspaceProbe.point(t),stamp);
        await page.mouse.move(xy.x-1,xy.y);await page.mouse.move(xy.x,xy.y);
        return legend.locator('.chart-legend-meta').first().innerText();
      }).toContain(new Date(stamp*1000).toISOString().slice(11,16)+' \u00b7 UTC');
      await expect(legend.locator('.chart-legend-meta').first()).toContainText(day.slice(8)+' Nov '+day.slice(0,4));
      for(const [i,symbol] of [a,b].entries()){
        const row=legend.locator('.chart-legend-row').nth(i),bar=refs[symbol].window[index];
        await expect(row.locator('strong')).toHaveText(symbol);
        await expect(row.locator('span')).toHaveText((['open','high','low','close'] as const)
          .map(k=>k[0].toUpperCase()+' '+bar[k].toFixed(5)));
      }
    }
  }
  function nextArchive(symbol:SymbolName){
    return page.waitForResponse(r=>{
      const url=new URL(r.url());
      return url.pathname.endsWith('/market/candles') && url.searchParams.get('symbol')===symbol
        && url.searchParams.get('source')===source && url.searchParams.get('timeframe')==='M5' && url.searchParams.get('limit')==='600';
    });
  }
  async function checkLoad(pending:ReturnType<typeof nextArchive>,symbol:SymbolName){
    const response=await pending;expect(response.status()).toBe(200);
    const body=await response.json();
    expect(body).toMatchObject({source,synthetic:false,price_basis:'BID',feed_id:feed,partitions:[refs[symbol].partition]});
    expect(body.candles.map(ohlc)).toEqual(refs[symbol].bars);
  }
  await page.goto('/');
  const primary=nextArchive('EURUSD');await click('Market Terminal');await checkLoad(primary,'EURUSD');
  const comparison=nextArchive('GBPUSD');await compare.selectOption('GBPUSD');await checkLoad(comparison,'GBPUSD');await pair('EURUSD','GBPUSD');await inspect('EURUSD','GBPUSD');
  await page.getByLabel('Comparison placement',{exact:true}).selectOption('New pane');
  await pair('EURUSD','GBPUSD');await inspect('EURUSD','GBPUSD');
  await page.locator('.market-watchlist button').filter({hasText:'GBPUSD'}).click();
  await compare.selectOption('EURUSD');await pair('GBPUSD','EURUSD');await inspect('GBPUSD','EURUSD');
  await click('Trade Planner');await click('Market Terminal');await pair('GBPUSD','EURUSD');

  const save=page.getByRole('button',{name:'Save chart evidence'});
  const held=Promise.withResolvers<void>(), release=Promise.withResolvers<void>(), delivered=Promise.withResolvers<void>();
  await compare.selectOption('none');
  await page.route('**/research/api/market/candles?*',async route=>{
    const q=new URL(route.request().url()).searchParams;
    if(q.get('symbol')!=='EURUSD'||q.get('source')!==source)return route.continue();
    const response=await route.fetch();expect(response.ok()).toBe(true);
    held.resolve();await release.promise;await route.fulfill({response});delivered.resolve();
  },{times:1});
  await compare.selectOption('EURUSD');await held.promise;
  await expect(save).toHaveCount(0);
  await page.locator('.market-watchlist button').filter({hasText:'EURUSD'}).click();
  await compare.selectOption('GBPUSD');await pair('EURUSD','GBPUSD');
  const savedNow=page.waitForResponse(r=>r.url().endsWith('/chart-snapshots')&&r.request().method()==='POST');
  await save.click();
  const before=await savedNow;expect(before.status()).toBe(201);
  expect(await before.json()).toMatchObject({source,symbol:'EURUSD',timeframe:'M5'});
  release.resolve();await delivered.promise;
  await page.evaluate(()=>new Promise<void>(done=>requestAnimationFrame(()=>requestAnimationFrame(()=>done()))));
  await pair('EURUSD','GBPUSD');await inspect('EURUSD','GBPUSD');
  await expect(page.getByRole('button',{name:'Record chart lesson'})).toBeVisible();
  await click('Bar replay');
  const replay=page.getByLabel('Historical replay');
  const cutoff=start+3900;
  await replay.getByLabel('Jump to UTC').fill(day+'T14:05');await replay.getByRole('button',{name:'Seek',exact:true}).click();
  await expect(replay.locator('output')).toContainText(day+'T14:05:00.000Z');
  await pair('EURUSD','GBPUSD',cutoff);
  const saved=page.waitForResponse(r=>r.url().endsWith('/chart-snapshots')&&r.request().method()==='POST');
  await save.click();const response=await saved;expect(response.status()).toBe(201);
  const meta=await response.json();
  expect(meta).toMatchObject({symbol:'EURUSD',timeframe:'M5',source,cutoff});
  expect((await request.get('/research/api/chart-snapshots/'+meta.id+'/image')).ok()).toBe(true);
});
