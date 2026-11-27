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

async function openReplay(page:Page,url='/'){
  await installProbe(page);
  await page.goto(url);
  await control(page,'Market Terminal').click();
  await page.getByLabel('Market data source').selectOption(fixture.request.source);
  const saved=page.getByLabel('Resume saved replay');
  const id=fixture.session_id;
  await expect(saved.locator('option[value="'+id+'"]')).toBeAttached({timeout:20000});
  const [opened]=await Promise.all([
    page.waitForResponse(response=>new URL(response.url()).pathname.endsWith('/replay/sessions/'+id)),
    saved.selectOption(id),
  ]);
  expect(opened.ok()).toBeTruthy();
  const paneCounts=Object.entries(fixture.panes).map(([id,lane])=>({
    selector:id==='primary'?'.primary-chart .chart-input':`[aria-label="Chart view ${id}"] .chart-input`,
    candles:lane.candles.length,
  }));
  await expect.poll(()=>page.evaluate(counts=>counts.map(({selector})=>
    window.workspaceProbe.series(selector).flat().find((series:{type:string})=>series.type==='Candlestick')?.data.length,
  ),paneCounts),{timeout:20000}).toEqual(paneCounts.map(lane=>lane.candles));
  await page.getByRole('combobox',{name:'Inspection pane',exact:true}).selectOption('primary');
  await page.getByText(/^Drawing toolbox/).click();
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
  await click(page,primary,.49,.61);
  await expect(select).toHaveValue(id);
  await page.screenshot({path:info.outputPath('pointer-selected.png')});
  const handle=await coords(primary,.5,.61);
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
