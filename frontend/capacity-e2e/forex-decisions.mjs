import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

// Exercise the same catalog and HTTP services as the Windows application.
export async function verifyDecisions(origin,restart){
  let browser=await chromium.launch();
  const browserErrors=[];
  async function createPage(){
    const page=await browser.newPage({viewport:{width:1536,height:960}});
    page.on('pageerror',error=>browserErrors.push(error.stack??error.message));
    return page;
  }
  let page=await createPage();
  const button=name=>page.getByRole('button',{name,exact:true});
  const click=name=>button(name).click();
  const captured=[];
  const journal=[];
  const checks=[];
  async function posted(path,action){
    const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith(path)&&r.request().method()==='POST'),action()]);
    assert(response.ok(),await response.text());
    return response.json();
  }
  async function freeze(){
    for(const [label,value] of [['From UTC','2026-11-13'],['Until UTC (exclusive)','2026-11-13'],
        ['From time (UTC)','13:00'],['Until time (UTC)','15:00']]){
      await page.getByLabel(label,{exact:true}).fill(value);
    }
    await click('Load saved range');
    return posted('/api/replay/sessions',()=>click('Freeze range for replay'));
  }
  try{
    await page.goto(origin);
    await click('Market Terminal');
    await click('Forex M5 workspace');
    for(const [index,kind] of ['trade','pass'].entries()){
      if(index)await page.getByLabel('Comparison placement').selectOption('New pane');
      const session=await freeze();
      assert.equal(session.request.smt_rules.alignment_bars,0);
      const timeline=page.getByLabel('Replay timeline');
      await timeline.fill(String(12+index));
      await page.getByLabel('Inspection pane').selectOption(index?'primary':'comparison');
      await page.getByLabel('Inspect candle',{exact:true}).fill('4');
      const shot=await posted('/api/chart-snapshots',()=>click('Save chart evidence'));
      const ref=shot.decision_replay;
      assert.equal(ref.session_id,session.id);
      assert.equal(ref.comparison_placement,index?'New pane':'Overlay');
      assert.equal(ref.view,index?'primary':'comparison');
      assert.equal(ref.time,Date.parse('2026-11-13T13:20:00Z')/1000);
      assert.equal(shot.cutoff,session.market.replay_cutoffs[12+index]);
      assert.deepEqual(shot.lanes.map(l=>l.symbol),['EURUSD','GBPUSD']);
      captured.push({id:shot.id,ref,cutoff:shot.cutoff,pins:shot.lanes.map(l=>l.partitions)});
      await click('Create trade plan');
      if(kind==='trade'){
        await click('Fill example wording (edit before saving)');
        for(const [label,value] of [['Entry','1.10500'],['Stop','1.10400'],['Target','1.10700']]){
          await page.getByLabel(label,{exact:true}).fill(value);
        }
        const plan=await posted('/trades',()=>click('Save plan'));
        await click('Journal this plan revision');
        await page.getByLabel('Title',{exact:true}).fill('Paired replay trade');
        await page.getByLabel('Observation / explanation').fill('Wait for a confirmed paired divergence.');
        const entry=await posted('/journal',()=>click('Append to journal'));
        journal.push({entry:entry.id,trade:plan.id});
      }else{
        await page.getByText('Pass on this setup',{exact:true}).click();
        await page.getByLabel('Reason to pass').fill('No confirmed paired divergence at the cutoff.');
        const entry=await posted('/journal',()=>click('Save pass to journal'));
        journal.push({entry:entry.id});
      }
      const row=page.locator('.journal-item').filter({hasText:journal[index].entry});
      await row.locator('summary').filter({hasText:'Chart capture details'}).click();
      await row.getByRole('button',{name:'Return to decision candle'}).click();
      await expect(timeline).toHaveValue(String(12+index));
      await click('Last candle');
      await posted('/checkpoint',()=>click('Save replay position'));
      await click('Market Terminal');
      if(!index){
        await click('Exit replay');
        await click('Forex M5 workspace');
      }
    }
    await browser.close();
    await restart();
    browser=await chromium.launch();page=await createPage();
    await page.goto(origin);
    await click('Market Terminal');
    await page.getByLabel('Chart layout',{exact:true}).selectOption('4');
    for(const [index,item] of journal.entries()){
      await click('Learning Journal');
      const row=page.locator('.journal-item').filter({hasText:item.entry});
      if(item.trade)await expect(row.getByRole('button',{name:'Open linked plan v1'})).toBeVisible();
      else await expect(row).toContainText('No confirmed paired divergence at the cutoff.');
      await row.locator('summary').filter({hasText:'Chart capture details'}).click();
      await row.getByRole('button',{name:'Return to decision candle'}).focus();
      await page.keyboard.press('Enter');
      await expect(page.getByLabel('Inspection pane')).toBeFocused();
      const {ref,id,cutoff,pins}=captured[index];
      await expect(page.getByLabel('Chart layout',{exact:true})).toHaveValue('1');
      await expect(page.getByLabel('Inspection pane')).toHaveValue(ref.view);
      await expect(page.locator('.primary-chart .chart-replay-guide')).toHaveAttribute('data-replay-candle',String(ref.time));
      await expect(page.getByLabel('Replay timeline')).toHaveValue(String(ref.position));
      await expect(page.getByLabel('Selected candle',{exact:true}).locator('time').first()).toHaveAttribute('datetime',new Date(ref.time*1000).toISOString());
      const saved=await (await page.request.get(origin+'/research/api/chart-snapshots/'+id)).json();
      assert.deepEqual(saved.decision_replay,ref);assert.equal(saved.cutoff,cutoff);
      assert.deepEqual(saved.lanes.map(l=>l.partitions),pins);
      checks.push({journal:item,...captured[index]});
    }
    const trades=await (await page.request.get(origin+'/research/api/trades')).json();
    assert.equal(trades.length,1);
    assert.equal(trades[0].id,journal[0].trade);
    const paper=await (await page.request.get(origin+'/research/api/paper/plans/'+trades[0].id)).json();
    assert.deepEqual(paper.events,[]);
    assert.deepEqual(browserErrors,[]);
    return {checks,planned:trades.length,paperEvents:paper.events.length};
  }catch(error){
    console.error(browserErrors,await page.locator('body').innerText().catch(()=>'Page closed'));
    throw error;
  }finally{await browser.close();}
}
