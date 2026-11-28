import {expect,test,type Page} from '@playwright/test';
import {writeFile} from 'node:fs/promises';
import {installProbe} from './workspace-probe';

const empty={charts:0,subscriptions:0,observers:0,listeners:0,timers:0,frames:0};
type Counts=typeof empty;
type Probe={comparisonSeries:()=>{title:string;data:{time:number;open:number;high:number;low:number;close:number}[]}[];viewRange:(from:number,to:number)=>void;bars:()=>unknown[][];point:(time:number)=>{x:number;y:number};range:()=>unknown;counts:Counts;created:number;disposed:number;retain:boolean;retained:Set<Function>;linkedCursor:number;linkedRange:number};
declare global {interface Window {workspaceProbe:Probe}}
const counts=(page:Page)=>page.evaluate(()=>({...window.workspaceProbe.counts}));
const nav=(page:Page,name:string)=>page.getByRole('button',{name,exact:true}).click();
const chart='.primary-chart .chart-input';
async function enter(page:Page){
  await installProbe(page);
  await page.setViewportSize({width:1536,height:900});
  await page.goto('/');
  await nav(page,'Market Terminal');
  await page.getByLabel('Market data source').selectOption('DEMO');
  await expect(page.locator(chart)).toBeVisible();
}
async function point(page:Page){
  const cells=page.locator(chart+' tr').first().locator('td');
  const widths=await cells.evaluateAll(els=>els.map(el=>el.clientWidth));
  const canvas=cells.nth(widths.indexOf(Math.max(...widths))).locator('canvas').first();
  await canvas.scrollIntoViewIfNeeded();
  const box=(await canvas.boundingBox())!;
  return {x:box.x+box.width*.57,y:box.y+box.height*.48};
}

test('100 StrictMode chart navigation cycles',async({page},info)=>{
  test.setTimeout(300000);
  const errors:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',msg=>{
    if(msg.type()==='error' && /uncaught|unhandled|\w+Error:/.test(msg.text()))errors.push(msg.text());
  });
  await enter(page);
  let held=false;
  const pending=Promise.withResolvers<void>(),release=Promise.withResolvers<void>(),delivered=Promise.withResolvers<void>();
  await page.route('**/research/api/market/candles?*',async route=>{
    const query=new URL(route.request().url()).searchParams;
    if(held||query.get('source')!=='DUKASCOPY_SAVED')return route.continue();
    held=true;pending.resolve();await release.promise;
    await route.fulfill({json:{symbol:'EURUSD',timeframe:'M5',source:'DUKASCOPY_SAVED',feed_id:'obsolete-feed',
      price_basis:'BID',candles:[{time:1700000000,open:1,high:2,low:1,close:2}]}});delivered.resolve();
  });
  const baseline:Record<string,Counts>={};
  let completed=0;
  for(let cycle=0;cycle<100;cycle++){
    const n=[1,2,4][cycle%3];
    await page.getByLabel('Chart layout').selectOption(String(n));
    if(cycle===4){
      await page.getByLabel('Market data source').selectOption('DUKASCOPY_SAVED');
      await pending.promise;
      await page.getByLabel('Market data source').selectOption('DEMO');
      await page.locator('.market-watchlist button').filter({hasText:'GBPUSD'}).click();
      await page.locator('.market-watchlist button').filter({hasText:'EURUSD'}).click();
      await nav(page,'M15');await nav(page,'M5');
    }
    if(cycle===6){
      await nav(page,'Bar replay');
      const replay=page.getByLabel('Historical replay');
      await replay.getByRole('button',{name:'Restart'}).click();
      const old=await replay.locator('output').innerText();
      await replay.getByRole('button',{name:'Next candle'}).click();
      await expect(replay.locator('output')).not.toHaveText(old);
      await nav(page,'Exit replay');
    }
    await expect(page.locator('.terminal-chart-columns .chart-input')).toHaveCount(n);
    const xy=await point(page);await page.mouse.move(xy.x,xy.y);await page.mouse.wheel(0,-65);
    await expect.poll(async()=>{const c=await counts(page);return [c.charts,c.subscriptions,c.frames];}).toEqual([n,3*n,0]);
    const initial=await counts(page);
    expect(initial.observers).toBeGreaterThanOrEqual(n);
    expect(initial.listeners).toBeGreaterThanOrEqual(10*n);
    expect(initial.timers).toBeGreaterThanOrEqual(0);
    baseline[n]??=initial;expect(initial).toEqual(baseline[n]);
    await nav(page,'Trade Planner');
    await expect.poll(()=>counts(page)).toEqual(empty);
    await nav(page,'Market Terminal');
    await expect(page.locator('.terminal-chart-columns .chart-input')).toHaveCount(n);
    await expect.poll(()=>counts(page)).toEqual(baseline[n]);
    if(cycle===4){
      release.resolve();await delivered.promise;
      await expect(page.locator('.primary-chart .pane-label')).toContainText('EURUSD');
      await expect(page.locator('.terminal-inspector')).not.toContainText('obsolete-feed');
    }
    completed++;
  }
  const lifecycle=await page.evaluate(()=>({created:window.workspaceProbe.created,disposed:window.workspaceProbe.disposed}));
  expect(lifecycle.created-lifecycle.disposed).toBe(1);
  expect(lifecycle.disposed).toBeGreaterThan(200);
  expect(errors).toEqual([]);
  const report={completed,baseline,away:empty,final:await counts(page),...lifecycle,errors};
  console.log('U01 soak',JSON.stringify(report));
  await writeFile(info.outputPath('100-cycle-counts.json'),JSON.stringify(report,null,2));
  await info.attach('100-cycle-counts.json',{body:JSON.stringify(report,null,2),contentType:'application/json'});
});

