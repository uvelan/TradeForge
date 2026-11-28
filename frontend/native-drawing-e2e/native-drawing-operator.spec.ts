import {expect,test,type Page,type Locator} from '@playwright/test';
import {readFileSync,writeFileSync} from 'node:fs';
import {installProbe} from '../e2e/workspace-probe';

type Point={x:number;y:number};
type Drawing={id:string;kind:string;anchors:{time:number;price:string}[]};
type Scene={revision:number;items:Drawing[]};
const fixturePath=process.env.TRADEFORGE_NATIVE_FIXTURE;
if(!fixturePath)throw Error('Supply the native replay fixture for this store');
const fixture=JSON.parse(readFileSync(fixturePath,'utf8')) as {
  session_id:string;
  request:{source:string;symbol:string;timeframe:string;views:{view:string;symbol:string;timeframe:string}[]};
  panes:Record<string,{candles:unknown[]}>;
};
const plot=(pane:Locator)=>pane.locator('.chart-input');
const scene=async(page:Page,pane:'primary'|'4'='primary'):Promise<Scene>=>{
  const {request}=fixture;
  const lane=pane==='primary'?request:request.views.find(lane=>lane.view===pane)!;
  const scope=[request.source,lane.symbol,lane.timeframe].join(':');
  const response=await page.request.get('/research/api/drawings?'+new URLSearchParams({scope}));
  expect(response.ok()).toBeTruthy();
  return response.json();
};
const control=(page:Page,name:string)=>page.getByRole('button',{name,exact:true});
const selected=(page:Page)=>page.getByRole('combobox',{name:/^Select drawing/});

async function openReplay(page:Page,url='/',archive=fixture){
  await installProbe(page);
  await page.goto(url);
  await control(page,'Market Terminal').click();
  await page.getByLabel('Market data source').selectOption(fixture.request.source);
  const saved=page.getByLabel('Resume saved replay');
  const id=archive.session_id;
  await expect(saved.locator('option[value="'+id+'"]')).toBeAttached({timeout:20000});
  const [opened]=await Promise.all([
    page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/replay/sessions/'+id)),
    saved.selectOption(id),
  ]);
  expect(opened.ok()).toBeTruthy();
  const paneCounts=Object.entries(archive.panes).map(([id,lane])=>({
    selector:id==='primary'?'.primary-chart .chart-input':`[aria-label="Chart view ${id}"] .chart-input`,
    candles:lane.candles.length,
  }));
  await expect.poll(()=>page.evaluate(counts=>counts.map(({selector})=>
    window.workspaceProbe.series(selector).flat().find((series:{type:string})=>series.type==='Candlestick')?.data.length,
  ),paneCounts),{timeout:20000}).toEqual(paneCounts.map(lane=>lane.candles));
  await page.getByRole('combobox',{name:'Inspection pane',exact:true}).selectOption('primary');
  const summary=page.getByText(/^Drawing toolbox/);
  if(!await summary.evaluate(el=>el.closest('details')!.open))await summary.click();
  await page.getByLabel('Drawing tool',{exact:true}).selectOption('horizontal');
}

async function coords(pane:Locator,x:number,y:number):Promise<Point>{
  const row=plot(pane).locator('tr').first();
  const cells=await row.locator('td').evaluateAll(nodes=>nodes.map(node=>node.clientWidth));
  const canvas=row.locator('td').nth(cells.indexOf(Math.max(...cells))).locator('canvas').first();
  await canvas.evaluate(node=>node.scrollIntoView({block:'center',inline:'center'}));
  const box=await canvas.boundingBox();
  if(!box)throw Error('Plot unavailable');
  return {x:box.x+box.width*x,y:box.y+box.height*y};
}
async function click(page:Page,pane:Locator,x:number,y:number){
  const point=await coords(pane,x,y);
  await page.mouse.click(point.x,point.y);
}

