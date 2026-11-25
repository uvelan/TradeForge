import {test,expect} from '@playwright/test';
import {readFileSync,writeFileSync} from 'node:fs';

test('review original paper and signal decisions across API restarts',async({page,request})=>{
  const file=process.env.TRADEFORGE_PAPER_RECEIPT!;
  const state=JSON.parse(readFileSync(file,'utf8'));
  const phase=process.env.TRADEFORGE_PAPER_PHASE;
  const reason='Passed because the independent entry condition was absent.';
  if(phase==='review-blind'){
    expect((await request.post('/research/api/trades/'+state.signal+'/decline',{data:{reason}})).ok()).toBeTruthy();
    state.practice={};
  }
  const network:string[]=[];
  page.on('request',r=>{if(r.url().includes('/research/api/'))network.push(new URL(r.url()).pathname)});
  for(const [i,id] of [state.trade,state.signal].entries()){
    const base='/research/api/learning/reviews/'+id+'/1';
    const capture=(method:string,url:string)=>page.waitForResponse(r=>r.url().endsWith(url)&&r.request().method()===method);
    await page.goto('/?practice');
    const reading=capture('GET',base);
    await page.getByRole('link',{name:new RegExp(id+' · v1')}).click();
    const result=await reading;
    expect(result.ok(),await result.text()).toBeTruthy();
    const view=await result.json();
    const memo=state.practice[id]??={};
    if(phase==='review-blind'){
      expect(Object.keys(view).sort()).toEqual(['phase','prompt','prompt_hash']);
      expect(JSON.stringify(view)).not.toContain(reason);
      expect(view.prompt.cutoff).toBe(Date.parse(i?'2026-11-13T13:25Z':'2025-11-13T13:05Z')/1000);
      expect(view.prompt.lanes.map((l:{symbol:string})=>l.symbol)).toEqual(i?['EURUSD','GBPUSD']:['EURUSD']);
      for(const lane of view.prompt.lanes){
        expect(lane.bars.length).toBeGreaterThan(0);
        expect(lane.bars.at(-1).time+300).toBe(view.prompt.cutoff);
      }
      if(i)expect(view.prompt.lanes.map((l:{partitions:unknown})=>l.partitions)).toEqual(state.signal_pins);
      else {
        const snapshot=await (await request.get('/research/api/chart-snapshots/'+state.snapshot)).json();
        expect(view.prompt.snapshot_id).toBe(state.snapshot);
        expect(view.prompt.lanes[0].partitions).toEqual(snapshot.lanes[0].partitions);
      }
      if(!i)expect(view.prompt.plan_version_id).toBe(state.events[0].plan_version_id);
      expect((await request.post(base+'/reveal')).status()).toBe(409);
      await expect(page.getByRole('heading',{name:'Original outcome'})).toHaveCount(0);
      memo.prompt=view.prompt;memo.hash=view.prompt_hash;
      memo.text=i?'Wait for the planned entry conditions.':'Follow the frozen risk budget.';
      await page.getByLabel('What would you do, and why?').fill(memo.text);
      const response=capture('POST',base+'/assess');
      await page.getByRole('button',{name:'Save assessment'}).click();
      const assessed=await (await response).json();
      expect(assessed.phase).toBe('ASSESSED');expect(assessed.revealed).toBeUndefined();
      expect(await (await request.post(base+'/assess',{data:{assessment:memo.text,prompt_hash:memo.hash}})).json()).toEqual(assessed);
    }else{
      expect(view.prompt).toEqual(memo.prompt);expect(view.assessment).toBe(memo.text);
      if(phase==='review-reveal'){
        expect(view.revealed).toBeUndefined();
        const response=capture('POST',base+'/reveal');
        await page.getByRole('button',{name:'Reveal original outcome'}).click();
        const revealed=(await (await response).json()).revealed;
        if(i)expect(revealed).toMatchObject({kind:'NO_TRADE',reason,signal_history:expect.arrayContaining([{status:'SMT_INVALIDATED',event_time:Date.parse('2026-11-13T13:30Z')/1000}])});
        else expect(revealed.receipts).toEqual(state.events);
        memo.outcome=revealed;
        await page.getByLabel('Practice objective').fill('Check confirmation and costs before acting');
        await page.getByLabel('Follow-up date (UTC)').fill('2025-11-14');
        const scheduled=capture('POST',base+'/practice');
        await page.getByRole('button',{name:'Schedule drill'}).click();
        expect((await (await scheduled).json()).phase).toBe('DUE');
      }else{
        expect(view.revealed).toEqual(memo.outcome);
        expect(view.phase).toBe('DUE');
        await page.getByRole('button',{name:'Mark drill completed'}).click();
        await expect(page.getByText(/DRILL_COMPLETED/)).toBeVisible();
      }
    }
  }
  expect(network.every(url=>url.startsWith('/research/api/learning/reviews'))).toBeTruthy();
  writeFileSync(file,JSON.stringify(state));
});
