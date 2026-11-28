import assert from 'node:assert/strict';
import {expect} from '@playwright/test';
import {place,save,scene,selectPane} from './native-drawing-journey.mjs';

// Genuine minute-aligned CSV releases cannot naturally supply an adjacent one-second tick.
// Change only the preceding replay clock slot, preserving every bar, source pin and drawing.
export async function installCutoffClock(page,details){
  await page.route(url=>url.pathname.endsWith('/api/replay/sessions')||url.pathname.endsWith('/api/replay/sessions/'+details.session_id),async route=>{
    if(route.request().method()!=='POST'&&!route.request().url().endsWith(details.session_id))return route.continue();
    const response=await route.fetch();
    const session=await response.json();
    if(session.id===details.session_id&&session.market?.replay_cutoffs){
      const clock=session.market.replay_cutoffs;
      assert.equal(clock.at(-1),details.request.end);
      clock[clock.length-2]=details.request.end-1;
      return route.fulfill({response,json:session});
    }
    await route.fulfill({response});
  });
}

export async function cutoffJourney(page,details,verifyBars,existing){
  const entries=existing??[],leaks=[],observations=[];

  const picker=page.getByLabel('Select drawing');
  const button=name=>page.getByRole('button',{name,exact:true});
  for(const view of ['primary','2']){
    const pane=details.panes[view], last=pane.candles.at(-1), prior=pane.candles.at(-3);
    await selectPane(page,view);
    const summary=page.getByText(/^Drawing toolbox/);
    if(!await summary.evaluate(el=>el.closest('details').open))await summary.click();
    let entry=entries.find(item=>item.view===view);
    if(!entry){
      const older=await place(page,details,view,'horizontal',[[0,0]],prior);
      const marks=view==='primary'?[last]:[prior,last];
      const drawing=await place(page,details,view,view==='primary'?'horizontal':'trend',marks.map(()=>[0,0]),marks);
      const label='Release '+view+' '+last.high.toFixed(5);
      await page.getByLabel('Label',{exact:true}).fill(label);
      const version=await save(page,drawing.scope,()=>button('Save style').click());
      entry={view,older:older.id,scope:drawing.scope,item:version.items.find(item=>item.id===drawing.id),
        sources:marks.map(bar=>({bar,partitions:bar.source_partition_indexes.map(i=>pane.partitions[i])}))};
      entries.push(entry);
    }
    await picker.selectOption(entry.item.id);
    await button('Previous candle').click();
    const counts=await verifyBars(page,details.panes,details.request.end-1);
    const options=await picker.locator('option').allTextContents();
    const selected=await picker.inputValue();
    const editActions=await button('Delete drawing').count();
    const undo=await button('Undo drawing edit').isEnabled();
    observations.push({view,counts,options,selected,editActions,undo});
    if(options.some(text=>text.includes(entry.item.label))||selected===entry.item.id||editActions||undo)
      leaks.push({view,options,selected,editActions,undo});
    assert.deepEqual((await scene(page,entry.scope)).items.find(item=>item.id===entry.item.id),entry.item);
    if(!existing&&!leaks.length){
      await picker.selectOption(entry.older);
      page.once('dialog',dialog=>dialog.accept());
      const cleared=await save(page,entry.scope,()=>button('Clear drawings').click());
      assert.deepEqual(cleared.items,[entry.item]);
      const undone=await save(page,entry.scope,()=>button('Undo drawing edit').click());
      assert.deepEqual(undone.items.find(item=>item.id===entry.item.id),entry.item);
    }
    await button('Next candle').click();
    await verifyBars(page,details.panes,details.request.end);
    await picker.selectOption(entry.item.id);
    await expect(page.getByLabel('Label',{exact:true})).toHaveValue(entry.item.label);
    assert.deepEqual((await scene(page,entry.scope)).items.find(item=>item.id===entry.item.id),entry.item);
  }
  return {...(existing?{}:{entries}),leaks,observations,before:details.request.end-1,at:details.request.end};
}

export async function createBoundedSession(page,details){
  const path=new URL('/research/api/replay/sessions',page.url()).href;
  const request={...details.request,end:details.request.end-1};
  const response=await page.request.post(path,{data:request});
  assert.equal(response.status(),201,await response.text());
  const session=await response.json();
  const position=session.market.replay_cutoffs.length-1;
  assert.equal(session.market.replay_cutoffs[position],request.end);
  const saved=await page.request.post(path+'/'+session.id+'/checkpoint',{data:{position,bookmarks:[]}});
  assert.equal(saved.status(),200,await saved.text());
  return session.id;
}

export async function checkServerSessions(page,details,boundedId,entries,resume){
  const observations=[];
  for(const [id,available] of [[boundedId,false],[details.session_id,true]]){
    await resume(page,id);
    const cutoff=details.request.end-(available?0:1);
    await expect(page.getByRole('region',{name:'Historical replay'}).locator('output'))
      .toContainText(new Date(cutoff*1000).toISOString());
    for(const entry of entries){
      await selectPane(page,entry.view);
      const summary=page.getByText(/^Drawing toolbox/);
      if(!await summary.evaluate(el=>el.closest('details').open))await summary.click();
      const picker=page.getByLabel('Select drawing');
      await expect(picker.locator('option[value="'+entry.older+'"]')).toHaveCount(1);
      const option=picker.locator('option[value="'+entry.item.id+'"]');
      if(available)await expect(option).toHaveCount(1);
      if(available){
        await picker.selectOption(entry.item.id);
        await expect(page.getByLabel('Label',{exact:true})).toHaveValue(entry.item.label);
      }
      const selector=entry.view==='primary'?'.primary-chart .chart-input':'[aria-label="Chart view '+entry.view+'"] .chart-input';
      const canvasLabels=await page.evaluate(async ({selector})=>{
        const chart=document.querySelector(selector);
        const found=new Set(),proto=CanvasRenderingContext2D.prototype,original=proto.fillText;
        proto.fillText=function(text,...args){
          if(chart.contains(this.canvas))found.add(text);
          return original.call(this,text,...args);
        };
        try{
          const bars=window.workspaceProbe.series(selector).flat().find(s=>s.type==='Candlestick').data;
          window.workspaceProbe.viewRange(bars.at(-30).time,bars.at(-1).time,selector);
          for(let i=0;i<4;i++)await new Promise(requestAnimationFrame);
        }finally{proto.fillText=original;}
        return [...found].filter(text=>text.startsWith('Release ')||text==='Horizontal line');
      },{selector});
      assert(canvasLabels.includes('Horizontal line'),'Expected a painted eligible control');
      assert.equal(canvasLabels.includes(entry.item.label),available);
      assert.deepEqual((await scene(page,entry.scope)).items.find(item=>item.id===entry.item.id),entry.item);
      observations.push({id,cutoff,view:entry.view,leaked:!available&&await option.count()>0,canvasLabels,options:await picker.locator('option').allTextContents()});
    }
    await page.getByRole('button',{name:'Exit replay',exact:true}).click();
  }
  return observations;
}
