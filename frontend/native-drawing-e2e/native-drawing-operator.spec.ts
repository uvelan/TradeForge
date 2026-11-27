import {expect,test,type Page,type Locator} from '@playwright/test';
import {readFileSync} from 'node:fs';
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

async function openReplay(page:Page){
  await installProbe(page);
  await page.goto('/');
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

test('first pointer placement after switching the active archive pane belongs to that pane',async({page})=>{
  await openReplay(page);
  const fourth=page.getByRole('region',{name:'Chart view 4'});
  const beforePrimary=await scene(page);
  const beforeFourth=await scene(page,'4');
  await page.getByRole('combobox',{name:'Inspection pane',exact:true}).selectOption('4');
  await control(page,'Draw').click();
  await click(page,fourth,.48,.67);
  await expect.poll(async()=>(await scene(page,'4')).revision,{timeout:4000}).toBe(beforeFourth.revision+1);
  expect(await scene(page)).toEqual(beforePrimary);
});
