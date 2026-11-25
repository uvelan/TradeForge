import {createHash} from 'node:crypto';
import {writeFileSync} from 'node:fs';
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
  const current=await before.json();
  expect(current).toMatchObject({source,symbol:'EURUSD',timeframe:'M5',lanes:[
    {role:'primary',symbol:'EURUSD',partitions:[refs.EURUSD.partition]},
    {role:'comparison',symbol:'GBPUSD',partitions:[refs.GBPUSD.partition]}]});
  release.resolve();await delivered.promise;
  await page.evaluate(()=>new Promise<void>(done=>requestAnimationFrame(()=>requestAnimationFrame(()=>done()))));
  await pair('EURUSD','GBPUSD');await inspect('EURUSD','GBPUSD');
  await expect(page.getByRole('button',{name:'Record chart lesson'})).toBeVisible();
  const receipts=[];
  const cutoff=start+3900;
  for(const [primarySymbol,peerSymbol] of [['EURUSD','GBPUSD'],['GBPUSD','EURUSD']] as const){
    if(receipts.length){
      await click('Market Terminal');
      await page.locator('.market-watchlist button').filter({hasText:primarySymbol}).click();
      await compare.selectOption(peerSymbol);await pair(primarySymbol,peerSymbol);
    }
    await click('Bar replay');
    const replay=page.getByLabel('Historical replay');
    await replay.getByLabel('Jump to UTC').fill(day+'T14:05');
    await replay.getByRole('button',{name:'Seek',exact:true}).click();
    await expect(replay.locator('output')).toContainText(day+'T14:05:00.000Z');
    await pair(primarySymbol,peerSymbol,cutoff);
    const saved=page.waitForResponse(r=>r.url().endsWith('/chart-snapshots')&&r.request().method()==='POST');
    await save.click();const response=await saved;expect(response.status(),await response.text()).toBe(201);
    const meta=await response.json();
    expect(meta).toMatchObject({symbol:primarySymbol,timeframe:'M5',source,cutoff,lane_schema_version:1});
    for(const [i,symbol] of [primarySymbol,peerSymbol].entries()){
      const bars=refs[symbol].bars.filter(b=>b.time+300<=cutoff);
      expect(meta.lanes[i]).toMatchObject({symbol,role:i?'comparison':'primary',feed_id:feed,price_basis:'BID',timeframe:'M5',
        partitions:[refs[symbol].partition],last_bar:bars.at(-1),bar_times:bars.map(b=>b.time)});
      expect(meta.lanes[i].last_bar.close).toBe(symbol==='EURUSD'?1.10527:1.32318);
    }
    const image=await request.get('/research/api/chart-snapshots/'+meta.id+'/image');expect(image.ok()).toBe(true);
    const digest=createHash('sha256').update(await image.body()).digest('hex');
    expect(createHash('sha256').update(Buffer.from(response.request().postDataJSON().png,'base64')).digest('hex')).toBe(digest);
    await click('Record chart lesson');
    const title='Frozen '+primarySymbol+' '+peerSymbol;
    await page.getByLabel('Title',{exact:true}).fill(title);
    await page.getByLabel('Observation / explanation').fill('Verified paired chart at 14:05 UTC.');
    await click('Append to journal');
    await expect(page.locator('.journal-item').filter({hasText:title}).getByRole('link',{name:'Open chart capture'}))
      .toHaveAttribute('href','/research/api/chart-snapshots/'+meta.id+'/image');
    receipts.push({meta,title,digest});
  }
  expect(receipts[0].meta.id).not.toBe(receipts[1].meta.id);
  // Hold a genuine POST until its originating pair has been removed from the chart.
  await click('Market Terminal');await pair('GBPUSD','EURUSD');
  const submitted=Promise.withResolvers<void>(), finish=Promise.withResolvers<void>();
  await page.route('**/research/api/chart-snapshots',async route=>{
    submitted.resolve();await finish.promise;
    await route.fulfill({response:await route.fetch()});
  },{times:1});
  const pendingSave=page.waitForResponse(r=>r.url().endsWith('/chart-snapshots')&&r.request().method()==='POST');
  await save.click();await submitted.promise;await compare.selectOption('none');
  await expect(compare).toHaveValue('none');
  await expect.poll(async()=>(await read()).map(series=>series.data.map(ohlc))).toEqual([refs.GBPUSD.bars]);
  finish.resolve();
  const response=await pendingSave;expect(response.status()).toBe(201);
  const captured=await response.json();
  expect(captured.lanes.map((lane:{symbol:string})=>lane.symbol)).toEqual(['GBPUSD','EURUSD']);
  const originalPng=Buffer.from(response.request().postDataJSON().png,'base64');
  expect(await (await request.get('/research/api/chart-snapshots/'+captured.id+'/image')).body()).toEqual(originalPng);
  await expect(page.getByRole('button',{name:'Record chart lesson'})).toHaveCount(0);
  await click('Signal Explorer');
  const feedFor=(symbol:SymbolName)=>({source,symbol,feed_id:feed,price_basis:'BID',dataset_id:refs[symbol].partition.id});
  const detected=await request.post('/research/api/indicators/smt',{data:{timeframe:'M5',primary:refs.EURUSD.bars,
    comparison:refs.GBPUSD.bars,primary_feed:feedFor('EURUSD'),comparison_feed:feedFor('GBPUSD')}});
  expect(detected.ok()).toBe(true);
  const event=(await detected.json()).events.find((e:{confirmed_at:number})=>e.confirmed_at&&e.confirmed_at<cutoff);
  await page.getByLabel('Signal, dataset or trade ID').fill(event.event_key);await click('Search signals');
  await page.locator('.signal-results .signal-row').click();
  const inspector=page.getByLabel('Evidence Inspector');
  await click('Open recorded chart');
  const pendingSignal=page.waitForResponse(r=>r.url().endsWith('/chart-snapshots')&&r.request().method()==='POST');
  await inspector.getByRole('button',{name:'Save chart evidence'}).click();
  const posted=await pendingSignal;expect(posted.status(),await posted.text()).toBe(201);
  const meta=await posted.json();
  const latest=await (await request.get('/research/api/indicators/signals/'+event.event_key+'?status='+meta.signal_reference.status)).json();
  expect(meta).toMatchObject({cutoff:null,chart_context:'RETROSPECTIVE',signal_transition_at:latest.transitions.at(-1).time});
  expect(meta.chart_evidence_through).toBeGreaterThan(meta.signal_transition_at);
  for(const [i,symbol] of symbols.entries()){
    expect(meta.lanes[i].bar_times).toEqual(refs[symbol].bars.map(b=>b.time));
    expect(meta.lanes[i].partitions).toEqual([refs[symbol].partition]);
  }
  expect(meta.chart_evidence_through).toBe(Math.max(...symbols.map(s=>refs[s].bars.at(-1)!.time+300)));
  const digest=createHash('sha256').update(Buffer.from(posted.request().postDataJSON().png,'base64')).digest('hex');
  if(process.env.TRADEFORGE_PAIR_RECEIPTS)writeFileSync(process.env.TRADEFORGE_PAIR_RECEIPTS+'.signal',JSON.stringify({meta,digest}));
  if(process.env.TRADEFORGE_PAIR_RECEIPTS)writeFileSync(process.env.TRADEFORGE_PAIR_RECEIPTS,JSON.stringify(receipts));
});