test('first pointer placement, selection, edit and undo on a four-pane archive',async({page},info)=>{
  await openReplay(page);
  const primary=page.getByRole('region',{name:'Primary chart'});
  const initial=await scene(page);
  await control(page,'Draw').click();
  await click(page,primary,.49,.61);
  const select=selected(page);
  await expect(select).toHaveValue(/^[a-f0-9-]{36}$/);
  const id=await select.inputValue();
  await expect.poll(async()=>(await scene(page)).revision).toBe(initial.revision+1);
  const created=await scene(page);
  const beforeRange=await page.evaluate(()=>window.workspaceProbe.range());
  const blank=await coords(primary,.58,.79);
  await page.mouse.move(blank.x,blank.y);
  await page.mouse.down();
  await page.mouse.move(blank.x+32,blank.y,{steps:8});
  await page.mouse.up();
  await page.mouse.wheel(0,-180);
  await expect.poll(()=>page.evaluate(()=>window.workspaceProbe.range())).not.toEqual(beforeRange);
  await click(page,primary,.38,.79);
  await expect(select).toHaveValue('');
  // Pan and zoom can change the autoscale on genuine history. Hit the saved price
  // in the current viewport, as an operator clicking the visible line would.
  const price=Number(created.items.find(item=>item.id===id)!.anchors[0].price);
  const linePoint=async(x:number)=>{
    const top=await coords(primary,x,0);
    const row=await page.evaluate(price=>window.workspaceProbe.priceRow('.primary-chart .chart-input',price),price);
    if(row==null)throw Error('Saved drawing price is outside the plot');
    return {x:top.x,y:top.y+row};
  };
  const line=await linePoint(.49);
  await page.mouse.click(line.x,line.y);
  await expect(select).toHaveValue(id);
  await page.screenshot({path:info.outputPath('pointer-selected.png')});
  const handle=await linePoint(.5);
  await page.mouse.move(handle.x,handle.y);
  await page.mouse.down();
  await page.mouse.move(handle.x,handle.y-29,{steps:8});
  await page.mouse.up();
  await expect.poll(async()=>(await scene(page)).revision).toBe(created.revision+1);
  const moved=await scene(page);
  expect(moved.items.find(item=>item.id===id)?.anchors).not.toEqual(created.items.find(item=>item.id===id)?.anchors);
  await control(page,'Undo drawing edit').click();
  await expect.poll(async()=>(await scene(page)).items).toEqual(created.items);
});

async function tabToPlot(page:Page,pane:Locator){
  const input=plot(pane);
  for(let step=0;step<24;step++){
    if(await input.evaluate(node=>node===document.activeElement))return;
    await page.keyboard.press('Tab');
  }
  await expect(input).toBeFocused();
}

test('keyboard placement, edit, undo and cancel keep focus visible on the archive plot',async({page},info)=>{
  await openReplay(page);
  const primary=page.getByRole('region',{name:'Primary chart'});
  const before=await scene(page);
  await control(page,'Draw').press('Enter');
  await expect(primary.locator('.drawing-prompt')).toBeVisible();
  await tabToPlot(page,primary);
  await expect(plot(primary)).toHaveCSS('outline-style','solid');
  const target=primary.locator('.drawing-keyboard-target');
  await expect(target).toBeVisible();
  const openingTarget=await target.getAttribute('style');
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowUp');
  await expect(target).not.toHaveAttribute('style',openingTarget!);
  await page.screenshot({path:info.outputPath('keyboard-target.png')});
  await page.keyboard.press('Enter');
  await expect.poll(async()=>(await scene(page)).revision).toBe(before.revision+1);
  await expect(selected(page)).toHaveValue(/^[a-f0-9-]{36}$/);
  const id=await selected(page).inputValue();
  const created=await scene(page);
  await control(page,'Set anchor 1').press('Enter');
  await tabToPlot(page,primary);
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect.poll(async()=>(await scene(page)).revision).toBe(created.revision+1);
  const edited=await scene(page);
  expect(edited.items.find(item=>item.id===id)!.anchors).not.toEqual(created.items.find(item=>item.id===id)!.anchors);
  await control(page,'Undo drawing edit').press('Enter');
  await expect.poll(async()=>(await scene(page)).items).toEqual(created.items);
  const undone=await scene(page);
  await control(page,'Draw').press('Enter');
  await tabToPlot(page,primary);
  const timeline=page.getByRole('slider',{name:'Replay timeline'});
  const cutoff=Number(await timeline.inputValue());
  await page.keyboard.press('Shift+ArrowLeft');
  await expect(timeline).toHaveValue(String(cutoff-1));
  await page.keyboard.press('Shift+ArrowRight');
  await expect(timeline).toHaveValue(String(cutoff));
  await page.keyboard.press('Escape');
  await expect(primary.locator('.drawing-prompt')).toHaveCount(0);
  await expect(plot(primary)).not.toHaveAttribute('tabindex');
  expect(await scene(page)).toEqual(undone);
});

