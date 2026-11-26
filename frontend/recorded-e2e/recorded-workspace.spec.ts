import {expect,test} from '@playwright/test';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {installProbe} from '../e2e/workspace-probe';

const directory=resolve('../storage/t06-input');
const source='DUKASCOPY_SAVED',feed='dukascopy-bi5-v2';
const pins=[
  ['2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014','55e8bd216f5d2b9e2b130ddedd0579dce0aa54fd561cfb6e86a633228a127b0d','a6a0d7ee85e62b4947e9b0735d31ed4985871d0cb90d2efb8c621f7d14b63e41'],
  ['9d40aa3147d93e6daa2218933877adb9471dc02ff08b19fc0d272f1eb781688f','42d67cb27e0d1b3554cd2f787b4d3268f8fdd9b022a520e2ce68477a2b1f6ca4','e577e7e92bf826da9fdd2ba3ea791f20a858da732ba864d3984a21bf67a87626'],
];
type Bar={time:number;open:number;high:number;low:number;close:number};
function points(price:number){
  const units=Math.round(price*100000);
  if(price!==units/100000)throw new Error('Off-grid price '+price);
  return units;
}
const units=(bar:Bar)=>({time:bar.time,open:points(bar.open),high:points(bar.high),
  low:points(bar.low),close:points(bar.close)});
function verified(hour:number,pin:string){
  const raw=readFileSync(resolve(directory,hour+'h_ticks.bi5'));
  expect(createHash('sha256').update(raw).digest('hex'),'BI5 checksum '+hour).toBe(pin);
  return raw;
}

