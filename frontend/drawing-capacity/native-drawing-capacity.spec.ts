import {expect,test,type Page,type Response} from '@playwright/test';
import {readFileSync,writeFileSync} from 'node:fs';
import {performance} from 'node:perf_hooks';
import {installProbe} from '../e2e/workspace-probe';
import {installDrawingCapacityProbe} from './drawing-capacity-probe';

type Bar={time:number;open:number;high:number;low:number;close:number};
type Lane={symbol:string;timeframe:string;candles:Bar[]};
type Fixture={session_id:string;panes:Record<string,Lane>;request:{source:string}};
type Anchor={time:number;price:string};
type Drawing={id:string;kind:string;anchors:Anchor[];label:string;color:string;visible:boolean;locked:boolean;created_at:number;updated_at:number};
type Book={schema_version:1;revision:number;items:Drawing[]};

const keys=['primary','2','3','4'];
const panes=['.primary-chart .chart-input',...['2','3','4'].map(view=>'[aria-label="Chart view '+view+'"] .chart-input')];
const counts=[[1,0,0,0],[25,0,0,0],[63,63,62,62]];
const twoFrames=(page:Page)=>page.evaluate(()=>new Promise<void>(done=>requestAnimationFrame(()=>requestAnimationFrame(()=>done()))));
const p95=(values:number[])=>values.toSorted((a,b)=>a-b)[Math.ceil(values.length*.95)-1];
const itemCount=(sizes:number[])=>sizes.reduce((total,size)=>total+size,0);
const scopePath=(scope:string)=>'/research/api/drawings?'+new URLSearchParams({scope});

function seed(lane:Lane,pane:number,size:number):Drawing[]{
  const recent=lane.candles.slice(-22);
  const low=Math.min(...recent.map(bar=>bar.low));
  const high=Math.max(...recent.map(bar=>bar.high));
  const time=recent[5].time;
  return Array.from({length:size},(_,index)=>{
    // Reserve the center band for the unlocked edit and a short vertical drag.
    const fraction=index===0?.49:index%2?.08+index/size*.12:.8+index/size*.12;
    const number=pane*250+index;
    return {id:'00000000-0000-4000-8000-'+String(number).padStart(12,'0'),kind:'horizontal',
      anchors:[{time,price:(low+(high-low)*fraction).toFixed(5)}],
      label:'Capacity '+number,color:'#c1'+number.toString(16).padStart(4,'0'),
      visible:true,locked:index!==0,created_at:time,updated_at:time};
  });
}

async function readBook(page:Page,path:string):Promise<Book>{
  const result=await page.request.get(path);
  expect(result.ok()).toBeTruthy();
  return result.json();
}
const controls=(page:Page)=>page.getByRole('button',{name:'Market Terminal',exact:true});
const planner=(page:Page)=>page.getByRole('button',{name:'Trade Planner',exact:true});
const selectDrawing=(page:Page)=>page.getByLabel('Select drawing');

async function verifyPaint(page:Page,palette:string[][]){
  await page.waitForFunction(({panes,palette})=>panes.every((selector,index)=>{
    const plot=window.drawingCapacity.surface(selector);
    if(!plot)return false;
    const colors=new Set(plot.strokes.map(({color})=>color).filter(color=>color.startsWith('#c1')));
    return colors.size===palette[index].length&&palette[index].every(color=>colors.has(color));
  }),{panes,palette},{timeout:10000});
  return page.evaluate(panes=>panes.map(selector=>new Set(
    window.drawingCapacity.surface(selector)?.strokes.map(({color})=>color).filter(color=>color.startsWith('#c1'))
  ).size),panes);
}
async function openReplay(page:Page,fixture:Fixture,palette:string[][]){
  const saved=page.getByLabel('Resume saved replay');
  await expect(saved.locator('option[value="'+fixture.session_id+'"]')).toBeAttached({timeout:20000});
  const started=performance.now();
  const [opened]=await Promise.all([
    page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/replay/sessions/'+fixture.session_id)
      &&r.request().method()==='GET'),
    saved.selectOption(fixture.session_id),
  ]);
  expect(opened.ok()).toBeTruthy();
  await page.waitForFunction(panes=>{
    const lanes=(window as unknown as {drawingCapacityReference:Lane[]}).drawingCapacityReference;
    return lanes.every((lane,index)=>{
      const bars=window.workspaceProbe.series(panes[index]).flat().find((s:{type:string})=>s.type==='Candlestick')?.data as Bar[]|undefined;
      return bars?.length===lane.candles.length&&bars.every((bar,j)=>{
        const ref=lane.candles[j];
        return bar.time===ref.time&&bar.open===ref.open&&bar.high===ref.high&&bar.low===ref.low&&bar.close===ref.close;
      });
    });
  },panes,{timeout:25000});
  const painted=await verifyPaint(page,palette);
  await twoFrames(page);
  return {ms:performance.now()-started,painted};
}