test('first pointer placement after switching the active archive pane belongs to that pane',async({page},info)=>{
  await openReplay(page);
  const fourth=page.getByRole('region',{name:'Chart view 4'});
  const beforePrimary=await scene(page);
  const beforeFourth=await scene(page,'4');
  await page.getByRole('combobox',{name:'Inspection pane',exact:true}).selectOption('4');
  const lane=fixture.request.views.find(lane=>lane.view==='4')!;
  const toolbox=page.locator('.terminal-drawing-tools');
  await expect(toolbox.locator('summary')).toContainText(`Chart view 4 · ${lane.symbol} ${lane.timeframe}`);
  await control(page,'Draw').click();
  await expect(fourth.locator('.drawing-prompt')).toBeVisible();
  await expect(page.getByRole('region',{name:'Primary chart'}).locator('.drawing-prompt')).toHaveCount(0);
  await toolbox.screenshot({path:info.outputPath('companion-toolbox.png')});
  await click(page,fourth,.48,.67);
  await expect.poll(async()=>(await scene(page,'4')).revision,{timeout:4000}).toBe(beforeFourth.revision+1);
  expect(await scene(page)).toEqual(beforePrimary);
});

test('keyboard target announces the exact persisted archive anchor with wick snap on and off',async({page},info)=>{
  await openReplay(page,'/?timezone=UTC');
  const primary=page.getByRole('region',{name:'Primary chart'});
  const readout=primary.locator('.drawing-prompt output');
  const snap=page.getByLabel('Snap to wick');
  const selector='.primary-chart .chart-input';
  const candles=fixture.panes.primary.candles as {time:number;high:number;low:number}[];
  const utc=new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',dateStyle:'medium',timeStyle:'short'});
  const before=await scene(page);
  await snap.uncheck();
  await control(page,'Draw').press('Enter');
  await tabToPlot(page,primary);
  await expect(readout).toHaveText(/ · /);
  const startingTime=(await readout.innerText()).split(' · ')[0];
  const first=candles.findIndex(bar=>utc.format(bar.time*1000)===startingTime);
  expect(first).toBeGreaterThanOrEqual(0);
  // Choose an interior grid point from archived OHLC and real chart coordinates.
  const move=await page.evaluate(({selector,bars})=>{
    const probe=window.workspaceProbe;
    const center=probe.drawingPoint(selector,.5,.5);
    for(const [right,bar] of bars.entries()){
      const high=probe.candlePoint(selector,bar.time,bar.high).y;
      const low=probe.candlePoint(selector,bar.time,bar.low).y;
      const down=Math.round(((high+low)/2-center.y)/8);
      const y=center.y+down*8;
      if(high+2<y&&y<low-2)return {right,down};
    }
    throw Error('No interior keyboard step in visible archive candles');
  },{selector,bars:candles.slice(first,first+24)});
  const bar=candles[first+move.right];
  const travel=async()=>{
    for(let i=0;i<move.right;i++)await page.keyboard.press('ArrowRight');
    for(let i=0;i<Math.abs(move.down);i++)await page.keyboard.press(move.down<0?'ArrowUp':'ArrowDown');
  };
  const receipt=[];
  let id='',freePrice=NaN;
  for(const [step,wick] of [false,true,false,true].entries()){
    const create=step===0||step===3;
    if(step){
      await snap.setChecked(wick);
      await control(page,create?'Draw':'Set anchor 1').press('Enter');
      await tabToPlot(page,primary);
    }
    await travel();
    const [announcedTime,announcedPrice]=(await readout.innerText()).split(' · ');
    expect(announcedTime).toBe(utc.format(bar.time*1000));
    if(!step){
      freePrice=Number(announcedPrice);
      expect(freePrice).toBeGreaterThan(bar.low);
      expect(freePrice).toBeLessThan(bar.high);
    }
    const nearest=Math.abs(freePrice-bar.high)<Math.abs(freePrice-bar.low)?bar.high:bar.low;
    const expectedPrice=(wick?nearest:freePrice).toFixed(5);
    const displayedRow=await primary.locator('.drawing-keyboard-target').evaluate(node=>parseFloat((node as HTMLElement).style.top));
    const expectedRow=await page.evaluate(({selector,price})=>window.workspaceProbe.priceRow(selector,price),
      {selector,price:Number(expectedPrice)});
    await page.keyboard.press('Enter');
    const revision=before.revision+step+1;
    await expect.poll(async()=>(await scene(page)).revision).toBe(revision);
    if(create){
      await expect(selected(page)).not.toHaveValue(id);
      id=await selected(page).inputValue();
    }
    const saved=(await scene(page)).items.find(item=>item.id===id)!.anchors[0];
    receipt.push({wick,revision,action:create?'create':'edit',utc:new Date(bar.time*1000).toISOString(),
      high:bar.high,low:bar.low,freePrice,announcedPrice,saved,displayedRow,expectedRow});
    writeFileSync(info.outputPath('keyboard-anchors.json'),JSON.stringify(receipt,null,2));
    expect(saved).toEqual({time:bar.time,price:expectedPrice});
    expect.soft(announcedPrice).toBe(saved.price);
    expect.soft(Math.abs(displayedRow-expectedRow)).toBeLessThan(1);
  }
});

