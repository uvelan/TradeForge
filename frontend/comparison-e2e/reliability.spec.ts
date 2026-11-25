import {expect,test,type APIRequestContext,type Page} from '@playwright/test';
import {createHash} from 'node:crypto';
import {spawn} from 'node:child_process';
import {existsSync,readFileSync,writeFileSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {installProbe} from '../e2e/workspace-probe';

type Bar={time:number;open:number;high:number;low:number;close:number};
type Dataset={id:string;symbol:string;sha256:string;kind:string;price_basis:string;timeframe:string;feed_id:string};
const input=JSON.parse(readFileSync(process.env.TRADEFORGE_DRILL_INPUT!,'utf8')) as {
  paths:Record<string,string>;references:Record<string,{bars:Bar[];partition:{id:string;sha256:string}}>;
  bi5:string;quotes:{counts:number[];bars:Bar[]}
};
const api='/research/api', phase=process.env.TRADEFORGE_DRILL_PHASE!;
const source='DUKASCOPY_SAVED',start=Date.parse('2025-11-13T13:00:00Z')/1000;
const root=process.env.TRADEFORGE_PAIR_STORAGE!;
const dir=resolve('..');
const ohlc=({time,open,high,low,close}:Bar)=>({time,open,high,low,close});
const datasets=async(request:APIRequestContext):Promise<Dataset[]>=>{
  const res=await request.get(api+'/datasets');expect(res.status()).toBe(200);return res.json();
};
async function register(page:Page,path:string){
  await page.getByLabel('Reviewed archive file').setInputFiles(path);
  const pending=page.waitForResponse(r=>r.url().endsWith('/market/import/reviewed'));
  await page.getByRole('button',{name:'Register or resume file'}).click();
  const res=await pending;expect(res.status(),await res.text()).toBe(200);return res.json();
}
async function inspect(page:Page,id:string,text:string){
  await page.getByLabel('Candle partition').selectOption(id);
  const trust=page.locator('details.data-trust');
  await expect(trust.locator('summary').first()).toContainText('Verified archive files');
  if(!await trust.evaluate(el=>el.hasAttribute('open')))await trust.locator('summary').first().click();
  await expect(trust.getByLabel('Source coverage')).toContainText(text);
  await expect(trust.getByLabel('Source coverage')).toContainText('Provider quote completeness is unverified');
}
async function checkQuotes(request:APIRequestContext,hours:number){
  const response=await request.get(api+'/market/candles',{params:{symbol:'EURUSD',timeframe:'M5',source,start,end:start+hours*3600}});
  expect(response.status(),await response.text()).toBe(200);
  const body=await response.json();expect(body.price_basis).toBe('BID');expect(body.feed_id).toBe('dukascopy-bi5-v2');
  expect(body.coverage.gaps).toEqual([]);
  expect(body.candles.map(ohlc)).toEqual(input.quotes.bars.slice(0,12*hours).map(({time,open,high,low,close})=>({
    time,open:open/1e5,high:high/1e5,low:low/1e5,close:close/1e5})));
}
async function openChart(page:Page){
  await installProbe(page);
  await page.addInitScript(()=>{for(const [k,v] of Object.entries({source:'DUKASCOPY_SAVED',timeframe:'M5',timezone:'UTC'}))
    localStorage.setItem('tradeforge.workspace.'+k,v);});
  await page.goto('/');await page.getByRole('button',{name:'Market Terminal',exact:true}).click();
  await page.getByLabel('SMT comparison').selectOption('GBPUSD');
}
async function chartMatches(page:Page){
  await expect.poll(()=>page.evaluate(()=>window.workspaceProbe.comparisonSeries().map(series=>
    series.data.map(({time,open,high,low,close})=>({time,open,high,low,close}))))).toEqual(
    ['EURUSD','GBPUSD'].map(s=>input.references[s].bars.map(ohlc)));
}
test('local market workspace '+phase,async({page,request,context})=>{
  test.setTimeout(120000);
  await page.goto('/');await page.getByRole('button',{name:'Data Library',exact:true}).click();
  if(phase==='import'){
    expect(await datasets(request)).toHaveLength(0);
    const changed=Buffer.from(readFileSync(input.paths.EURUSD));changed[0]^=1;
    const invalid=await request.post(api+'/market/import/reviewed',{multipart:{file:{name:'EURUSD.parquet',mimeType:'application/octet-stream',buffer:changed}}});
    expect(invalid.status()).toBe(422);expect(await datasets(request)).toHaveLength(0);
    for(const file of [input.paths.EURUSD,input.paths.GBPUSD,join(input.bi5,'13h_ticks.bi5')]){
      const first=await register(page,file), previous=await datasets(request);
      expect(first.source_sha256).toBe(createHash('sha256').update(readFileSync(file)).digest('hex'));
      expect(await register(page,file)).toEqual(first);
      expect(await datasets(request)).toEqual(previous);
    }
    const rows=await datasets(request);expect(rows).toHaveLength(7);
    for(const [symbol,ref] of Object.entries(input.references)){
      expect(rows).toContainEqual(expect.objectContaining({...ref.partition,symbol,timeframe:'M5',price_basis:'BID'}));
      await inspect(page,ref.partition.id,'Provider candle archive');
    }
    const hour=rows.find(r=>r.feed_id==='dukascopy-bi5-v2'&&r.timeframe==='M5'&&r.price_basis==='BID')!;
    await inspect(page,hour.id,'12 candle(s) match');
    await expect(page.getByLabel('Source coverage')).toContainText('Every record in the checksum-verified BI5 file matched');await checkQuotes(request,1);
    return;
  }
  if(phase==='restored'){
    const rows=await datasets(request);expect(rows).toHaveLength(12);
    for(const r of rows.filter(r=>r.kind==='candles')){
      const res=await request.get(api+'/datasets/'+r.id+'/coverage');expect(res.status(),await res.text()).toBe(200);
      expect((await res.json()).partitions).toEqual([{id:r.id,sha256:r.sha256}]);
    }
    await inspect(page,input.references.EURUSD.partition.id,'Provider candle archive');
    await openChart(page);await chartMatches(page);await checkQuotes(request,2);
    return;
  }
  expect(phase).toBe('publication');
  const chart=await context.newPage();await openChart(chart);await chartMatches(chart);
  const lesson=await context.newPage();await lesson.goto('/');
  await lesson.getByRole('button',{name:'Learning Journal',exact:true}).click();
  const [{meta,title,digest}]=JSON.parse(readFileSync(process.env.TRADEFORGE_PAIR_RECEIPTS!,'utf8'));
  const entry=lesson.locator('.journal-item').filter({hasText:title});
  await entry.getByText('Chart capture details',{exact:true}).click();
  const previous=await datasets(request);
  async function checkReaders(){
    await chartMatches(chart);
    for(const s of ['EURUSD','GBPUSD']){
      await expect(entry.locator('details')).toContainText(input.references[s].partition.sha256);
      const res=await request.get(api+'/market/candles',{params:{source,symbol:s,timeframe:'M5',limit:240}});
      expect(res.status()).toBe(200);
      expect((await res.json()).candles.map(ohlc)).toEqual(input.references[s].bars.slice(-240).map(ohlc));
    }
    const url=api+'/chart-snapshots/'+meta.id;
    expect(await (await request.get(url)).json()).toEqual(meta);
    expect(createHash('sha256').update(await (await request.get(url+'/image')).body()).digest('hex')).toBe(digest);
  }
  function writer(mode:string){
    const child=spawn(resolve(dir,'.venv/Scripts/python.exe'),['-m','research_backend.tests.workspace_fault_import',mode],
      {cwd:dir,env:{...process.env,TRADEFORGE_STORAGE:root,TRADEFORGE_DATA_DIR:join(root,'research')},windowsHide:true,stdio:'inherit'});
    return new Promise<number|null>((done,fail)=>{child.once('error',fail);child.once('exit',done);});
  }
  const interrupted=writer('interrupt');
  try{
    await expect.poll(()=>existsSync(join(root,'uncommitted-file')),{timeout:20000}).toBe(true);
    expect(await datasets(request)).toEqual(previous);await checkQuotes(request,1);await checkReaders();
    const missing=await request.get(api+'/market/candles',{params:{symbol:'EURUSD',timeframe:'M5',source,start:start+3600,end:start+7200}});
    expect(missing.status()).toBe(404);
  }finally{writeFileSync(join(root,'release-writer'),'release');}
  expect(await interrupted).toBe(75);
  expect(await datasets(request)).toEqual(previous);await checkReaders();
  expect(await writer('disk-full')).toBe(0);expect(await datasets(request)).toEqual(previous);await checkReaders();
  const file=join(input.bi5,'14h_ticks.bi5'), first=await register(page,file);
  expect(await register(page,file)).toEqual(first);
  expect(await datasets(request)).toHaveLength(12);
  await checkQuotes(request,2);await checkReaders();
});