test('pinned recorded EURUSD ticks reach the chart, frozen replay and saved evidence',async({page,request})=>{
  test.setTimeout(400000);
  for(const [index,[pin,id,sha256]] of pins.entries()){
    const hour=index+13, raw=verified(hour,pin);
    expect(()=>verified(hour,'0'.repeat(64))).toThrow(/BI5 checksum/);
    const imported=await request.post('/research/api/market/import/bi5?symbol=EURUSD&day=2025-11-13&hour='+hour,
      {multipart:{file:{name:hour+'h_ticks.bi5',mimeType:'application/octet-stream',buffer:raw}}});
    expect(imported.ok(),await imported.text()).toBeTruthy();
    const receipt=await imported.json();
    expect(receipt.source.sha256).toBe(pin);
    expect(receipt.candle_datasets).toContainEqual(expect.objectContaining({timeframe:'M5',price_basis:'BID',id,sha256}));
  }
  const {bars}=JSON.parse(execFileSync(resolve('../.venv/Scripts/python.exe'),
    ['-m','scripts.u01_recorded_quotes','--source',directory],{cwd:resolve('..'),encoding:'utf8'})) as {bars:Bar[]};
  const partitions=pins.map(([,id,sha256])=>({id,sha256}));
  const start=Date.parse('2025-11-13T13:00:00Z')/1000, end=start+7200;
  const api='/research/api/market/candles?symbol=EURUSD&timeframe=M5&source='+source;
  for(const [from,to] of [[start,end],[start+3300,start+3600],[start+3600,start+3900]]){
    const res=await request.get(api+'&start='+from+'&end='+to);
    expect(res.status()).toBe(200);
    const body=await res.json();
    expect(body).toMatchObject({source,synthetic:false,price_basis:'BID',feed_id:feed,coverage:{start:from,end:to,gaps:[]}});
    expect(body.partitions).toEqual(partitions.filter((_,i)=>from<start+(i+1)*3600&&to>start+i*3600));
    expect(body.candles.map(units)).toEqual(bars.filter(b=>from<=b.time&&b.time<to));
  }
  await installProbe(page);
  await page.addInitScript(()=>{
    if(localStorage.getItem('tradeforge.workspace.source')===null){
      for(const [k,v] of Object.entries({source:'DUKASCOPY_SAVED',timeframe:'M5',timezone:'UTC',
        'link-timeframe':'on','link-instrument':'on'}))localStorage.setItem('tradeforge.workspace.'+k,v);
    }
  });
  await page.setViewportSize({width:1536,height:900});
  const failures:string[]=[];
  page.on('pageerror',e=>failures.push(e.message));
  page.on('request',r=>{
    const url=new URL(r.url());
    if(url.pathname.endsWith('/market/candles') && ![source,'MT5_SAVED'].includes(url.searchParams.get('source')??''))
      failures.push('Unexpected chart candle source '+url.searchParams.get('source'));
  });
  const returned=page.waitForResponse(r=>r.url().includes('/market/candles?')&&r.url().includes(source)&&r.url().includes('timeframe=M5'));
  await page.goto('/');
  const click=(name:string)=>page.getByRole('button',{name,exact:true}).click();
  await click('Market Terminal');
  const data=await (await returned).json();
  expect(data).toMatchObject({source,synthetic:false,price_basis:'BID',feed_id:feed,partitions});
  expect(data.candles.map(units)).toEqual(bars);
  const read=async()=> (await page.evaluate(()=>window.workspaceProbe.bars()) as Bar[][]).map(rows=>rows.map(units));
  await expect.poll(read).toEqual([bars]);
  await page.locator('.primary-chart').getByRole('button',{name:'Fullscreen chart'}).click();
  for(const i of [0,12,23]){
    const bar=bars[i];

    const legend=page.getByLabel('Chart data',{exact:true});
    await expect.poll(async()=>{
      const xy=await page.evaluate(t=>window.workspaceProbe.point(t),bar.time);
      await page.mouse.move(xy.x-2,xy.y);await page.mouse.move(xy.x,xy.y);
      return legend.locator('.chart-legend-meta').innerText();
    }).toContain(new Date(bar.time*1000).toISOString().slice(11,16)+' \u00b7 UTC');
    await expect(legend).toContainText('13 Nov 2025');
    const fields=legend.locator('.chart-legend-row span');
    await expect(fields).toHaveText((['open','high','low','close'] as const).map(k=>k[0].toUpperCase()+' '+(bar[k]/1e5).toFixed(5)));
  }
  await click('Exit fullscreen');
  const evaluated=page.waitForResponse(r=>r.url().endsWith('/indicators/evaluate')&&r.request().postDataJSON().indicators.some((d:{id:string})=>d.id==='ema'));
  await page.getByRole('combobox',{name:'Indicator',exact:true}).selectOption('ema');
  const indicator=await evaluated;expect(indicator.status()).toBe(200);
  expect(indicator.request().postDataJSON()).toMatchObject({source,feed_id:feed,price_basis:'BID',partitions});
  const selector=page.getByLabel('Market data source');
  const unavailable=page.waitForResponse(r=>r.url().includes('/market/candles?')&&r.url().includes('MT5_SAVED'));
  await selector.selectOption('MT5_SAVED');
  expect((await unavailable).status()).toBe(404);
  await expect.poll(read).toEqual([]);
  const restored=page.waitForResponse(r=>r.url().includes('/market/candles?')&&r.url().includes(source));
  await selector.selectOption(source);
  expect(await (await restored).json()).toMatchObject({source,synthetic:false,price_basis:'BID',feed_id:feed,partitions});
  for(let cycle=0;cycle<100;cycle++){
    const count=[1,2,4][cycle%3];
    await page.getByLabel('Chart layout').selectOption(String(count));
    await expect.poll(read).toEqual(Array(count).fill(bars));
    await page.locator('.primary-chart .chart-input').scrollIntoViewIfNeeded();
    await page.locator('.primary-chart .chart-input').hover();
    await page.mouse.wheel(0,-80);
    await click('Trade Planner');
    await expect.poll(()=>page.evaluate(()=>window.workspaceProbe.counts)).toEqual({charts:0,subscriptions:0,observers:0,listeners:0,timers:0,frames:0});
    await click('Market Terminal');
    if(cycle===2)await page.reload();
    await expect(selector).toHaveValue(source);
    await expect.poll(read).toEqual(Array(count).fill(bars));
    await expect(page.getByRole('combobox',{name:'Indicator',exact:true})).toHaveValue('ema');
  }
  await click('Bar replay');
  const replay=page.getByLabel('Historical replay');
  await replay.getByRole('button',{name:'Restart'}).click();
  await expect(replay.locator('output')).toContainText('13:05:00.000Z');
  await expect.poll(read).toEqual([bars.slice(0,1)]);
  const saved=page.waitForResponse(r=>r.url().endsWith('/chart-snapshots')&&r.request().method()==='POST');
  await click('Save chart evidence');
  const response=await saved;expect(response.status()).toBe(201);
  const snapshot=await response.json();
  expect(snapshot).toMatchObject({source,symbol:'EURUSD',timeframe:'M5',cutoff:start+300});
  await replay.getByRole('button',{name:'Next candle'}).click();
  await expect.poll(read).toEqual([bars.slice(0,2)]);
  await click('Trade Planner');await click('Market Terminal');
  const image=await request.get('/research/api/chart-snapshots/'+snapshot.id+'/image');
  expect(image.status()).toBe(200);
  expect(failures).toEqual([]);
});
