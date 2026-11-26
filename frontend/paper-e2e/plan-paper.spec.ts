import {expect,test} from '@playwright/test';
import {readFileSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';

test('plan-linked local paper position across fresh application processes',async({page,request})=>{
  test.setTimeout(90000);
  const phase=process.env.TRADEFORGE_PAPER_PHASE!,file=process.env.TRADEFORGE_PAPER_RECEIPT!;
  const receipts=JSON.parse(readFileSync(file,'utf8'));
  const api='/research/api';
  const click=(name:string)=>page.getByRole('button',{name,exact:true}).click();
  const response=(url:string)=>page.waitForResponse(r=>r.url().endsWith(url)&&r.request().method()==='POST');
  const open=async(id:string)=>{
    await click('Trade Planner');
    await page.getByRole('row').filter({hasText:id}).getByRole('button',{name:'Versions'}).click();
  };
  const fillPlan=async()=>{
    await click('Fill example wording (edit before saving)');
    for(const [k,v] of Object.entries({Entry:'1.16210',Stop:'1.16110',Target:'1.16410'}))await page.getByLabel(k,{exact:true}).fill(v);
    const saved=response('/api/trades');
    await click('Save plan');
    const result=await saved;expect(result.ok(),await result.text()).toBeTruthy();return await result.json();
  };
  await page.addInitScript(()=>{for(const [k,v] of Object.entries({source:'DUKASCOPY_SAVED',timeframe:'M5',timezone:'UTC'}))localStorage.setItem('tradeforge.workspace.'+k,v);});
  await page.goto('/');
  if(phase==='signal'){
    await click('Signal Explorer');
    const item=page.locator('.signal-results li').filter({hasText:'bearish'}).filter({hasText:'Invalidated'}).first();
    await item.getByRole('button',{name:/^Confirmed ·/}).click();
    await click('Create plan draft');
    const trade=await fillPlan();
    receipts.signal=trade.id;
    expect(trade.plan.signal_reference).toEqual({event_key:'7e9485b627eb7c327f80ae78f4e524920c33ef6ffe6802ecdd76397c53985e43',status:'SMT_CONFIRMED'});
  }else if(phase==='signal-reopen'){
    await open(receipts.signal);
    await expect(page.getByText(/Original SMT revision/)).toContainText('SMT_CONFIRMED');
    await expect(page.getByText('No paper orders for this plan.')).toBeVisible();
    const original=page.waitForResponse(r=>r.url().endsWith('7e9485b627eb7c327f80ae78f4e524920c33ef6ffe6802ecdd76397c53985e43?status=SMT_CONFIRMED'));
    await click('Inspect original SMT evidence');
    const signal=await original;
    await expect(page.getByRole('complementary',{name:'Evidence Inspector'})).toContainText('Confirmed');
    const proof=await signal.json();
    expect(proof.chart_cutoff).toBe(Date.parse('2026-11-13T13:25Z')/1000);
    receipts.signal_pins=[proof.provenance.primary.partitions,proof.provenance.comparison.partitions];
    const selection=JSON.parse(readFileSync(resolve('../docs/implementation/evidence/TF-I10A1-dukascopy-nov13/pin.json'),'utf8')).run.selection.series;
    expect(receipts.signal_pins).toEqual([selection['PRIMARY:M5'].partitions,selection['COMPARISON:M5'].partitions]);
    const rejected=await request.post(api+'/paper/plans/'+receipts.signal+'/actions',{data:receipts.events[0].request});
    expect(rejected.status()).toBe(409);
    expect((await (await request.get(api+'/paper/plans/'+receipts.signal)).json()).events).toEqual([]);
  }else{
    if(phase==='entry'){
      for(const hour of [13,14]){
        const imported=await request.post(api+'/market/import/reviewed',{multipart:{file:{name:hour+'h_ticks.bi5',mimeType:'application/octet-stream',buffer:readFileSync(resolve('../storage/t06-input/'+hour+'h_ticks.bi5'))}}});
        expect(imported.ok(),await imported.text()).toBeTruthy();
      }
      await click('Market Terminal');
      await click('Bar replay');
      await page.getByLabel('Historical replay').getByRole('button',{name:'Restart'}).click();
      await expect(page.getByLabel('Historical replay').locator('output')).toContainText('13:05:00.000Z');
      const saved=response('/chart-snapshots');await click('Save chart evidence');
      const chart=await (await saved).json();
      expect(chart.lanes?.[0]?.feed_id).toBe('dukascopy-bi5-v2');
      await expect(page.getByText('Chart capture saved',{exact:true})).toBeVisible();
      await click('Create trade plan');
      const plan=await fillPlan();receipts.trade=plan.id;receipts.snapshot=chart.id;receipts.events=[];
      expect(plan.plan.snapshot_id).toBe(chart.id);expect(plan.plan.knowledge_cutoff_utc).toBe('2025-11-13T13:05:00.000Z');
    }
    await open(receipts.trade);
    const section=page.getByRole('region',{name:'Paper trade review'});
    const path=api+'/paper/plans/'+receipts.trade;
    const events=async()=>(await (await request.get(path)).json()).events;
    expect(await events()).toEqual(receipts.events);
    const stored=await (await request.get(api+'/trades/'+receipts.trade)).json();
    expect(stored.versions.find((v:{version:number})=>v.version===1).plan.snapshot_id).toBe(receipts.snapshot);
    const image=await request.get(api+'/chart-snapshots/'+receipts.snapshot+'/image');
    expect(image.status()).toBe(200);
    if(phase==='close'){
      await click('Create revision from latest');
      await page.getByLabel('04 · Hypothesis').fill('Revised hypothesis after the partial reduction.');
      await click('Save revision');
      await open(receipts.trade);
      await expect(page.getByLabel('To version')).toHaveValue('2');
      await page.getByLabel('To version').selectOption('1');
    }
    const index=receipts.events.length;
    await section.getByLabel('Decision time (UTC)').fill('2025-11-13T13:0'+(5+index)+':00');
    await section.getByLabel('Quantity (EUR)').fill(index===0?'1000':'400');
    const submitted=response(path+'/actions');
    await click(['Submit paper buy','Reduce paper position','Close remaining paper position'][index]);
    const result=await submitted;expect(result.ok(),await result.text()).toBeTruthy();
    const fill=await result.json();
    expect(fill).toMatchObject({request:{version:1,quantity_eur:[1000,400,600][index]},side:['ASK','BID','BID'][index],snapshot_id:receipts.snapshot,
      quote:{source_order:[506,558,595][index],time_ms:Date.parse('2025-11-13T13:00:00Z')+[302028,360038,425141][index]},
      event:{price_units:[116211,116199,116194][index],position_units:[1000,600,0][index],cash_units:[883789000,930268600,999985000][index]},
      realized_usd_units:[0,-4800,-15000][index]});
    const retry=await request.post(path+'/actions',{data:fill.request});
    expect(await retry.json()).toEqual(fill);
    receipts.events.push(fill);expect(await events()).toEqual(receipts.events);
    if(phase==='close'){
      await click('Record plan lesson');
      await page.getByLabel('Title',{exact:true}).fill('Paper EURUSD review');
      await page.getByLabel('Observation / explanation').fill('Reduced 400 EUR and closed the remaining 600 EUR below the planned entry.');
      await click('Append to journal');
      await page.getByText('Planned versus paper execution').click();
      await expect(page.getByRole('region',{name:'Paper trade review'})).toContainText('9999.85000');
      await click('Trade Planner');await click('New Trade Plan');const declined=await fillPlan();
      await page.getByRole('row').filter({hasText:declined.id}).getByRole('button',{name:'No trade'}).click();
      await page.getByLabel('Reason for passing').fill('Required setup was absent.');
      await click('Save no-trade decision');
      await expect(page.getByRole('row').filter({hasText:declined.id})).toContainText('CANCELLED');
      expect((await (await request.get(api+'/paper/plans/'+declined.id)).json()).events).toEqual([]);
      const noTrade=await (await request.get(api+'/trades/'+declined.id)).json();
      expect(noTrade.events.at(-1)).toMatchObject({kind:'NO_TRADE',payload:{reason:'Required setup was absent.'}});
      receipts.no_trade={id:declined.id,reason:noTrade.events.at(-1).payload.reason,orders:0};
      const journal=await (await request.get(api+'/journal')).json();
      const review=journal.find((j:{title:string})=>j.title==='Paper EURUSD review');
      expect(review.plan_reference).toEqual({trade_id:receipts.trade,version:1});
      receipts.review={id:review.id,content:review.content,plan_reference:review.plan_reference};
    }
  }
  writeFileSync(file,JSON.stringify(receipts,null,2));
});
