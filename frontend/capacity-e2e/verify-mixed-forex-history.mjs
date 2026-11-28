// Controlled imports through an isolated local application. No provider requests or mocked candles.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {writeFileSync} from 'node:fs';
import {parseArgs} from 'node:util';
import {chromium,expect} from '@playwright/test';

const {values}=parseArgs({options:{url:{type:'string'},python:{type:'string'},output:{type:'string'},baseline:{type:'boolean'}}});
assert(values.url&&values.python&&values.output,'Supply --url, --python and --output for a fresh disposable profile');
assert(['localhost','127.0.0.1'].includes(new URL(values.url).hostname));
const day='2026-10-08', end='2026-10-09';
const start=Date.parse(day+'T13:00Z')/1000;
const price={EURUSD:1.12,GBPUSD:1.27};
const symbols=Object.keys(price);
const qprice=1.47;
const minutes=Array.from({length:60},(_,n)=>n);
// Delay minute 30 by one millisecond to exceed the 60-second continuity bound.
const ticks=Buffer.alloc(20*minutes.length);
for(const n of minutes){
  const o=20*n;
  ticks.writeUInt32BE(n*60_000+(n===30?1:0),o);
  ticks.writeUInt32BE(Math.round(qprice*100000)+2,o+4);
  ticks.writeUInt32BE(Math.round(qprice*100000),o+8);
  ticks.writeFloatBE(1,o+12);ticks.writeFloatBE(1,o+16);
}
const bi5=execFileSync(values.python,['-c','import sys,lzma;sys.stdout.buffer.write(lzma.compress(sys.stdin.buffer.read()))'],{input:ticks});
const url=path=>new URL('/research/api/'+path,values.url);
async function importFile(path,name,bytes){
  const form=new FormData();
  form.append('file',new Blob([bytes]),name);
  const res=await fetch(url(path),{method:'POST',body:form});
  const data=await res.json();assert.equal(res.status,200,JSON.stringify(data));return data;
}
function csv(symbol){
  const rows=minutes.map(n=>[new Date((start+n*60)*1000).toISOString(),...Array(4).fill(price[symbol].toFixed(5)),1].join(','));
  return ['Etc/UTC,Open,High,Low,Close,Volume',...rows].join('\n')+'\n';
}
const candles=symbol=>importFile('forex/candles?symbol='+symbol+'&day='+day,symbol.slice(0,3)+'-USD_1Minute_BID_'+day+'_00_00-23_59_Etc_UTC.csv',csv(symbol));
const quotes=()=>importFile('forex/bi5?symbol=EURUSD&hour='+start,'13h_ticks.bi5',bi5);
const pin=({id,sha256})=>({id,sha256});
const counts=h=>[h.eur.count,h.gbp.count,h.paired,h.quotes.count];
async function inventory(){
  const res=await fetch(url('forex/coverage?start='+day+'&end='+end));assert.equal(res.status,200);
  return (await res.json()).hours.find(h=>h.start===start);
}
assert.deepEqual(counts(await inventory()),[0,0,0,0],'Use empty storage for the controlled journey');
const imports={};
for(const s of symbols)imports[s]=await candles(s);
const original=await inventory();
assert.deepEqual(counts(original),[12,12,12,0]);
const pins=symbols.map(s=>pin(imports[s].partitions.find(p=>p.timeframe==='M5')));
const importedTicks=await quotes();
const mixed=await inventory();
const receipt={source:'CONTROLLED_CSV_AND_HOURLY_BI5',date:day,start,original:counts(original),mixed:counts(mixed),
  original_pins:pins,alternative_pin:pin(importedTicks.candle_datasets.find(p=>p.timeframe==='M5'&&p.price_basis==='BID')),quote_pin:pin(importedTicks.tick_dataset),
  issue:mixed.eur.error,gaps:mixed.quotes.gaps};
