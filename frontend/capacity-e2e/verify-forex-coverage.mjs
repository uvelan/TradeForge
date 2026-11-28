// Windows-local acceptance against a disposable profile containing the pinned CSV and BI5 imports.
import assert from 'node:assert/strict';
import {readFileSync, mkdirSync, writeFileSync} from 'node:fs';
import {join} from 'node:path';
import {parseArgs} from 'node:util';
import {createHash} from 'node:crypto';
import {chromium, expect} from '@playwright/test';

const {values} = parseArgs({options: {url: {type:'string'}, csv: {type:'string'}, output: {type:'string'}}});
assert(values.url && values.csv && values.output, 'Supply --url, --csv and --output for the seeded disposable app.');
assert(['localhost','127.0.0.1'].includes(new URL(values.url).hostname));
mkdirSync(values.output, {recursive:true});
const body = readFileSync(join(values.csv,'EURUSD_2026-11-13_M1_BID.csv'));
const csvPin = '4c1e415ee7bb1c8a7cfe473364afb5d00f82d11ea9e3b48d8960b476f53f0a6d';
assert.equal(createHash('sha256').update(body).digest('hex'),csvPin);
const browser = await chromium.launch();
try {
  const page = await browser.newPage({viewport:{width:1440,height:960}});
  const errors = [];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(values.url);
  await page.getByRole('button',{name:'Data Library',exact:true}).press('Enter');
  const panel = page.getByRole('region',{name:'Forex history',exact:true});
  async function inspect(start,end,expected){
    await panel.getByLabel('Start date (UTC)',{exact:true}).fill(start);
    await panel.getByLabel('Exclusive end date (UTC)',{exact:true}).fill(end);
    const [response] = await Promise.all([
      page.waitForResponse(r=>r.url().includes('/api/forex/coverage?')),
      panel.getByRole('button',{name:'Inspect coverage'}).press('Enter'),
    ]);
    assert.equal(response.status(),200);
    const report = await response.json();
    assert.equal(report.start,Date.parse(start+'T00:00Z')/1000);
    assert.equal(report.end,Date.parse(end+'T00:00Z')/1000);
    const totals = report.hours.reduce((a,h)=>[a[0]+h.eur.count,a[1]+h.gbp.count,
      a[2]+h.paired,a[3]+h.quotes.count],[0,0,0,0]);
    assert.deepEqual(totals,expected);
    const [eur,gbp,paired,quotes]=totals;
    await expect(panel.getByText(`${eur} EURUSD candles · ${gbp} GBPUSD candles · ${paired} paired UTC opens · ${quotes} EURUSD quotes`)).toBeVisible();
    return report;
  }
  const month = await inspect('2025-11-01','2025-12-01',[24,0,0,8636]);
  assert.equal(month.hours.length,720);
  const closed=month.hours.filter(h=>!h.expected).length;
  const missing=month.hours.filter(h=>h.expected&&!h.quotes.registered).length;
  assert.equal(closed,238);
  assert.equal(missing,480);
  assert.deepEqual(month.hours.filter(h=>h.quotes.count).map(h=>h.quotes.count),[3171,5465]);
  const period=await inspect('2026-11-09','2026-11-19',[2266,2262,2251,0]);
  const day=await inspect('2026-11-13','2026-11-14',[264,264,264,0]);
  const hour=day.hours[13];
  assert.deepEqual(hour.quotes.gaps,[[hour.start*1000,hour.end*1000]]);
  assert.equal(hour.paired,12);
  await panel.getByRole('button',{name:'13:00 UTC',exact:true}).press('Enter');
  const detail=panel.getByRole('region',{name:'Selected hour evidence'});
  await detail.getByText(/EURUSD bid\/ask · 0 observations/).press('Enter');
  await expect(detail.getByText(/2026-11-13T13:00:00.000Z/)).toBeVisible();
  await expect(detail.getByRole('button',{name:'Retry missing EURUSD hour'})).toBeVisible();
  await detail.getByText(/EURUSD M5 BID · 12 candles/).press('Enter');
  await expect(detail.getByText(csvPin,{exact:true})).toBeVisible();
  // Re-deliver identical source bytes through the local import control and inspect both returned IDs.
  await detail.getByLabel(/Local BID M1 CSV/).setInputFiles({
    name:'EUR-USD_1Minute_BID_2026-11-13_00_00-23_59_Etc_UTC.csv', mimeType:'text/csv', buffer:body,
  });
  const imports=[];
  for (let n=0;n<2;n++){
    const [reply]=await Promise.all([page.waitForResponse(r=>r.url().includes('/api/forex/candles?')),
      detail.getByRole('button',{name:'Import local file'}).press('Enter')]);
    assert.equal(reply.status(),200);
    imports.push(await reply.json());
    await expect(panel.getByText(/BID candle file registered/)).toBeVisible();
    await expect(panel.getByRole('button',{name:'Inspect coverage'})).toBeEnabled();
  }
  assert.deepEqual(imports[0],imports[1]);
  assert.equal(imports[0].source.sha256,csvPin);
  await page.setViewportSize({width:1440,height:1620});
  await panel.evaluate(el=>el.scrollIntoView({block:'center'}));
  await panel.locator('.table-scroll').evaluate(el=>{el.scrollTop=0;});
  await panel.screenshot({path:join(values.output,'genuine-paired-history.png')});
  await page.setViewportSize({width:390,height:844});
  const table=panel.locator('.table-scroll');
  assert(await table.evaluate(el=>el.clientWidth<=window.innerWidth && el.scrollWidth>=el.clientWidth),'Table must stay inside narrow viewport');
  await expect(detail.getByRole('button',{name:/Open paired replay/})).toBeEnabled();
  await page.setViewportSize({width:1440,height:960});
  await detail.getByRole('button',{name:/Open paired replay/}).press('Enter');
  await expect(page.getByLabel('From time (UTC)',{exact:true})).toHaveValue('13:00');
  await expect(page.getByLabel('Until time (UTC)',{exact:true})).toHaveValue('14:00');
  await expect(page.getByRole('button',{name:'Freeze range for replay',exact:true})).toBeEnabled();
  const [frozen]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/replay/sessions')&&r.request().method()==='POST'),
    page.getByRole('button',{name:'Freeze range for replay',exact:true}).press('Enter')]);
  assert.equal(frozen.status(),201);
  const session=await frozen.json(), peer=session.views.comparison;
  for(const [lane,pin] of [[session.market,hour.eur.pins[0]],[peer,hour.gbp.pins[0]]]){
    assert.equal(lane.candles.length,12);
    assert.equal(lane.candles[0].time,hour.start);
    assert.deepEqual(lane.partitions,[{id:pin.id,sha256:pin.sha256}]);
  }
  assert.deepEqual(errors,[]);
  writeFileSync(join(values.output,'genuine-paired-history.json'),JSON.stringify({
    month2025:{hours:720,closed,missing,quotes:8636},period2026:{hours:period.hours.length,eur:2266,gbp:2262,paired:2251,quotes:0},
    selected:hour,identical_imports:imports.length,session:session.id,browser_errors:errors,
  },null,2));
} finally {await browser.close();}
