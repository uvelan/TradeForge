import {execFileSync} from 'node:child_process';
import {readFileSync,writeFileSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {expect,test,type Locator,type Page} from '@playwright/test';
import {installProbe} from '../e2e/workspace-probe';

type SymbolName='EURUSD'|'GBPUSD';
type Bar={time:number;open:number;high:number;low:number;close:number};
type Point={time:number;value:number};
type Line={type:string;title:string;data:(Bar|Point)[]};
type Partition={id:string;sha256:string};
type Lane={partition:Partition;window:Bar[];bars:Bar[]};
type Signal={event_key:string;status:string;strategy_revision:{id:string}};
const day='2026-11-13';
const step=300;
const at=(hm:string)=>Date.parse(day+'T'+hm+':00Z')/1000;
const timeText=(t:number)=>new Date(t*1000).toISOString().slice(11,16);
const root=resolve('..');
const output=process.env.TRADEFORGE_T10_CHART_OUTPUT!;
const state=join(process.env.TRADEFORGE_PAIR_STORAGE!,'t10-browser-state.json');
const reopen=process.env.TRADEFORGE_PAIR_REOPEN==='1';
const reports=resolve(root,'docs/implementation/plan-integration-evidence');
const watch=JSON.parse(readFileSync(join(reports,'SMT-WATCH-RECEIPTS.json'),'utf8')).uninterrupted;
const eventKey=watch.event_key as string;
const refs:Record<SymbolName,Lane>=JSON.parse(execFileSync(
  process.env.TRADEFORGE_E2E_PYTHON??join(root,'.venv','Scripts','python.exe'),
  ['-m','scripts.u01_comparison_archive',day],{cwd:root,encoding:'utf8'}));
const EUR='EURUSD',GBP='GBPUSD';
const price='.primary-chart',peer='.secondary-chart';
const names=['CCI','%K','%D'] as const;
const columns=['open','high','low','close'] as const;

if(reopen)test.use({storageState:state});

const getBars=(symbol:SymbolName,cutoff:string)=>refs[symbol].window.filter(bar=>bar.time+step<=at(cutoff));
const units=(price:number)=>Math.round(price*100000);
const mean=(values:number[])=>values.reduce((sum,value)=>sum+value,0)/values.length;

// Direct fixed-point inputs from verified Arrow bytes. Each formula consumes only the visible window.
function referenceOscillators(bars:Bar[]):Record<typeof names[number],Point[]> {
  const typical=bars.map(b=>(units(b.high)+units(b.low)+units(b.close))/3);
  const cci=typical.slice(19).map((price,offset)=>{
    const sample=typical.slice(offset,offset+20);
    const center=mean(sample),mad=mean(sample.map(value=>Math.abs(value-center)));
    return {time:bars[offset+19].time,value:mad?(price-center)/(.015*mad):0};
  });
  const k=bars.slice(13).map((bar,offset)=>{
    const sample=bars.slice(offset,offset+14);
    const low=Math.min(...sample.map(row=>units(row.low)));
    const high=Math.max(...sample.map(row=>units(row.high)));
    return {time:bar.time,value:high===low?50:100*(units(bar.close)-low)/(high-low)};
  });
  const d=k.slice(2).map((point,offset)=>({time:point.time,value:mean(k.slice(offset,offset+3).map(row=>row.value))}));
  return {CCI:cci,'%K':k,'%D':d};
}

const expectedRevision=(cutoff:string)=>watch.checks.find(([time]:string[])=>time===cutoff);
const evidence=(page:Page)=>page.getByRole('region',{name:'Recorded SMT at chart cutoff'});
const button=(place:Page|Locator,name:string)=>place.getByRole('button',{name,exact:true});
const choose=(place:Page|Locator,name:string)=>place.getByRole('combobox',{name,exact:true});
const replayBar=(page:Page)=>page.getByLabel('Historical replay',{exact:true});
const plotted=(page:Page,scope:string)=>page.evaluate(sel=>window.workspaceProbe.series(sel+' .chart-input'),scope) as Promise<Line[][]>;
const chartImage=(page:Page,name:string)=>page.locator('.terminal-chart-columns').screenshot({path:join(output,name+'.png')});
const receiptImage=(page:Page,name:string)=>evidence(page).screenshot({path:join(output,name+'.png')});
const row=(page:Page,scope:string)=>page.locator(scope).getByLabel('Chart data',{exact:true});
const archived=(page:Page,symbol:SymbolName)=>page.locator('.market-watchlist button').filter({hasText:symbol}).click();

async function watchPair(page:Page) {
  await button(page,'Data Library').click();
  const panel=page.locator('section.card').filter({has:page.getByRole('heading',{name:'Historical SMT watch'})});
  for(const symbol of [EUR,GBP])await panel.getByLabel(symbol+' M5 BID partition').selectOption(refs[symbol].partition.id);
  await panel.getByLabel('Start UTC',{exact:true}).fill(day+'T12:15');
  await panel.getByLabel('End UTC',{exact:true}).fill(day+'T13:40');
  await button(panel,'Watch exact M5 pairs').click();
  await expect(panel.getByText(/EURUSD \/ GBPUSD · COMPLETE · Verified through 2026-11-13T13:40/))
    .toBeVisible({timeout:45000});
}

async function enableIndicators(scope:Page|Locator) {
  await choose(scope,'Indicator').selectOption('cci');
  const library=scope.locator('.indicator-library');
  await library.locator('summary').click();
  await choose(library,'Add indicator').selectOption('stochastic');
  await button(library,'Enable indicator').click();
  await library.locator('summary').click();
}

async function verifyChart(page:Page,scope:string,symbol:SymbolName,cutoff:string) {
  const expected=getBars(symbol,cutoff);
  const osc=referenceOscillators(expected);
  await expect.poll(async()=>{
    const panes=await plotted(page,scope);
    return panes.flat().filter(s=>s.type==='Candlestick').map(s=>s.data.map(p=>{
      const {time,open,high,low,close}=p as Bar;return {time,open,high,low,close};
    }));
  }).toEqual([expected]);
  await expect.poll(async()=>{
    const lines=(await plotted(page,scope)).flat().filter(s=>s.type==='Line');
    return Object.fromEntries(names.map(name=>[name,lines.find(l=>l.title===name)?.data.map(p=>p.time)??[]]));
  }).toEqual(Object.fromEntries(names.map(name=>[name,osc[name].map(p=>p.time)])));
  const panes=await plotted(page,scope);
  const result=Object.fromEntries(names.map(name=>{
    const pane=panes.findIndex(items=>items.some(item=>item.title===name));
    const points=(panes[pane]?.find(item=>item.title===name)?.data??[]) as Point[];
    if(points.length)expect(pane).toBeGreaterThan(0);
    else expect(pane).toBe(-1);
    for(const [index,point] of points.entries())expect(point.value,name+' '+timeText(point.time))
      .toBeCloseTo(osc[name][index].value,8);
    return [name,{pane,expected:osc[name],observed:points}];
  }));
  if(osc['%D'].length)expect(result['%K'].pane).toBe(result['%D'].pane);
  if(osc.CCI.length&&osc['%K'].length)expect(result.CCI.pane).not.toBe(result['%K'].pane);
  return {symbol,cutoff,expectedBarCount:expected.length,observedBarCount:panes[0][0].data.length,
    lastBar:expected.at(-1),oscillators:result};
}

async function hover(page:Page,scope:string,hm:string) {
  const selector=scope+' .chart-input';
  await page.locator(selector).scrollIntoViewIfNeeded();
  const t=at(hm);
  await page.evaluate(({start,end,selector})=>window.workspaceProbe.viewRange(start,end,selector),
    {start:at('13:00'),end:at('15:00'),selector});
  await expect.poll(async()=>{
    const xy=await page.evaluate(({t,selector})=>window.workspaceProbe.point(t,selector),{t,selector});
    await page.mouse.move(xy.x-1,xy.y);
    await page.mouse.move(xy.x,xy.y);
    return row(page,scope).locator('.chart-legend-meta').first().innerText();
  }).toContain(hm+' · UTC');
  await expect(row(page,scope).locator('.chart-legend-meta').first()).toContainText('13 Nov 2026');
}

async function verifyLegend(page:Page,scope:string,hm:string,symbols:SymbolName[]) {
  const legend=row(page,scope);
  await expect(legend.locator('.chart-legend-meta').first()).toContainText(hm+' · UTC');
  await expect(legend.locator('.chart-legend-row strong')).toHaveText(symbols);
  for(const [index,symbol] of symbols.entries()){
    const b=refs[symbol].window.find(bar=>bar.time===at(hm))!;
    await expect(legend.locator('.chart-legend-row').nth(index).locator('span'))
      .toHaveText(columns.map(key=>key[0].toUpperCase()+' '+b[key].toFixed(5)));
  }
  return {time:at(hm),symbols,observed:await legend.innerText()};
}

async function seek(page:Page,hm:string) {
  const replay=replayBar(page);
  await replay.getByLabel('Jump to UTC',{exact:true}).fill(day+'T'+hm);
  await button(replay,'Seek').click();
  await replayAt(page,hm);
}

async function replayAt(page:Page,hm:string) {
  await expect(replayBar(page).locator('output')).toContainText(day+'T'+hm+':00.000Z');
}

async function startReplay(page:Page) {
  const load=page.waitForResponse(r=>r.url().endsWith('/replay/sessions')&&r.request().method()==='POST');
  await button(page,'Bar replay').click();
  const response=await load;
  expect(response.status(),await response.text()).toBe(201);
  const result=await response.json();
  expect(result.request).toMatchObject({start:at('13:00'),end:at('15:00')});
  return result;
}

async function revision(page:Page,cutoff:string) {
  const [,status,id]=expectedRevision(cutoff);
  const region=evidence(page);
  const selected=region.locator('[data-event-key="'+eventKey+'"]');
  await choose(region,'Recorded SMT family').selectOption(eventKey);
  await expect(selected).toHaveAttribute('data-status',status);
  await expect(selected).toHaveAttribute('data-revision-id',id);
  await expect(selected.getByText(id,{exact:true})).toBeVisible();
  await expect(region).toContainText('Chart cutoff '+day+' '+cutoff+':00 UTC');
  return {cutoff,expected:{status,id},observed:{status:await selected.getAttribute('data-status'),
    id:await selected.getAttribute('data-revision-id'),key:await selected.getAttribute('data-event-key')}};
}

async function proof(page:Page,first:SymbolName,second:SymbolName,single=false) {
  const region=evidence(page);
  const summaries=region.locator('[data-event-key="'+eventKey+'"] summary').filter({hasText:/· BID$/});
  await button(region,'Inspect transition candles').click();
  await expect(summaries).toHaveText([first+' · chart primary · BID',second+' · '+(single?'watch peer':'chart comparison')+' · BID']);
  const lanes=[];
  for(const [index,symbol] of [first,second].entries()){
    const block=summaries.nth(index).locator('..');
    if(await block.getAttribute('open')===null)await summaries.nth(index).click();
    const pin=refs[symbol].partition;
    await expect(block).toContainText(pin.id);
    await expect(block).toContainText(pin.sha256);
    await expect(block).toContainText('dukascopy-web-m1-v1 · M5');
    const supporting=refs[symbol].window.find(bar=>bar.time===at('13:20'))!;
    const displayed='Last supporting candle opened '+day+' 13:20:00 UTC · '+
      columns.map(key=>key[0].toUpperCase()+' '+supporting[key]).join(' ');
    await expect(block.getByText(displayed,{exact:true})).toBeVisible();
    const pivots=await block.locator('p').filter({hasText:/^(Previous|Current) pivot/}).allTextContents();
    expect(pivots).toHaveLength(2);
    for(const label of pivots){
      const match=label.match(/(2026-11-13 \d\d:\d\d:\d\d) UTC · ([0-9.]+)/)!;
      expect(match,label).toBeTruthy();
      const bar=refs[symbol].bars.find(b=>b.time===Date.parse(match[1].replace(' ','T')+'Z')/1000);
      expect(bar?.high,label).toBe(Number(match[2])); // bearish family, both pivots use the wick high
    }
    lanes.push({symbol,expectedPin:pin,observed:await block.innerText()});
  }
  return lanes;
}

test('freezes, advances and rewinds genuine M5 charts and saves a swapped paired checkpoint',async({page,context})=>{
  test.skip(reopen);
  test.setTimeout(120000);
  await installProbe(page);
  await page.setViewportSize({width:1536,height:900});
  await page.goto('/?timezone=UTC');
  await watchPair(page);
  await button(page,'Market Terminal').click();
  await choose(page,'Market data source').selectOption('DUKASCOPY_SAVED');
  await page.getByLabel('From UTC',{exact:true}).fill(day);
  await page.getByLabel('From time (UTC)',{exact:true}).fill('13:00');
  await page.getByLabel('Until UTC (exclusive)',{exact:true}).fill(day);
  await page.getByLabel('Until time (UTC)',{exact:true}).fill('15:00');
  await button(page,'Load saved range').click();
  await enableIndicators(page);
  const single=await startReplay(page);
  expect(single.market.partitions).toEqual([refs[EUR].partition]);
  const oscillatorChecks=[], revisions=[], cursors=[];
  for(const cutoff of ['14:05','14:10','14:15','14:20','14:35']) {
    await seek(page,cutoff);
    oscillatorChecks.push(await verifyChart(page,price,EUR,cutoff));
  }
  await button(replayBar(page),'Next candle').click();
  await replayAt(page,'14:40');
  oscillatorChecks.push(await verifyChart(page,price,EUR,'14:40'));
  await button(replayBar(page),'Previous candle').click();
  await replayAt(page,'14:35');
  oscillatorChecks.push(await verifyChart(page,price,EUR,'14:35'));
  await button(replayBar(page),'Last candle').click();
  await replayAt(page,'15:00');
  oscillatorChecks.push(await verifyChart(page,price,EUR,'15:00'));
  await hover(page,price,'14:50');
  cursors.push(await verifyLegend(page,price,'14:50',[EUR]));
  await chartImage(page,'single-eurusd');

  await seek(page,'13:25');
  revisions.push(await revision(page,'13:25'));
  const singleLanes=await proof(page,EUR,GBP,true);
  await button(replayBar(page),'Exit replay').click();
  await choose(page,'Chart layout').selectOption('2');
  const other=page.getByLabel('Chart view 2',{exact:true});
  await choose(other,'View 2 instrument').selectOption(GBP);
  await choose(other,'View 2 timeframe').selectOption('M5');
  await other.getByText('View 2 indicators',{exact:true}).click();
  await enableIndicators(other);
  await page.getByText('Chart synchronization',{exact:true}).click();
  await page.getByRole('checkbox',{name:'Crosshair',exact:true}).check();
  const separate=await startReplay(page);
  expect(separate.views['2'].partitions).toEqual([refs[GBP].partition]);
  await seek(page,'14:55');
  oscillatorChecks.push(await verifyChart(page,price,EUR,'14:55'),await verifyChart(page,peer,GBP,'14:55'));
  await button(replayBar(page),'Next candle').click();
  await replayAt(page,'15:00');
  oscillatorChecks.push(await verifyChart(page,price,EUR,'15:00'),await verifyChart(page,peer,GBP,'15:00'));
  await hover(page,price,'14:50');
  cursors.push(await verifyLegend(page,price,'14:50',[EUR]),await verifyLegend(page,peer,'14:50',[GBP]));
  await chartImage(page,'linked-eurusd-gbpusd');

  await button(replayBar(page),'Exit replay').click();
  await archived(page,GBP);
  await choose(other,'View 2 instrument').selectOption(EUR);
  const reversed=await startReplay(page);
  expect(reversed.market.partitions).toEqual([refs[GBP].partition]);
  expect(reversed.views['2'].partitions).toEqual([refs[EUR].partition]);
  await seek(page,'15:00');
  oscillatorChecks.push(await verifyChart(page,price,GBP,'15:00'),await verifyChart(page,peer,EUR,'15:00'));
  await hover(page,price,'14:50');
  cursors.push(await verifyLegend(page,price,'14:50',[GBP]),await verifyLegend(page,peer,'14:50',[EUR]));
  await chartImage(page,'linked-gbpusd-eurusd');

  await button(replayBar(page),'Exit replay').click();
  await choose(page,'Chart layout').selectOption('1');
  await choose(page,'SMT comparison').selectOption(EUR);
  await choose(page,'Comparison placement').selectOption('New pane');
  const pair=await startReplay(page);
  expect(pair.market.partitions).toEqual([refs[GBP].partition]);
  expect(pair.views.comparison.partitions).toEqual([refs[EUR].partition]);
  await seek(page,'13:15');
  await expect(evidence(page).locator('option[value="'+eventKey+'"]')).toHaveCount(0);
  await expect(evidence(page)).toContainText('Chart cutoff '+day+' 13:15:00 UTC');
  await button(replayBar(page),'Next candle').click();
  await replayAt(page,'13:20');
  revisions.push(await revision(page,'13:20'));
  await receiptImage(page,'candidate-receipt');

  // Delay a genuine response after the backend has served it. Advance once more before delivering it.
  const held=Promise.withResolvers<Signal>(), release=Promise.withResolvers<void>(), delivered=Promise.withResolvers<void>();
  const pattern='**/research/api/indicators/signals?*';
  await page.route(pattern,async route=>{
    const query=new URL(route.request().url()).searchParams;
    if(query.get('as_of')!==String(at('13:25')))return route.continue();
    const actual=await route.fetch();
    const body=await actual.json();
    held.resolve(body.items?.find((s:Signal)=>s.event_key===eventKey));
    await release.promise;
    await route.fulfill({response:actual});
    delivered.resolve();
  });
  await button(replayBar(page),'Next candle').click();
  await replayAt(page,'13:25');
  const outdated=await held.promise;
  expect(outdated).toMatchObject({status:'SMT_CONFIRMED',strategy_revision:{id:expectedRevision('13:25')[2]}});
  await button(replayBar(page),'Next candle').click();
  await replayAt(page,'13:30');
  revisions.push(await revision(page,'13:30'));
  const stale=page.waitForResponse(r=>new URL(r.url()).searchParams.get('as_of')===String(at('13:25')));
  release.resolve();
  await delivered.promise;
  await (await stale).finished();
  await page.unroute(pattern);
  revisions.push(await revision(page,'13:30'));
  await receiptImage(page,'invalidated-receipt');

  await button(replayBar(page),'Previous candle').click();
  await replayAt(page,'13:25');
  revisions.push(await revision(page,'13:25'));
  const pairedLanes=await proof(page,GBP,EUR);
  const panes=await plotted(page,price);
  const pricePanes=panes.map(pane=>pane.filter(s=>s.type==='Candlestick')).filter(pane=>pane.length);
  expect(pricePanes.map(pane=>pane.map(s=>s.title))).toEqual([[''],[EUR]]);
  expect(pricePanes.map(pane=>pane[0].data)).toEqual([getBars(GBP,'13:25'),getBars(EUR,'13:25')]);
  await hover(page,price,'13:20');
  cursors.push(await verifyLegend(page,price,'13:20',[GBP,EUR]));
  await chartImage(page,'swapped-confirmation-chart');
  await receiptImage(page,'swapped-confirmation-receipt');
  await button(replayBar(page),'Save replay position').click();
  await expect(replayBar(page).getByText('Replay position saved',{exact:true})).toBeVisible();
  writeFileSync(join(output,'chart-journey.json'),JSON.stringify({date:day,oscillatorChecks,revisions,cursors,
    firstSession:single.id,checkpoint:pair.id,position:4,singleLanes,pairedLanes,staleResponse:outdated.strategy_revision.id},null,2));
  await context.storageState({path:state});
});

test('reopens the paired frozen confirmation in a new Chromium and API process',async({page})=>{
  test.skip(!reopen);
  test.setTimeout(60000);
  const stored=JSON.parse(readFileSync(join(output,'chart-journey.json'),'utf8'));
  await installProbe(page);
  await page.setViewportSize({width:1536,height:900});
  await page.goto('/');
  await button(page,'Market Terminal').click();
  await expect(choose(page,'Market data source')).toHaveValue('DUKASCOPY_SAVED');
  await expect(choose(page,'SMT comparison')).toHaveValue(EUR);
  const pending=page.waitForResponse(r=>r.url().endsWith('/replay/sessions/'+stored.checkpoint));
  await choose(page,'Resume saved replay').selectOption(stored.checkpoint);
  const response=await pending;
  expect(response.ok()).toBe(true);
  const session=await response.json();
  expect(session).toMatchObject({id:stored.checkpoint,checkpoint:{position:stored.position},
    market:{symbol:GBP,partitions:[refs[GBP].partition]},
    views:{comparison:{symbol:EUR,partitions:[refs[EUR].partition]}}});
  await replayAt(page,'13:25');
  await expect(choose(evidence(page),'Recorded SMT family')).toHaveValue(eventKey);
  const restored=await revision(page,'13:25');
  const lanes=await proof(page,GBP,EUR);
  expect(lanes).toEqual(stored.pairedLanes);
  await expect(choose(page,'Comparison placement')).toHaveValue('New pane');
  await hover(page,price,'13:20');
  const cursor=await verifyLegend(page,price,'13:20',[GBP,EUR]);
  expect(cursor).toEqual(stored.cursors.at(-1));
  await chartImage(page,'reopened-confirmation-chart');
  await receiptImage(page,'reopened-confirmation-receipt');
  writeFileSync(join(output,'chart-reopened.json'),JSON.stringify({checkpoint:session.id,
    position:session.checkpoint.position,revision:restored,lanes,cursor},null,2));
});
