import {expect,test} from '@playwright/test';
import {writeFile} from 'node:fs/promises';
import {installProbe} from '../e2e/workspace-probe';

for(const size of [600,5000]){
  test(`controlled renderer, ${size} candles per pane`,async({page},info)=>{
    test.setTimeout(480000);
    const errors:string[]=[];
    page.on('pageerror',error=>errors.push(error.message));
    await installProbe(page);
    const lanes:Record<string,number>={M5:300,H1:3600,M15:900,H4:14400};
    await page.route('**/research/api/market/candles?*',async route=>{
      const query=new URL(route.request().url()).searchParams;
      const step=lanes[query.get('timeframe')??''];
      if(query.get('source')!=='DEMO'||!step)return route.continue();
      const candles=Array.from({length:size},(_,i)=>{
        const open=1+step/100000+i/100000;
        return {time:1763078400-(size-i)*step,open,high:open+.00030,low:open-.00010,close:open+.00020,volume:10};
      });
      await route.fulfill({json:{source:'DEMO',feed_id:'demo-v1',synthetic:true,price_basis:'SIMULATED',candles}});
    });
    await page.goto('/');
    const nav=(name:string)=>page.getByRole('button',{name,exact:true}).click();
    await nav('Market Terminal');
    await page.getByLabel('Market data source').selectOption('DEMO');
    const cdp=await page.context().newCDPSession(page);
    const heap=async()=>{await cdp.send('HeapProfiler.collectGarbage');return (await cdp.send('Runtime.getHeapUsage')).usedSize;};
    const samples=[];
    const terminal=page.locator('.primary-chart .chart-input');
    for(const panes of [1,2,4]){
      await page.getByLabel('Chart layout').selectOption(String(panes));
      for(let cycle=0;cycle<10;cycle++){
        const series=()=>page.evaluate(count=>Array.from({length:count},(_,index)=>{
          const selector=index===0?'.primary-chart .chart-input':`[aria-label="Chart view ${index+1}"] .chart-input`;
          const series=window.workspaceProbe.series(selector).flat().find((line:{type:string})=>line.type==='Candlestick');
          const bars=(series?.data??[]) as {time:number;open:number;high:number;low:number;close:number}[];
          const step=bars.length>1?bars[1].time-bars[0].time:0;
          const valid=bars.every((bar,i)=>{
            const open=1+step/100000+i/100000;
            return bar.time===1763078400-(bars.length-i)*step&&bar.open===open&&bar.high===open+.00030
              &&bar.low===open-.00010&&bar.close===open+.00020;
          });
          return {length:bars.length,step,valid};
        }),panes);
        const expected=[300,3600,900,14400].slice(0,panes);
        await expect.poll(series).toEqual(expected.map(step=>({step,length:size,valid:true})));
        if(!cycle&&panes===1)await page.getByTitle('Place horizontal level (Alt+H)').click();
        const cells=terminal.locator('tr').first().locator('td');
        const widths=await cells.evaluateAll(items=>items.map(item=>item.clientWidth));
        const canvas=cells.nth(widths.indexOf(Math.max(...widths))).locator('canvas').first();
        await canvas.scrollIntoViewIfNeeded();
        const box=(await canvas.boundingBox())!;
        const x=box.x+box.width/2,y=box.y+box.height/2;
        if(!cycle&&panes===1){
          await page.mouse.click(x,y);
        }
        await expect(page.locator('.rail-count')).toHaveText('1');
        const previousRange=await page.evaluate(()=>window.workspaceProbe.range());
        await page.evaluate(()=>{
          const sample={frames:[] as number[],last:performance.now(),id:0};
          const tick=(now:number)=>{sample.frames.push(now-sample.last);sample.last=now;sample.id=requestAnimationFrame(tick);};
          sample.id=requestAnimationFrame(tick);Object.assign(window,{capacitySample:sample});
        });
        await page.mouse.move(x,y);await page.mouse.down();
        await page.mouse.move(x+45,y,{steps:12});await page.mouse.up();
        await page.mouse.wheel(0,-180);
        await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
        const frames=await page.evaluate(()=>{
          const sample=(window as unknown as {capacitySample:{id:number;frames:number[]}}).capacitySample;
          cancelAnimationFrame(sample.id);return sample.frames.slice(1);
        });
        expect(frames.length).toBeGreaterThan(1);
        await expect.poll(()=>page.evaluate(()=>window.workspaceProbe.range())).not.toEqual(previousRange);
        await nav('Trade Planner');
        await expect.poll(()=>page.evaluate(()=>window.workspaceProbe.counts.charts)).toBe(0);
        samples.push({panes,cycle,warmup:cycle<3,frames,unmountedHeapBytes:await heap()});
        await nav('Market Terminal');
      }
    }
    await nav('Trade Planner');
    await expect.poll(()=>page.evaluate(()=>window.workspaceProbe.counts.charts)).toBe(0);
    const device=await page.evaluate(()=>({dpr:devicePixelRatio,width:innerWidth,height:innerHeight,
      created:window.workspaceProbe.created,disposed:window.workspaceProbe.disposed,remaining:{...window.workspaceProbe.counts}}));
    expect(device.created).toBe(device.disposed);
    expect(Object.values(device.remaining).every(value=>value===0)).toBeTruthy();
    expect(errors).toEqual([]);
    await writeFile(info.outputPath('samples.json'),JSON.stringify({size,device,annotations:1,errors,samples}));
  });
}
