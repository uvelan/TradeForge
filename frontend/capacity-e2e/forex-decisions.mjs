import {keepManualDecision} from './plan-pane.mjs';
import {mkdirSync} from 'node:fs';
import {join} from 'node:path';
import assert from 'node:assert/strict';
import {chromium,expect} from '@playwright/test';

// Exercise the same catalog and HTTP services as the Windows application.
export async function verifyDecisions(origin,restart){
  let browser=await chromium.launch();
  const browserErrors=[];
  async function createPage(){
    const page=await browser.newPage({viewport:{width:1536,height:960}});
    page.setDefaultTimeout(90000);
    page.on('pageerror',error=>browserErrors.push(error.stack??error.message));
    return page;
  }
  let page=await createPage();
  const button=name=>page.getByRole('button',{name,exact:true});
  const click=name=>button(name).click();
  const captured=[];
  const journal=[];
  const checks=[];
  const plots=[];
  async function measurePlot(target,kind){
    const chart=target.locator('.primary-chart .chart-input');
    const measure=()=>chart.evaluate(host=>{
      const row=host.querySelector('table').rows[0],plot=row.cells[1],axis=row.cells[2];
      const bounds=el=>{const rect=el.getBoundingClientRect();return [rect.left,rect.right];};
      // The last-price badge paints a saturated band across the right scale. Gridlines and
      // scale tick text alone must not satisfy this check.
      const label=Array.from(axis.querySelectorAll('canvas')).some(canvas=>{
        if(!canvas.width||!canvas.height)return false;
        const {width:w,height:h}=canvas;
        const data=canvas.getContext('2d').getImageData(0,0,w,h).data;
        for(let y=0;y<h;y++){
          let colored=0;
          for(let x=0;x<w;x++){
            const i=(y*w+x)*4;
            if(data[i+3]>180&&Math.max(data[i],data[i+1],data[i+2])-Math.min(data[i],data[i+1],data[i+2])>55)colored++;
          }
          if(colored>w*.65)return true;
        }
        return false;
      });
      return {viewport:innerWidth,host:bounds(host),plot:bounds(plot),axis:bounds(axis),lastPriceLabel:label};
    });
    await expect.poll(async()=>{
      const m=await measure();return Math.abs(m.host[1]-m.axis[1])<3&&m.axis[1]<=m.viewport&&m.plot[1]<=m.axis[0]+1&&m.axis[1]-m.axis[0]>0&&m.lastPriceLabel;
    }).toBe(true);
    await expect(target.locator('.terminal-head-price strong')).toHaveText('1.10527');
    plots.push({kind,...await measure(),lastClose:'1.10527'});
  }
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
    await page.addInitScript(()=>localStorage.setItem('tradeforge.workspace.source','DUKASCOPY_SAVED'));
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
      if(process.env.TF_TERMINAL_SHOTS && !index){
        const dir=process.env.TF_TERMINAL_SHOTS;
        mkdirSync(dir,{recursive:true});
        for(const [width,height] of [[1440,900],[1280,800],[390,844]]){
          await page.setViewportSize({width,height});
          await page.locator('.terminal-suite-header').scrollIntoViewIfNeeded();
          await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
          await measurePlot(page,'desktop-resized');
          await page.screenshot({animations:'disabled',path:join(dir,`terminal-${width}.png`)});

        }
        const phone=await browser.newPage({viewport:{width:390,height:844}});
        await phone.addInitScript(()=>localStorage.setItem('tradeforge.workspace.source','DUKASCOPY_SAVED'));
        await phone.goto(origin);
        await phone.getByRole('button',{name:'Open navigation'}).click();
        await phone.getByRole('button',{name:'Market Terminal',exact:true}).click();
        await phone.getByRole('button',{name:/Chart settings/}).click();
        await phone.getByRole('button',{name:'Forex M5 workspace'}).click();
        for(const [label,value] of [['From UTC','2026-11-13'],['Until UTC (exclusive)','2026-11-13'],['From time (UTC)','13:00'],['Until time (UTC)','15:00']])
          await phone.getByLabel(label,{exact:true}).fill(value);
        await phone.getByRole('button',{name:'Load saved range'}).click();
        await phone.getByRole('button',{name:'Freeze range for replay'}).click();
        await phone.getByLabel('Replay timeline').fill('12');
        await phone.getByRole('button',{name:/Chart settings/}).click();
        const inspect=phone.getByRole('button',{name:'Inspect evidence'});
        await inspect.press('Enter');await phone.getByLabel('Inspection pane').selectOption('comparison');
        await phone.getByLabel('Inspect candle',{exact:true}).fill('4');
        await expect(phone.getByLabel('Selected candle OHLC')).toContainText('1.32289');
        await phone.keyboard.press('Escape');await expect(inspect).toBeFocused();
        await expect(phone.getByLabel('Replay timeline')).toHaveValue('12');
        await measurePlot(phone,'fresh-phone');
        await phone.locator('.terminal-suite-header').scrollIntoViewIfNeeded();
        await phone.screenshot({animations:'disabled',path:join(dir,'fresh-phone.png')});
        await phone.close();
        await page.setViewportSize({width:1536,height:960});
      }
      const modern=await button('Record decision').count()>0;
      if(modern){
        if(!index){
          for(const [name,title,file] of [['Indicators & layers','Indicators & layers','layers'],['Sessions','Session settings','sessions']]){
            await button(name).focus();await page.keyboard.press('Enter');
            const dialog=page.getByRole('dialog',{name:title,exact:true});
            await expect(dialog).toBeVisible();
            if(file==='sessions'){
              const overlay=dialog.getByLabel('Show kill zone').first();
              const initial=await overlay.isChecked();await overlay.setChecked(!initial);await overlay.setChecked(initial);
            }else{
              const indicator=dialog.getByLabel('Indicator',{exact:true});
              const initial=await indicator.inputValue();await indicator.selectOption({index:1});await indicator.selectOption(initial);
            }
            if(process.env.TF_TERMINAL_SHOTS)await page.screenshot({path:join(process.env.TF_TERMINAL_SHOTS,file+'.png')});
            await page.keyboard.press('Escape');await expect(button(name)).toBeFocused();await expect(timeline).toHaveValue(String(12+index));
          }
        }
        await click('Record decision');
      }
      const shot=await posted('/api/chart-snapshots',()=>click(modern?'Attach chart capture':'Save chart evidence'));
      if(modern&&process.env.TF_TERMINAL_SHOTS&&!index)await page.screenshot({path:join(process.env.TF_TERMINAL_SHOTS,'decision.png')});
      const ref=shot.decision_replay;
      assert.equal(ref.session_id,session.id);
      assert.equal(ref.comparison_placement,index?'New pane':'Overlay');
      assert.equal(ref.view,index?'primary':'comparison');
      assert.equal(ref.time,Date.parse('2026-11-13T13:20:00Z')/1000);
      assert.equal(shot.cutoff,session.market.replay_cutoffs[12+index]);
      assert.deepEqual(shot.lanes.map(l=>l.symbol),['EURUSD','GBPUSD']);
      captured.push({id:shot.id,ref,cutoff:shot.cutoff,pins:shot.lanes.map(l=>l.partitions)});
      if(modern&&kind==='trade')await keepManualDecision(page,shot);
      if(kind==='trade'||!modern)await click('Create trade plan');
      if(kind==='trade'){
        for(const field of ['Observation','Evidence','Hypothesis','Setup','Trigger','Invalidation','Management plan'])
          await page.getByRole('textbox',{name:new RegExp(field+'$')}).fill('GBPUSD paired replay '+field);
        for(const [label,value] of [['Entry','1.32289'],['Stop','1.32189'],['Target','1.32489']]){
          await page.getByLabel(label,{exact:true}).fill(value);
        }
        const plan=await posted('/trades',()=>click('Save plan'));
        const version=(await (await page.request.get(origin+'/research/api/trades/'+plan.id)).json()).versions[0].plan;
        assert.deepEqual([version.symbol,version.timeframe,version.snapshot_id,version.knowledge_cutoff_utc],
          ['GBPUSD','M5',shot.id,new Date(shot.cutoff*1000).toISOString()]);
        assert.ok(version.context.includes(shot.lanes[1].feed_id));assert.ok(version.context.includes(shot.lanes[1].price_basis));
        assert.deepEqual([version.entry,version.stop,version.target].map(Number),[1.32289,1.32189,1.32489]);
        await click('Journal this plan revision');
        await page.getByLabel('Title',{exact:true}).fill('Paired replay trade');
        await page.getByLabel('Observation / explanation').fill('Wait for a confirmed paired divergence.');
        const entry=await posted('/journal',()=>click('Append to journal'));
        journal.push({entry:entry.id,trade:plan.id});
      }else{
        if(!modern)await page.getByText('Pass on this setup',{exact:true}).click();
        await page.getByLabel('Reason to pass').fill('No confirmed paired divergence at the cutoff.');
        const entry=await posted('/journal',()=>click('Save pass to journal'));
        journal.push({entry:entry.id});
        if(modern){
          await page.keyboard.press('Escape');
          await page.getByRole('navigation',{name:'Inspector views'}).getByRole('button',{name:'Journal',exact:true}).click();
          const receipt=page.locator('#terminal-journal li').filter({hasText:'No trade'});
          await receipt.locator('summary').click();await receipt.getByRole('button',{name:'Return to decision candle'}).click();
          await expect(timeline).toHaveValue(String(12+index));await click('Learning Journal');
        }
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
      const [history]=await Promise.all([
        page.waitForResponse(r=>r.url().endsWith('/api/journal')&&r.request().method()==='GET'),
        click('Learning Journal'),
      ]);
      assert.ok(history.ok(),await history.text());
      assert.ok((await history.json()).some(entry=>entry.id===item.entry),'Restarted API lost the journal entry');
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
      checks.push({journal:item,...captured[index],reopenedId:saved.id});
      assert.equal(saved.id,id);
    }
    const trades=await (await page.request.get(origin+'/research/api/trades')).json();
    assert.equal(trades.length,1);
    assert.equal(trades[0].id,journal[0].trade);
    const paper=await (await page.request.get(origin+'/research/api/paper/plans/'+trades[0].id)).json();
    assert.deepEqual(paper.events,[]);
    assert.deepEqual(browserErrors,[]);
    return {checks,plots,journalWrites:journal.length,planned:trades.length,paperEvents:paper.events.length};
  }catch(error){
    console.error(browserErrors,await page.locator('body').innerText().catch(()=>'Page closed'));
    throw error;
  }finally{await browser.close();}
}