test('focused plot after zoom previews the first Enter and commits only the second',async({page})=>{
  await openReplay(page,'/?timezone=UTC');
  const pane=page.getByRole('region',{name:'Primary chart'});
  const marker=pane.locator('.drawing-keyboard-target');
  const readout=pane.locator('.drawing-prompt output');
  const initial=await scene(page);
  let writes=0;
  page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.endsWith('/drawings'))writes++;});
  await control(page,'Draw').press('Enter');
  await tabToPlot(page,pane);
  await expect(marker).toBeVisible();
  const center=await coords(pane,.5,.5);
  await page.mouse.move(center.x,center.y);
  await page.mouse.wheel(0,-180);
  await expect(marker).toHaveCount(0);
  await expect(readout).toHaveText('');
  await expect(plot(pane)).toBeFocused();

  await page.keyboard.down('Enter');
  await expect(marker).toBeVisible();
  await page.keyboard.down('Enter'); // repeat while still held
  await page.keyboard.up('Enter');
  const [utc,price]=(await readout.innerText()).split(' · ');
  expect(writes).toBe(0);
  expect(await scene(page)).toEqual(initial);
  const formatter=new Intl.DateTimeFormat('en-GB',{timeZone:'UTC',dateStyle:'medium',timeStyle:'short'});
  const bars=fixture.panes.primary.candles as {time:number}[];
  const matching=bars.filter(bar=>formatter.format(bar.time*1000)===utc);
  expect(matching).toHaveLength(1);
  await page.keyboard.press('Enter');
  await expect.poll(async()=>(await scene(page)).revision).toBe(initial.revision+1);
  const saved=await scene(page);
  expect(saved.items.slice(0,-1)).toEqual(initial.items);
  expect(saved.items.at(-1)?.anchors).toEqual([{time:matching[0].time,price}]);
  expect(writes).toBe(1);
});

test('out-of-window trendline move keeps its editor open for an earlier origin',async({page})=>{
  await openReplay(page);
  await control(page,'Previous candle').click();
  const pane=page.getByRole('region',{name:'Primary chart'});
  const {point,bars,id,initial}=await drawTrend(page,pane);
  let writes=0;
  page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.endsWith('/drawings'))writes++;});
  await control(page,'Move drawing').click();
  await point(bars[5]);
  expect(await scene(page)).toEqual(initial);
  expect(writes).toBe(0);
  await expect(pane.locator('.drawing-prompt')).toContainText('new origin');
  await expect(page.getByRole('alert').filter({hasText:'available replay candles'})).toBeVisible();
  await point(bars[2]);
  await expect.poll(async()=>(await scene(page)).revision).toBe(initial.revision+1);
  const saved=await scene(page);
  expect(saved.items.find(i=>i.id===id)?.anchors.map(a=>a.time)).toEqual([bars[2].time,bars[3].time]);
  expect(saved.items.filter(i=>i.id!==id)).toEqual(initial.items.filter(i=>i.id!==id));
  expect(writes).toBe(1);
  await expect(pane.locator('.drawing-prompt')).toHaveCount(0);
  await expect(page.getByRole('alert').filter({hasText:'available replay candles'})).toHaveCount(0);
});

async function drawTrend(page:Page,pane:Locator,selector='.primary-chart .chart-input'){
  const candles=fixture.panes.primary.candles as {time:number;close:number}[];
  const bars=candles.slice(-7,-1);
  const point=async(bar:typeof bars[number])=>{
    await plot(pane).scrollIntoViewIfNeeded();
    const p=await page.evaluate(({selector,bar})=>window.workspaceProbe.candlePoint(selector,bar.time,bar.close),{selector,bar});
    await page.mouse.click(p.x,p.y);
  };
  await page.getByLabel('Drawing tool',{exact:true}).selectOption('trend');
  await control(page,'Draw').click();
  await point(bars[0]);
  await point(bars[1]);
  await expect(selected(page)).toHaveValue(/^[a-f0-9-]{36}$/);
  const id=await selected(page).inputValue();
  const initial=await scene(page);
  const original=initial.items.find(i=>i.id===id)!;
  expect(original.anchors.map(a=>a.time)).toEqual([bars[0].time,bars[1].time]);
  return {point,bars,id,initial};
}