if(values.baseline){
  assert.deepEqual(counts(mixed),[0,12,0,60]);
  assert.match(mixed.eur.error,/incompatible feeds/);
  assert.deepEqual(mixed.paired_ranges,[]);
}else{
  assert.deepEqual(counts(mixed),[12,12,12,60]);
  assert.deepEqual(mixed.paired_ranges,[[start,start+3600]]);
  assert(mixed.quotes.gaps.some(([a,b])=>a===(start+29*60)*1000+1&&b===(start+30*60)*1000+1));
  for(const [i,k] of ['eur','gbp'].entries())assert.deepEqual(mixed[k].pins.map(pin),[pins[i]]);
  assert.deepEqual(mixed.eur.alternatives.flatMap(a=>a.pins.map(pin)),[receipt.alternative_pin]);
  const [selection]=mixed.chart_selections;
  assert.equal(selection.feed_id,'dukascopy-web-m1-v1');
  for(const s of symbols)assert.deepEqual(await candles(s),imports[s]);
  assert.deepEqual(await quotes(),importedTicks);
  assert.deepEqual(await inventory(),mixed);
  const browser=await chromium.launch();
  try{
    const page=await browser.newPage();
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.goto(values.url);
    await page.getByRole('button',{name:'Data Library',exact:true}).press('Enter');
    const panel=page.getByRole('region',{name:'Forex history',exact:true});
    await panel.getByLabel('Start date (UTC)',{exact:true}).fill(day);
    await panel.getByLabel('Exclusive end date (UTC)',{exact:true}).fill(end);
    await panel.getByRole('button',{name:'Inspect coverage'}).press('Enter');
    await panel.getByRole('button',{name:'13:00 UTC',exact:true}).press('Enter');
    await panel.getByText('EURUSD M5 BID · 12 candles · 0 missing intervals',{exact:true}).press('Enter');
    await expect(panel.getByText('Alternative · dukascopy-bi5-v2 · 12 candles',{exact:true})).toBeVisible();
    const previews=symbols.map(symbol=>page.waitForResponse(response=>{
      const u=new URL(response.url());
      return u.pathname.endsWith('/forex/chart')&&u.searchParams.get('symbol')===symbol;
    }));
    await panel.getByRole('button',{name:/Open paired replay/}).press('Enter');
    for(const [i,preview] of (await Promise.all(previews)).entries()){
      assert.equal(preview.status(),200);
      const chart=await preview.json();
      assert.deepEqual(chart.partitions,[pins[i]]);
      assert.equal(chart.candles.at(-1).close,price[symbols[i]]);
    }
    const freeze=page.getByRole('button',{name:'Freeze range for replay',exact:true});
    await expect(freeze).toBeEnabled();
    const [reply]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/replay/sessions')&&r.request().method()==='POST'),freeze.press('Enter')]);
    assert.deepEqual(reply.request().postDataJSON().coverage_selection, selection);
    const session=await reply.json();assert.equal(reply.status(),201,JSON.stringify(session));
    const lanes=[session.market,session.views.comparison];
    for(const [i,lane] of lanes.entries()){
      assert.deepEqual(lane.partitions,[pins[i]]);
      assert.equal(lane.candles.length,12);
      for(const [n,bar] of lane.candles.entries()){
        assert.equal(bar.time,start+300*n);
        for(const k of ['open','high','low','close'])assert.equal(bar[k],price[symbols[i]]);
      }
    }
    receipt.frozen={id:session.id,pins:lanes.map(lane=>lane.partitions[0]),prices:lanes.map(lane=>lane.candles[0].close)};
    const reopened=await fetch(url('replay/sessions/'+session.id)).then(r=>r.json());
    assert.equal(reopened.id,session.id);
    assert.deepEqual(reopened.market,session.market);
    assert.deepEqual(reopened.views.comparison,session.views.comparison);
    // Leaving the advertised pair releases its pin contract. The native reader
    // must then diagnose the mixed EURUSD series in its own terms.
    await page.getByRole('button',{name:'Exit replay',exact:true}).click();
    await page.getByRole('combobox',{name:'SMT comparison',exact:true}).selectOption('none');
    await expect(page.getByText(/^Coverage chart feed/)).toHaveCount(0);
    const [generic]=await Promise.all([
      page.waitForResponse(r=>r.url().endsWith('/api/replay/sessions')&&r.request().method()==='POST'),
      freeze.press('Enter'),
    ]);
    assert.equal(generic.request().postDataJSON().coverage_selection,undefined);
    assert.equal(generic.status(),409);
    const {detail}=await generic.json();
    assert.match(detail,/incompatible feeds/);
    receipt.changed_view={selection_released:true,status:generic.status(),detail};
    assert.deepEqual(errors,[]);
  }finally{await browser.close();}
}
writeFileSync(values.output,JSON.stringify(receipt,null,2)+'\n');
console.log(JSON.stringify(receipt));
