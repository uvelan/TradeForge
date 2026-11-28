import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

// Controlled Parquet inputs; the only delayed operation is delivery of A's journal response.
export async function verifyDecisionReview(origin,{start,pins},counts){
  const browser=await chromium.launch();
  try{
    const page=await browser.newPage({baseURL:origin});
    page.setDefaultTimeout(90000);
    const click=name=>page.getByRole('button',{name,exact:true}).click();
    const post=async(path,action)=>{
      const [r]=await Promise.all([page.waitForResponse(r=>r.url().endsWith(path)&&r.request().method()==='POST'),action()]);
      assert.ok(r.ok(),await r.text());return r.json();
    };
    const request={source:'DUKASCOPY_SAVED',symbol:'EURUSD',timeframe:'M5',window_bars:600,start,end:start+7200,
      views:[{view:'2',symbol:'GBPUSD',timeframe:'H1'}]};
    const res=await page.request.post('/research/api/replay/sessions',{data:request});
    assert.ok(res.ok(),await res.text());const session=await res.json();
    const checkpoint=await page.request.post('/research/api/replay/sessions/'+session.id+'/checkpoint',
      {data:{position:session.market.replay_cutoffs.length-1,bookmarks:[]}});
    assert.ok(checkpoint.ok(),await checkpoint.text());
    await page.addInitScript(()=>localStorage.setItem('tradeforge.workspace.source','DUKASCOPY_SAVED'));
    await page.goto('/');await click('Market Terminal');
    await page.getByLabel('Market data source').selectOption(request.source);
    await page.getByLabel('Resume saved replay').selectOption(session.id);
    const timeline=page.getByLabel('Replay timeline');
    await timeline.fill(String(session.market.replay_cutoffs.length-1));
    await page.getByLabel('Inspection pane').selectOption('2');
    const slider=page.getByLabel('Inspect candle',{exact:true});
    const shots=[];
    async function capture(index){
      await slider.fill(String(index));await click('Record decision');
      const shot=await post('/chart-snapshots',()=>click('Attach chart capture'));
      assert.equal(shot.symbol,'EURUSD');assert.equal(shot.timeframe,'M5');
      assert.equal(shot.decision_replay.view,'2');assert.equal(shot.decision_replay.time,start+3600*index);
      assert.equal(shot.cutoff,start+7200);shots.push(shot.id);return shot;
    }
    const a=await capture(0);
    const release=Promise.withResolvers(),committed=Promise.withResolvers();
    await page.route('**/research/api/journal',async route=>{
      if(route.request().method()!=='POST')return route.continue();
      const response=await route.fetch();assert.ok(response.ok(),await response.text());
      committed.resolve();await release.promise;await route.fulfill({response});
    },{times:1});
    await page.getByLabel('Reason to pass').fill('First H1 observation');await click('Save pass to journal');
    await committed.promise;await page.keyboard.press('Escape');
    const b=await capture(1);assert.notEqual(a.id,b.id);
    await page.getByLabel('Reason to pass').fill('Second H1 observation');
    const done=page.waitForResponse(r=>r.url().endsWith('/journal')&&r.request().method()==='POST');
    release.resolve();await done;
    const drawer=page.getByRole('dialog',{name:'Record decision'});
    await expect(drawer.getByLabel('Reason to pass')).toHaveValue('Second H1 observation');
    await expect(drawer).not.toContainText('Pass saved');assert.equal(counts().journal_entries,1);
    await post('/journal',()=>click('Save pass to journal'));
    await expect(drawer).toContainText('GBPUSD · H1');
    await click('Create trade plan');
    const context='DUKASCOPY_SAVED · controlled-peer-h1 · BID';
    await expect(page.getByLabel(/01 · Context/)).toHaveValue(context);
    for(const field of ['Observation','Evidence','Hypothesis','Setup','Trigger','Invalidation','Management plan']){
      await page.getByRole('textbox',{name:new RegExp(field+'$')}).fill('Controlled numbered H1 '+field);
    }
    for(const [name,val] of [['Entry','1.30000'],['Stop','1.29900'],['Target','1.30200']])await page.getByLabel(name,{exact:true}).fill(val);
    const saved=await post('/trades',()=>click('Save plan'));
    await page.reload();
    await click('Trade Planner');
    await page.getByRole('row').filter({hasText:saved.id}).getByRole('button',{name:'Versions'}).click();
    const history=page.getByRole('region',{name:'Plan versions'});
    await history.getByText('Chart capture details',{exact:true}).click();
    await expect(history).toContainText('Frozen chart cutoff: '+new Date(b.cutoff*1000).toISOString());
    const trade=await (await page.request.get('/research/api/trades/'+saved.id)).json();
    const plan=trade.versions[0].plan;
    assert.deepEqual([plan.symbol,plan.timeframe,plan.context,plan.snapshot_id],['GBPUSD','H1',context,b.id]);
    const rows=await (await page.request.get('/research/api/journal')).json();
    assert.deepEqual(rows.map(r=>r.snapshot_id).sort(),[...shots].sort());
    const reopened=[];
    for(const shot of [a,b]){
      const read=await (await page.request.get('/research/api/chart-snapshots/'+shot.id)).json();
      assert.deepEqual(read,shot);reopened.push({id:read.id,bookmark:read.decision_replay,cutoff:read.cutoff});
    }
    const count=counts();assert.equal(count.journal_entries,2);assert.equal(count.plan_paper_events,0);
    assert.deepEqual(session.views['2'].partitions,[pins.GBPUSD]);
    return {fixture:'controlled numbered H1 pane',session:session.id,pins,reopened,trade:saved.id,context,count};
  }finally{await browser.close();}
}