for(const action of ['lock move','restyle companion anchor'] as const){
  test('stale drawing edit after '+action,async({page},info)=>{
    let archive=fixture;
    const companion=action==='restyle companion anchor';
    if(companion){
      const request={...fixture.request,views:[{view:'2',symbol:fixture.request.symbol,timeframe:fixture.request.timeframe}]};
      const response=await page.request.post('/research/api/replay/sessions',{data:request});
      expect(response.status(),await response.text()).toBe(201);
      const session=await response.json();
      const checkpoint=await page.request.post('/research/api/replay/sessions/'+session.id+'/checkpoint',{data:{position:session.market.replay_cutoffs.length-1,bookmarks:[]}});
      expect(checkpoint.ok()).toBeTruthy();
      archive={...fixture,session_id:session.id,panes:{primary:fixture.panes.primary,'2':fixture.panes.primary}};
    }
    await openReplay(page,'/?timezone=UTC',archive);
    await control(page,'Previous candle').click();
    if(companion)await page.getByRole('combobox',{name:'Inspection pane',exact:true}).selectOption('2');
    const pane=page.getByRole('region',{name:companion?'Chart view 2':'Primary chart',exact:true});
    const {point,bars,id,initial}=await drawTrend(page,pane,companion?'[aria-label="Chart view 2"] .chart-input':'.primary-chart .chart-input');
    const bodies:unknown[]=[];
    page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.endsWith('/drawings'))bodies.push(r.postDataJSON());});
    if(companion){
      await control(page,'Set anchor 2').press('Enter');
      await tabToPlot(page,pane);
      await expect(pane.locator('.drawing-keyboard-target')).toBeVisible();
      await page.getByLabel('Label',{exact:true}).fill('Saved companion label');
      await control(page,'Save style').click();
    }else{
      await control(page,'Move drawing').click();
      await point(bars[5]);
      await expect(pane.locator('.drawing-prompt')).toContainText('new origin');
      await expect(page.getByRole('alert').filter({hasText:'available replay candles'})).toBeVisible();
      expect(await scene(page)).toEqual(initial);
      expect(bodies).toHaveLength(0);
      await control(page,'Lock').click();
    }
    await expect.poll(async()=>(await scene(page)).revision).toBe(initial.revision+1);
    const changed=await scene(page);
    const updated=changed.items.find(i=>i.id===id)!;
    expect(updated).toMatchObject(companion?{label:'Saved companion label'}:{locked:true});
    expect(updated.anchors).toEqual(initial.items.find(i=>i.id===id)!.anchors);
    expect(bodies).toHaveLength(1);
    await point(bars[2]);
    await page.keyboard.press('Enter');
    if(bodies.length>1)await expect.poll(async()=>(await scene(page)).revision).toBe(changed.revision+1);
    const after=await scene(page);
    writeFileSync(info.outputPath('stale-edit.json'),JSON.stringify({action,id,initial,changed,after,bodies},null,2));
    expect(bodies).toHaveLength(1);
    expect(after).toEqual(changed);
    await expect(pane.locator('.drawing-prompt,.drawing-keyboard-target')).toHaveCount(0);
    await expect(page.getByRole('status').filter({hasText:'Drawing edit canceled'})).toBeVisible();
    await expect(plot(pane)).not.toHaveAttribute('tabindex','0');
    await openReplay(page,'/?timezone=UTC',archive);
    expect(await scene(page)).toEqual(changed);
    await selected(page).selectOption(id);
    if(companion){
      await expect(page.getByLabel('Label',{exact:true})).toHaveValue('Saved companion label');
      await page.getByRole('combobox',{name:'Inspection pane',exact:true}).selectOption('2');
      await selected(page).selectOption(id);
    }else{
      await control(page,'Unlock').click();
      await expect(control(page,'Move drawing')).toBeEnabled();
    }
    const beforeRetry=await scene(page);
    const count=bodies.length;
    await control(page,'Move drawing').press('Enter');
    await tabToPlot(page,pane);
    await expect(plot(pane)).toBeFocused();
    // Use an earlier valid candle for the explicit retry.
    await point(bars[2]);
    await expect.poll(async()=>(await scene(page)).revision).toBe(beforeRetry.revision+1);
    expect(bodies).toHaveLength(count+1);
    const final=await scene(page);
    expect(final.items.find(i=>i.id===id)).toMatchObject({locked:false,anchors:[{time:bars[2].time},{time:bars[3].time}]});
    if(companion)expect(final.items.find(i=>i.id===id)).toMatchObject({label:'Saved companion label'});
    expect(final.items.filter(i=>i.id!==id)).toEqual(initial.items.filter(i=>i.id!==id));
    writeFileSync(info.outputPath('stale-edit.json'),JSON.stringify({action,id,initial,changed,after,final,bodies},null,2));
  });
}
