import {expect, test} from '@playwright/test';
import {readFileSync, writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {performance} from 'node:perf_hooks';
import {installProbe} from '../e2e/workspace-probe';

type Bar={time:number;open:number;high:number;low:number;close:number};
type Lane={symbol:string;timeframe:string;candles:Bar[]};
type Fixture={session_id:string;panes:Record<string,Lane>;request:{source:string;symbol:string;timeframe:string}};
const keys=['primary','2','3','4'];

test('native saved replay open, inspect, pan and release',async({page,browser},info)=>{
  test.setTimeout(900000);
  const fixture:Fixture=JSON.parse(readFileSync(process.env.TRADEFORGE_NATIVE_FIXTURE!,'utf8'));
  const lanes=keys.map(key=>fixture.panes[key]);
  expect(lanes.map(lane=>lane.candles.length)).toEqual([5000,5000,5000,5000]);
  expect(new Set(lanes.map(lane=>lane.symbol+'/'+lane.timeframe)).size).toBe(4);
  const errors:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  await installProbe(page);
  await page.goto('/');
  // Copy the independent OHLC reference once. Never intercept a market or replay response.
  await page.evaluate(lanes=>{Object.assign(window,{nativeReference:lanes});},lanes);
  const nav=(name:string)=>page.getByRole('button',{name,exact:true}).click();
  const samples=[];
  const raw=await browser.newBrowserCDPSession();
  const renderer=await page.context().newCDPSession(page);
  await nav('Market Terminal');
  await page.getByLabel('Market data source').selectOption(fixture.request.source);
  for(let cycle=0;cycle<30;cycle++){
    const saved=page.getByLabel('Resume saved replay');
    await expect(saved.locator('option[value="'+fixture.session_id+'"]')).toBeAttached({timeout:20000});
    const t0=performance.now();
    const [opened]=await Promise.all([
      page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/replay/sessions/'+fixture.session_id)
        && r.request().method()==='GET'),
      saved.selectOption(fixture.session_id),
    ]);
    expect(opened.ok()).toBeTruthy();
    await page.waitForFunction(()=>{
      const refs=(window as unknown as {nativeReference:Lane[]}).nativeReference;
      return refs.every((lane,i)=>{
        const selector=i===0?'.primary-chart .chart-input':'[aria-label="Chart view '+(i+1)+'"] .chart-input';
        const bars=window.workspaceProbe.series(selector).flat().find((s:{type:string})=>s.type==='Candlestick')?.data as Bar[]|undefined;
        return bars?.length===lane.candles.length&&bars.every((bar,j)=>{
          const r=lane.candles[j];
          return bar.time===r.time&&bar.open===r.open&&bar.high===r.high&&bar.low===r.low&&bar.close===r.close;
        });
      });
    },undefined,{timeout:25000});
    await page.evaluate(()=>new Promise<void>(done=>requestAnimationFrame(()=>requestAnimationFrame(()=>done()))));
    const openMs=performance.now()-t0;
    if(cycle===0)await page.getByTitle('Place horizontal level (Alt+H)').click();
    const cells=page.locator('.primary-chart .chart-input tr').first().locator('td');
    const widths=await cells.evaluateAll(items=>items.map(el=>el.clientWidth));
    const canvas=cells.nth(widths.indexOf(Math.max(...widths))).locator('canvas').first();
    await canvas.scrollIntoViewIfNeeded();
    const box=(await canvas.boundingBox())!;
    const x=box.x+box.width/2,y=box.y+box.height/2;
    if(cycle===0)await page.mouse.click(x,y);
    await expect(page.locator('.rail-count')).toHaveText('1');
    const before=await page.evaluate(()=>window.workspaceProbe.range());
    await page.evaluate(()=>{
      const sample={frames:[] as number[],last:performance.now(),id:0};
      const tick=(now:number)=>{sample.frames.push(now-sample.last);sample.last=now;sample.id=requestAnimationFrame(tick);};
      sample.id=requestAnimationFrame(tick);Object.assign(window,{nativeSample:sample});
    });
    await page.mouse.move(x,y);await page.mouse.down();
    await page.mouse.move(x+45,y,{steps:12});await page.mouse.up();await page.mouse.wheel(0,-180);
    await page.evaluate(()=>new Promise<void>(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r()))));
    const frames=await page.evaluate(()=>{
      const sample=(window as unknown as {nativeSample:{id:number;frames:number[]}}).nativeSample;
      cancelAnimationFrame(sample.id);return sample.frames.slice(1);
    });
    expect(frames.length).toBeGreaterThan(1);
    await expect.poll(()=>page.evaluate(()=>window.workspaceProbe.range())).not.toEqual(before);
    await nav('Trade Planner');
    await expect.poll(()=>page.evaluate(()=>Object.values(window.workspaceProbe.counts).every(n=>n===0))).toBeTruthy();
    await renderer.send('HeapProfiler.collectGarbage');
    const heap=await renderer.send('Runtime.getHeapUsage');
    samples.push({cycle,warmup:cycle<3,openMs,frames,unmountedHeapBytes:heap.usedSize});
    if(cycle!==29)await nav('Market Terminal');
  }
  const device=await page.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio,
    created:window.workspaceProbe.created,disposed:window.workspaceProbe.disposed,remaining:window.workspaceProbe.counts}));
  expect(device.created).toBe(device.disposed);expect(errors).toEqual([]);
  const pids=(await raw.send('SystemInfo.getProcessInfo')).processInfo;
  const port=Number(process.env.TRADEFORGE_E2E_API_PORT);
  const apiLine=execFileSync('netstat',['-ano','-p','tcp'],{encoding:'utf8'}).split(/\r?\n/)
    .find(line=>line.includes('LISTENING')&&line.trim().split(/\s+/)[1]==='127.0.0.1:'+port);
  if(!apiLine)throw Error('API listener missing from memory measurement');
  pids.push({type:'api',id:Number(apiLine.trim().split(/\s+/).at(-1)),cpuTime:0});
  const peaks=JSON.parse(execFileSync('powershell.exe',['-NoProfile','-Command',
    'Get-Process -Id '+pids.map(p=>Number(p.id)).join(',')+' | Select-Object Id,PeakWorkingSet64 | ConvertTo-Json -Compress'],{encoding:'utf8'}));
  const memory=pids.map(p=>({type:p.type,pid:p.id,peakWorkingSetBytes:peaks.find((r:{Id:number})=>r.Id===p.id)?.PeakWorkingSet64}));
  await raw.detach();
  await renderer.detach();
  writeFileSync(info.outputPath('samples.json'),JSON.stringify({device,memory,samples,errors},null,2));
});