async function surface(page:Page,selector:string){
  const row=page.locator(selector+' tr').first();
  await row.evaluate(node=>node.scrollIntoView({block:'center',inline:'center'}));
  const plot=await page.evaluate(selector=>window.drawingCapacity.surface(selector),selector);
  if(!plot)throw Error('Plot unavailable: '+selector);
  expect(plot.top).toBeGreaterThanOrEqual(0);
  return plot;
}

async function panZoom(page:Page,selector:string){
  const plot=await surface(page,selector);
  const x=plot.left+plot.width*.45,y=plot.top+plot.height*.49;
  const before=await page.evaluate(selector=>window.workspaceProbe.range(selector),selector);
  await page.mouse.move(x,y);
  await page.evaluate(()=>window.drawingCapacity.frames());
  await page.mouse.down();
  await page.mouse.move(x+45,y,{steps:12});
  await page.mouse.up();
  const afterPan=await page.evaluate(selector=>window.workspaceProbe.range(selector),selector);
  await page.mouse.wheel(0,-180);
  await twoFrames(page);
  const frames=await page.evaluate(()=>window.drawingCapacity.stopFrames());
  expect(frames.length).toBeGreaterThan(1);
  expect(afterPan).not.toEqual(before);
  await expect.poll(async()=>{
    const zoomed=await page.evaluate(selector=>window.workspaceProbe.range(selector),selector);
    return zoomed.to-zoomed.from;
  }).toBeLessThan(afterPan.to-afterPan.from);
  // Let zoom/draw updates settle before taking a fresh selection timing.
  await page.locator(selector).locator('xpath=..').getByTitle('Show recent candles',{exact:true}).click();
  await twoFrames(page);
  return frames;
}

async function selectOnChart(page:Page,selector:string,target:Drawing){
  const plot=await surface(page,selector);
  const line=plot.strokes.find(stroke=>stroke.color===target.color);
  if(!line)throw Error('Edit target not painted');
  await expect(selectDrawing(page)).toHaveValue('');
  await page.evaluate(selector=>window.drawingCapacity.arm('selection',selector),selector);
  await page.mouse.click(plot.left+plot.width*.33,plot.top+line.row);
  const selected=await page.waitForFunction(id=>{
    const select=document.querySelector<HTMLSelectElement>('.terminal-drawing-tools select[aria-label="Select drawing"]')
      ??Array.from(document.querySelectorAll<HTMLLabelElement>('.terminal-drawing-tools label'))
        .find(label=>label.textContent?.startsWith('Select drawing'))?.querySelector('select');
    const start=window.drawingCapacity.started();
    if(start!==undefined&&select?.value===id)return {ms:performance.now()-start};
  },target.id,{timeout:6000});
  return (await selected.jsonValue()).ms;
}

async function dragOnChart(page:Page,selector:string,target:Drawing,path:string,before:Book){
  const plot=await surface(page,selector);
  const base=plot.strokes.find(stroke=>stroke.color===target.color);
  if(!base)throw Error('Selected edit target not painted');
  const x=plot.left+plot.width*.33,y=plot.top+base.row;
  const delta=base.row<plot.height*.52?20:-20;
  await page.mouse.move(x,y);
  await page.mouse.down();
  await page.evaluate(selector=>window.drawingCapacity.arm('drag',selector),selector);
  await page.mouse.move(x,y+delta);
  const preview=await page.waitForFunction(({selector,row,y})=>{
    const start=window.drawingCapacity.started();
    const strokes=window.drawingCapacity.surface(selector)?.strokes;
    if(start!==undefined&&strokes?.some(stroke=>stroke.color==='#ffdc86'&&
      Math.abs(stroke.row-row)>4&&Math.abs(stroke.row-y)<4))return {ms:performance.now()-start};
  },{selector,row:base.row,y:base.row+delta},{timeout:6000});
  const dragMs=(await preview.jsonValue()).ms;

  const resourceEntriesBeforeSave=await page.evaluate(selector=>{
    const entries=performance.getEntriesByType('resource').length;
    performance.clearResourceTimings();
    window.drawingCapacity.arm('save',selector);
    return entries;
  },selector);
  const [posted]=await Promise.all([
    page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith(path)),
    page.mouse.up(),
  ]);
  expect(posted.status(),await posted.text()).toBe(200);
  const done=await page.waitForFunction(path=>{
    const start=window.drawingCapacity.started();
    if(start===undefined)return;
    const end=performance.getEntriesByName(new URL(path,location.origin).href)
      .find(entry=>entry.startTime>=start) as PerformanceResourceTiming|undefined;
    if(end?.responseEnd)return {ms:end.responseEnd-start};
  },path,{timeout:6000});
  const saveMs=(await done.jsonValue()).ms;
  const next:Book=await posted.json();
  expect(next.revision).toBe(before.revision+1);
  expect(next.items.slice(1)).toEqual(before.items.slice(1));
  expect(next.items[0]).toMatchObject({id:target.id,kind:target.kind,color:target.color,anchors:[{time:target.anchors[0].time}]});
  expect(next.items[0].anchors).not.toEqual(target.anchors);
  expect(Number(next.items[0].anchors[0].price)).toBeGreaterThan(0);
  expect(Number(next.items[0].anchors[0].price)<Number(target.anchors[0].price)).toBe(delta>0);
  expect(await readBook(page,path)).toEqual(next);
  await page.waitForFunction(({selector,color,expected})=>{
    const row=window.drawingCapacity.surface(selector)?.strokes.find(stroke=>stroke.color===color)?.row;
    return row!==undefined&&Math.abs(row-expected)<4?{row}:undefined;
  },{selector,color:target.color,expected:base.row+delta},{timeout:6000});
  return {dragMs,saveMs,next,resourceEntriesBeforeSave};
}