test('resource oracle detects a deliberately retained chart subscription',async({page})=>{
  await enter(page);await page.evaluate(()=>{window.workspaceProbe.retain=true;});
  await nav(page,'Trade Planner');
  await expect.poll(()=>counts(page)).toEqual({...empty,subscriptions:1});
  expect(await page.evaluate(()=>window.workspaceProbe.retained.size)).toBe(1);
});

test('panel preferences, drawing geometry and linked charts',async({page},info)=>{
  await enter(page);
  await nav(page,'M30');
  await page.getByText(/^Drawing toolbox/).click();
  await page.getByLabel('Drawing tool',{exact:true}).selectOption('horizontal');
  async function draw() {
    await page.getByRole('button',{name:'Draw',exact:true}).click();
    const response=page.waitForResponse(r=>r.url().includes('/drawings?')&&r.request().method()==='POST');
    const at=await point(page);await page.mouse.click(at.x,at.y);
    const res=await response;expect(res.ok()).toBe(true);
    return {scene:await res.json(),url:res.url()};
  }
  const drawing=await draw();
  const panels=page.getByText('Panel settings',{exact:true});
  await panels.click();
  const original=await page.locator(chart).elementHandle();
  const created=await page.evaluate(()=>window.workspaceProbe.created);
  await page.getByTitle('Place horizontal level (Alt+H)').click();
  const xy=await point(page);await page.mouse.click(xy.x,xy.y);
  const levels=page.getByText(/^Horizontal levels \(/);
  await expect(levels).toBeVisible();
  const saved=await levels.innerText();
  await page.getByLabel('Watchlist size').selectOption('340');
  await expect(page.locator('.market-watchlist')).toHaveCSS('width','340px');
  for(const [label,selector] of [['Toolbar','.terminal-controls'],['Chart tools','.chart-tool-rail'],['Evidence Inspector','.terminal-inspector']]){
    await page.getByLabel('Show '+label,{exact:true}).uncheck();
    await expect(page.locator(selector)).toBeHidden();
    await page.getByLabel('Show '+label,{exact:true}).check();
  }
  expect(await original!.evaluate(el=>el===document.querySelector('.primary-chart .chart-input'))).toBe(true);
  expect(await page.evaluate(()=>window.workspaceProbe.created)).toBe(created);
  await expect(levels).toHaveText(saved);
  const options=page.locator('.workspace-panel-options');
  await options.locator('summary').focus();
  const panelPixels=await options.locator('fieldset').first().screenshot({animations:'disabled'});
  await panels.click();await page.locator(chart).scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath('desktop-workspace.png')});
  await page.reload();await expect(page.locator('.market-watchlist')).toHaveCSS('width','340px');
  await panels.click();await options.locator('summary').focus();
  const restored=await options.locator('fieldset').first().screenshot({animations:'disabled'});
  const changed=await page.evaluate(async images=>{
    const pixels=await Promise.all(images.map(async data=>{
      const image=new Image();image.src='data:image/png;base64,'+data;await image.decode();
      const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;
      const ctx=canvas.getContext('2d')!;ctx.drawImage(image,0,0);
      return ctx.getImageData(0,0,image.width,image.height);
    }));
    const [a,b]=pixels;if(a.width!==b.width||a.height!==b.height)return 1;
    let differences=0;
    for(let i=0;i<a.data.length;i+=4)
      if([0,1,2].some(c=>Math.abs(a.data[i+c]-b.data[i+c])>16))differences++;
    return differences/(a.width*a.height);
  },[panelPixels.toString('base64'),restored.toString('base64')]);
  expect(changed).toBeLessThanOrEqual(.01);
  await panels.click();
  await page.setViewportSize({width:390,height:844});
  await panels.click();await page.getByLabel('Show Watchlist').uncheck();await page.reload();
  await expect(page.locator('.market-watchlist')).toBeHidden();
  await expect(levels).toHaveText(saved);
  expect(await (await page.request.get(drawing.url)).json()).toEqual(drawing.scene);
  await expect(page.getByLabel('Drawing tool',{exact:true})).toHaveValue('horizontal');
  const narrow=await point(page);
  const range=await page.evaluate(()=>window.workspaceProbe.range());
  await page.mouse.move(narrow.x,narrow.y);await page.mouse.wheel(0,-180);
  await expect.poll(()=>page.evaluate(()=>window.workspaceProbe.range())).not.toEqual(range);
  const dimensions=await page.locator(chart).boundingBox();expect(dimensions!.width).toBeGreaterThan(170);
  expect(dimensions!.x+dimensions!.width).toBeLessThanOrEqual(390);
  await page.screenshot({path:info.outputPath('narrow-workspace.png')});
  await page.setViewportSize({width:1536,height:900});
  await page.getByLabel('Chart layout').selectOption('2');await page.getByText('Chart synchronization').click();
  for(const name of ['Timeframe','Crosshair','Time range','Zoom'])await page.getByRole('checkbox',{name,exact:true}).check();
  await expect(page.locator('.secondary-chart .chart-input')).toBeVisible();
  const linked=await point(page);await page.mouse.move(linked.x-10,linked.y);await page.mouse.move(linked.x,linked.y);
  await page.mouse.wheel(0,-180);
  await expect.poll(()=>page.evaluate(()=>[window.workspaceProbe.linkedCursor>0,window.workspaceProbe.linkedRange>0])).toEqual([true,true]);
  await page.getByRole('combobox',{name:'Indicator',exact:true}).selectOption('ema');
  const secondary=page.getByRole('region',{name:'Chart view 2'});
  await secondary.getByText('View 2 indicators').click();await secondary.getByRole('combobox',{name:'Indicator',exact:true}).selectOption('rsi');
  await page.reload();await expect(page.getByRole('combobox',{name:'Indicator',exact:true})).toHaveValue('ema');
  await secondary.getByText('View 2 indicators').click();
  await expect(secondary.getByRole('combobox',{name:'Indicator',exact:true})).toHaveValue('rsi');
});

test('invalid stored arrangement recovers',async({page})=>{
  await page.goto('/');
  await page.evaluate(()=>{
    for(const [k,v] of [['panel-tools-size','999999'],['panel-watchlist','broken'],['chart-views','0']])
      localStorage.setItem('tradeforge.workspace.'+k,v);
  });
  await page.reload();await nav(page,'Market Terminal');await page.getByText('Panel settings',{exact:true}).click();
  await expect(page.getByLabel('Chart tools size')).toHaveValue('auto');
  await expect(page.getByLabel('Show Watchlist')).toBeChecked();
  await expect(page.getByLabel('Chart layout')).toHaveValue('1');
});
