import assert from 'node:assert/strict';
import {chromium, expect} from '@playwright/test';

const utc = epoch => new Date(epoch * 1000).toISOString();
const button = (page, name) => page.getByRole('button', {name, exact:true});

async function post(page, path, action) {
  const [response] = await Promise.all([
    page.waitForResponse(r => r.url().endsWith(path) && r.request().method() === 'POST'),
    action(),
  ]);
  const result = await response.json();
  assert(response.ok(), path+' returned '+response.status()+' '+JSON.stringify(result.detail)?.slice(0,320));
  return result;
}

async function freeze(page, fixture) {
  const end = fixture.start + (fixture.count + 1) * 300;
  for (const [label, timeLabel, epoch] of [
    ['From UTC','From time (UTC)',fixture.start], ['Until UTC (exclusive)','Until time (UTC)',end],
  ]) {
    await page.getByLabel(label,{exact:true}).fill(utc(epoch).slice(0,10));
    await page.getByLabel(timeLabel,{exact:true}).fill(utc(epoch).slice(11,16));
  }
  await button(page,'Load saved range').click();
  return post(page,'/api/replay/sessions',()=>button(page,'Freeze range for replay').click());
}

async function checkReturn(page, sample) {
  const {fixture, ref, cutoff, journal, snapshot, gap} = sample;
  const entry = page.locator('.journal-item').filter({hasText:journal});
  await entry.locator('summary').filter({hasText:'Chart capture details'}).click();
  if (gap) await expect(entry).toContainText(gap.symbol);
  const action = entry.getByRole('button',{name:'Return to decision candle'});
  await action.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByLabel('Inspection pane')).toBeFocused();
  await expect(page.getByLabel('Inspection pane')).toHaveValue('primary');
  await expect(page.getByLabel('Replay timeline')).toHaveValue(String(ref.position));
  await expect(page.getByLabel('Historical replay',{exact:true}).locator('output')).toContainText(utc(cutoff));
  const selected=page.getByLabel('Selected candle',{exact:true});
  await expect(selected.locator('time').first()).toHaveAttribute('datetime',utc(ref.time));
  const index=(ref.time-fixture.start)/300;
  await expect(selected.getByLabel('Selected candle OHLC')).toContainText(((110001+index)/100000).toFixed(5));
  await selected.locator('summary').click();
  await expect(selected).toContainText(fixture.pins.EURUSD.sha256);
  if (gap) {
    await expect(page.getByText(gap.status==='SOURCE_ERROR' ? /GBPUSD · Comparison source unavailable/ :
      /GBPUSD · No comparison candles available at this cutoff/).first()).toBeVisible();
    await expect(page.getByLabel('Chart data',{exact:true})).not.toContainText('1.30001');
  } else {
    await expect(page.getByText(/SMT overlay unavailable/).first()).toBeVisible();
  }
  const saved=await (await page.request.get('/research/api/chart-snapshots/'+snapshot)).json();
  assert.deepEqual(saved.decision_replay,ref);
  assert.equal(saved.cutoff,cutoff);
  assert.deepEqual(saved.comparison_gap,gap);
}

async function record(page,fixture) {
  const long=fixture.scenario==='long';
  const session=await freeze(page,fixture);
  assert.equal(session.market.candles.length,fixture.count);
  assert.equal(session.market.partitions[0].sha256,fixture.pins.EURUSD.sha256);
  const position=long?4999:0;
  const cutoff=fixture.start+(position+1)*300;
  const candleIndex=long?7:0;
  const time=fixture.start+candleIndex*300;
  await page.getByLabel('Replay timeline').fill(String(position));
  await page.getByLabel('Inspect candle',{exact:true}).fill(String(candleIndex));
  const capture=button(page,'Save chart evidence');
  if(!await capture.count()) throw Error('Capture missing, planner disabled='+await button(page,'Create trade plan').isDisabled());
  const saved=await post(page,'/chart-snapshots',()=>capture.click());
  const ref=saved.decision_replay;
  assert.equal(ref.session_id,session.id);
  assert.equal(ref.position,position);
  assert.equal(ref.view,'primary');
  assert.equal(ref.time,time);
  assert.equal(saved.cutoff,cutoff);
  assert.deepEqual(saved.lanes.map(l=>l.symbol),long?['EURUSD','GBPUSD']:['EURUSD']);
  for(const lane of saved.lanes) {
    assert.deepEqual(lane.partitions,[fixture.pins[lane.symbol]]);
    assert.equal(lane.bar_times.length,long?5000:1);
    assert.equal(lane.bar_times[candleIndex],time);
  }
  const gap=saved.comparison_gap;
  if(long) assert.equal(gap,undefined);
  else {
    assert.equal(gap.status,fixture.scenario==='delayed'?'NO_VISIBLE_OBSERVATIONS':'SOURCE_ERROR');
    assert.equal(gap.symbol,'GBPUSD');
    if(fixture.scenario==='source-error')assert.equal(gap.error,session.views.comparison.error);
  }
  await button(page,'Create trade plan').click();
  await page.getByText('Pass on this setup',{exact:true}).click();
  const reason='Controlled '+fixture.scenario+' decision at '+utc(cutoff);
  await page.getByLabel('Reason to pass').fill(reason);
  const entry=await post(page,'/journal',()=>button(page,'Save pass to journal').click());
  const sample={fixture,ref,cutoff,journal:entry.id,snapshot:saved.id,gap};
  await checkReturn(page,sample);
  await button(page,'Next candle').click();
  await expect(page.getByLabel('Replay timeline')).toHaveValue(String(position+1));
  if(fixture.scenario==='delayed'){
    await expect(page.getByText(/GBPUSD · No comparison candles available at this cutoff/)).toHaveCount(0);
    await expect(page.getByLabel('Chart data',{exact:true})).toContainText('GBPUSD');
  }
  await post(page,'/checkpoint',()=>button(page,'Save replay position').click());
  return sample;
}

// The source files are controlled partitions. The genuine provider journey runs separately.
export async function verifyDecisionBoundaries(origin,fixtures,restart,counts) {
  let browser=await chromium.launch();
  const samples=[], errors=[], failures=[];
  async function newPage(){
    const page=await browser.newPage({baseURL:origin,viewport:{width:1536,height:960}});
    page.on('pageerror',err=>errors.push(err.message));
    await page.goto(origin);
    return page;
  }
  try {
    for(const fixture of fixtures){
      const page=await newPage();
      try{
        await button(page,'Market Terminal').click();
        await button(page,'Forex M5 workspace').click();
        const before=counts();
        const sample=await record(page,fixture);
        const after=counts();
        assert.equal(after.journal_entries,before.journal_entries+1);
        assert.equal(after.chart_snapshots,before.chart_snapshots+1);
        assert.equal(after.trades,0);
        assert.equal(after.plan_paper_events,0);
        samples.push(sample);
      }catch(error){failures.push(fixture.scenario+': '+error.message);}
      finally{await page.close();}
    }
    assert.deepEqual(failures,[]);
    await browser.close();
    await restart();
    browser=await chromium.launch();
    for(const sample of samples){
      const page=await newPage();
      await button(page,'Learning Journal').click();
      await checkReturn(page,sample);
      await page.close();
    }
    assert.deepEqual(errors,[]);
    return {source:'CONTROLLED_M5_BID',new_api_and_browser:true,samples,counts:counts(),errors};
  }finally{await browser.close();}
}