type Trial={trial:number;warmup:boolean;frames:number[];frameP95Ms:number;clickToSelectionMs:number;
  dragToVisibleMs:number;releaseToSaveMs:number;reopenMs:number;rendered:number[];persisted:number[];
  target:string;before:Anchor;after:Anchor;reopenedRow:{actual:number;projected:number};resourceEntriesBeforeSave:number;unmountedHeapBytes:number;resources:Record<string,number>};
type Tier={drawings:number;interactionPane:string;persisted:number[];rendered:number[];scopes:string[];samples:Trial[]};

test('drawing capacity on a real four-pane saved replay',async({page,browser},info)=>{
  test.setTimeout(900000);
  const fixture:Fixture=JSON.parse(readFileSync(process.env.TRADEFORGE_NATIVE_FIXTURE!,'utf8'));
  const lanes=keys.map(key=>fixture.panes[key]);
  expect(lanes.map(lane=>lane.candles.length)).toEqual([5000,5000,5000,5000]);
  expect(new Set(lanes.map(lane=>lane.symbol+'/'+lane.timeframe)).size).toBe(4);
  const scopes=lanes.map(lane=>[fixture.request.source,lane.symbol,lane.timeframe].join(':'));
  const paths=scopes.map(scopePath);
  const variant=process.env.TRADEFORGE_DRAWING_VARIANT;
  if(variant!=='baseline'&&variant!=='candidate')throw Error('Set TRADEFORGE_DRAWING_VARIANT to baseline or candidate');
  const smoke=process.env.TRADEFORGE_DRAWING_SMOKE==='1';
  const warmups=smoke?0:3,trials=smoke?1:7;
  const errors:string[]=[];
  const report={variant,session:fixture.session_id,bars:[5000,5000,5000,5000],warmups,trials,
    browser:browser.version(),error:'',stage:'init',device:{},exploratory:[] as object[],tiers:[] as Tier[]};
  const publish=()=>writeFileSync(info.outputPath('drawing-capacity.json'),JSON.stringify(report,null,2)+'\n');
  page.on('pageerror',error=>errors.push(error.message));
  await installProbe(page);
  await installDrawingCapacityProbe(page);
  await page.goto('/');
  await page.evaluate(lanes=>{Object.assign(window,{drawingCapacityReference:lanes});},lanes);
  const renderer=await page.context().newCDPSession(page);
  const snapshot=async()=>{
    await renderer.send('HeapProfiler.collectGarbage');
    return (await renderer.send('Runtime.getHeapUsage')).usedSize;
  };
  const lifetime=()=>page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio,
    created:window.workspaceProbe.created,disposed:window.workspaceProbe.disposed,remaining:window.workspaceProbe.counts}));
  const unmount=async()=>{
    await planner(page).click();
    await expect.poll(async()=>Object.values((await lifetime()).remaining).every(count=>count===0)).toBeTruthy();
    return lifetime();
  };
  let closed=false;
  try {
    report.stage='probe native 1,000 drawing requests';
    await planner(page).click();
    // Native POST 250 drawings per distinct scope. An enforced capability limit is a result.
    for(let i=0;i<paths.length;i++){
      const before=await readBook(page,paths[i]);
      const result=await page.request.post(paths[i],{data:{...before,items:seed(lanes[i],i,250)}});
      const after=await readBook(page,paths[i]);
      report.exploratory.push({scope:scopes[i],attempted:250,status:result.status(),persistedBefore:before.items.length,persistedAfter:after.items.length,
        revisionUnchanged:after.revision===before.revision,detail:result.ok()?undefined:await result.json().then(
          body=>Array.isArray(body.detail)?body.detail.map(({type,msg,ctx}:{type:string;msg:string;ctx:object})=>({type,msg,ctx})):body.detail)});
      if(!result.ok())expect(after).toEqual(before);
    }
    publish();

    for(const sizes of counts){
      report.stage='seed '+itemCount(sizes);
      let books:Book[]=[];
      for(let i=0;i<scopes.length;i++){
        const before=await readBook(page,paths[i]);
        const items=seed(lanes[i],i,sizes[i]);
        const result=await page.request.post(paths[i],{data:{...before,items}});
        expect(result.status()).toBe(200);
        const stored=await readBook(page,paths[i]);
        expect(stored.items).toEqual(items);
        books.push(stored);
      }
      const expected=sizes.map((size,index)=>variant==='baseline'&&index!==0?0:size);
      const palette=books.map((book,index)=>book.items.slice(0,expected[index]).map(item=>item.color));
      const index=variant==="candidate"&&sizes[1]?1:0;
      const selector=panes[index],interactionPane=keys[index],path=paths[index];
      const tier:Tier={drawings:itemCount(sizes),interactionPane,persisted:books.map(book=>book.items.length),rendered:[],scopes,samples:[]};
      report.tiers.push(tier);
      await controls(page).click();
      await page.getByLabel('Market data source').selectOption(fixture.request.source);
      report.stage='open '+tier.drawings;
      tier.rendered=(await openReplay(page,fixture,palette)).painted;
      const toolbox=page.locator('.terminal-drawing-tools summary');
      if(!await toolbox.evaluate(node=>node.closest('details')?.open))await toolbox.click();
      publish();
      for(let trial=0;trial<warmups+trials;trial++){
        const stage=(action:string)=>{report.stage=[tier.drawings,trial,action].join(' ');};
        await page.getByLabel('Inspection pane').selectOption(interactionPane);
        stage('pan and zoom');
        const frames=await panZoom(page,selector);
        const target=books[index].items[0];
        stage('chart click selects intended drawing');
        const clickToSelectionMs=await selectOnChart(page,selector,target);
        stage('drag preview and save');
        const {dragMs,saveMs,next,resourceEntriesBeforeSave}=await dragOnChart(page,selector,target,path,books[index]);
        books[index]=next;
        stage('unmount and inspect retained resources');
        const unmounted=await unmount();
        expect(unmounted.created).toBe(unmounted.disposed);
        const unmountedHeapBytes=await snapshot();
        stage('reopen saved revision and verify canvas');
        const response=page.waitForResponse(r=>r.request().method()==='GET'&&r.url().endsWith(path));
        await controls(page).click();
        const reopened=await openReplay(page,fixture,palette);
        const returned:Response=await response;
        expect(await returned.json()).toEqual(next);
        const projectedRow=await page.waitForFunction(({selector,color,price})=>{
          const actual=window.drawingCapacity.surface(selector)?.strokes.find(stroke=>stroke.color===color)?.row;
          const projected=window.workspaceProbe.priceRow(selector,price);
          if(actual!==undefined&&projected!=null&&Math.abs(actual-projected)<1)return {actual,projected};
        },{selector,color:target.color,price:Number(next.items[0].anchors[0].price)},{timeout:6000});
        const reopenedRow=await projectedRow.jsonValue();
        tier.samples.push({trial,warmup:trial<warmups,frames,frameP95Ms:p95(frames),clickToSelectionMs,
          dragToVisibleMs:dragMs,releaseToSaveMs:saveMs,reopenMs:reopened.ms,rendered:reopened.painted,
          persisted:books.map(book=>book.items.length),target:target.id,before:target.anchors[0],after:next.items[0].anchors[0],
          reopenedRow,resourceEntriesBeforeSave,unmountedHeapBytes,resources:unmounted.remaining});
        console.log('Drawing capacity '+variant+' '+tier.drawings+' '+(trial+1)+'/'+(warmups+trials));
        publish();
      }
      await unmount();
    }
    closed=true;
    expect(errors).toEqual([]);
  } catch(error) {
    report.error=String(error);
    await page.screenshot({path:info.outputPath('drawing-capacity-failure.png')}).catch(()=>{});
    throw error;
  } finally {
    report.device={...(await lifetime()),errors,completed:closed};
    publish();
    await renderer.detach();
  }
});
