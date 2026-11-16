import {expect, test, type Page} from "@playwright/test";

const primary = ".primary-chart .chart-input";

async function terminal(page: Page) {
  await page.setViewportSize({width:1536,height:900});
  await page.goto("/");
  await page.getByRole("button",{name:"Market Terminal",exact:true}).click();
  await page.getByRole("combobox",{name:"Market data source"}).selectOption("DEMO");
  await expect(page.locator(primary)).toBeVisible();
}

async function enable(page: Page, query: string, id: string) {
  const search = page.getByLabel("Search indicators");
  await search.fill(query);
  await page.getByRole("combobox",{name:"Add indicator",exact:true}).selectOption(id);
  await page.getByRole("button",{name:"Enable indicator",exact:true}).click();
}

test("multiple canonical indicators persist, update panes and clean up on remount",async ({page},info)=>{
  const errors:string[]=[];
  page.on("pageerror", error=>errors.push(error.message));
  await terminal(page);
  await page.getByRole("combobox",{name:"Indicator",exact:true}).selectOption("ema");
  await page.getByText("Indicator library",{exact:true}).click();
  await enable(page,"relative strength","rsi");
  await enable(page,"average true range","atr");
  const atr=page.getByRole("group",{name:"Average True Range"});
  const updated=page.waitForResponse(res=>res.url().endsWith("/indicators/evaluate") &&
    res.request().postDataJSON().indicators.some((item:{id:string;period:number})=>item.id==="atr"&&item.period===9));
  await atr.getByLabel("Period").fill("9");
  const response=await updated;
  expect(response.status()).toBe(200);
  const body=await response.json();
  expect(body.results.map((result:{id:string})=>result.id)).toEqual(["ema","rsi","atr"]);
  expect(body.results[2].series.ATR.length).toBeGreaterThan(0);
  expect(body.results[2].unit).toBe("price");
  const panes=page.locator(primary+" tr");
  await expect(panes).toHaveCount(6); // Three panes, two separators and the shared time axis.
  await page.locator(primary).scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath("numerical-panes.png")});
  await page.reload();
  await expect(page.getByRole("combobox",{name:"Indicator",exact:true})).toHaveValue("ema");
  await page.getByText("Indicator library",{exact:true}).click();
  await expect(page.getByRole("group",{name:"Average True Range"}).getByLabel("Period")).toHaveValue("9");
  await expect(panes).toHaveCount(6);
  for(let i=0;i<2;i++){
    await page.getByRole("button",{name:"Disable Average True Range"}).click();
    await expect(panes).toHaveCount(4);
    await enable(page,"average true range","atr");
    await expect(panes).toHaveCount(6);
  }
  const switched=page.waitForResponse(res=>res.url().endsWith("/indicators/evaluate")&&
    res.request().postDataJSON().timeframe==="M15"&&res.request().postDataJSON().symbol==="GBPUSD");
  await page.getByRole("button",{name:"M15",exact:true}).click();
  await page.locator(".market-watchlist").getByRole("button",{name:/GBPUSD/}).click();
  expect((await switched).ok()).toBe(true);
  await expect(panes).toHaveCount(6);
  expect(errors).toEqual([]);
});

test("replay submits only closed evidence within the visible cutoff",async ({page})=>{
  await terminal(page);
  await page.getByRole("combobox",{name:"Indicator",exact:true}).selectOption("ema");
  await page.getByLabel("Period",{exact:true}).fill("2");
  await page.getByRole("button",{name:"Bar replay",exact:true}).click();
  const replay=page.getByLabel("Historical replay");
  await replay.getByRole("button",{name:"Restart"}).click();
  const request=page.waitForResponse(res=>{
    if(!res.url().endsWith("/indicators/evaluate"))return false;
    const payload=res.request().postDataJSON();
    return payload.candles.length===2 && payload.indicators[0]?.period===2;
  });
  await replay.getByRole("button",{name:"Next candle"}).click();
  const responded=await request;
  const input=responded.request().postDataJSON();
  const result=await responded.json();
  expect(input.candles.every((bar:{time:number;available_at?:number})=>
    Math.max(bar.time+300,bar.available_at??0)<=input.cutoff)).toBe(true);
  expect(result.results[0].series.EMA).toHaveLength(1);
  expect(result.results[0].series.EMA[0].available_at).toBeLessThanOrEqual(input.cutoff);
});


test("a session overlay exposes provisional and completed backend values",async ({page},info)=>{
  await terminal(page);
  await page.getByRole("button",{name:"H1",exact:true}).click();
  await page.getByText("Indicator library",{exact:true}).click();
  const evaluated=page.waitForResponse(res=>res.url().endsWith("/indicators/evaluate")&&
    res.request().postDataJSON().indicators[0]?.id==="session_new_york");
  await enable(page,"New York","session_new_york");
  const response=await evaluated;
  expect(response.ok()).toBe(true);
  const {results}=await response.json();
  expect(results[0].series.High.some((point:{status:string})=>point.status==="provisional")).toBe(true);
  expect(results[0].series.High.some((point:{status:string})=>point.status==="confirmed")).toBe(true);
  await page.locator(primary).scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath("session-overlay.png")});
});

test("previous week requests a bounded M5 reference beyond chart history",async ({page},info)=>{
  await terminal(page);
  await page.getByText("Indicator library",{exact:true}).click();
  const next=page.waitForResponse(res=>res.url().endsWith("/indicators/evaluate")&&
    res.request().postDataJSON().indicators[0]?.id==="previous_week");
  await enable(page,"previous utc week","previous_week");
  const response=await next;
  expect(response.ok()).toBe(true);
  const data=await response.json();
  expect(data.input.candles).toBeLessThan(2016);
  expect(data.results[0].coverage.retrieval.requested_candles).toBe(2016);
  expect(data.results[0].coverage.status).toBe("COMPLETE");
  await expect(page.getByText("Previous UTC week · Confirmed previous range",{exact:true})).toBeVisible();
  await page.screenshot({path:info.outputPath("continuous-week.png")});
});

test("the terminal distinguishes a complete forex week from missing open-market coverage",async ({page},info)=>{
  // Supply an independently constructed H1 feed fixture to the real evaluator.
  const start=Date.parse("2026-11-02T00:00:00Z")/1000, next=start+7*86400;
  const bars=Array.from({length:168},(_,i)=>i).filter(i=>i<118||i>=166).map(i=>({
    time:start+i*3600,open:10,close:10,high:i===166?14:12,low:i===117?8:9,
  }));
  let incomplete=false;
  await page.route("**/research/api/indicators/evaluate",route=>{
    const data=route.request().postDataJSON();
    return route.continue({postData:JSON.stringify({...data,source:"MT5_SAVED",timeframe:"H1",cutoff:next+3600,
      load_reference:false, candles:[...bars.filter((_,index)=>!incomplete||index!==10),
        {time:next,open:10,close:10,high:40,low:1}]})});
  });
  await terminal(page);
  await page.getByRole("button",{name:"H1",exact:true}).click();
  await page.getByText("Indicator library",{exact:true}).click();
  await enable(page,"previous utc week","previous_week");
  const group=page.getByRole("group",{name:"Previous UTC week"});
  const market=group.getByRole("combobox",{name:"Market schedule",exact:true});
  const check=page.waitForResponse(res=>res.url().endsWith("/indicators/evaluate")&&
    res.request().postDataJSON().indicators[0]?.schedule?.kind==="forex_weekly");
  await market.selectOption("forex_weekly");
  const ready=(await (await check).json()).results[0];
  expect(ready.coverage).toMatchObject({status:"COMPLETE",expected_candles:120,observed_candles:120,
    candidate_high:14,candidate_low:8,available_at:next});
  expect(ready.series.High[0].value).toBe(14);
  await expect(page.getByText("Previous UTC week · Confirmed previous range",{exact:true})).toBeVisible();
  await page.screenshot({path:info.outputPath("forex-confirmed.png")});
  incomplete=true;
  await market.selectOption("continuous");
  await market.selectOption("forex_weekly");
  await expect(page.getByText(/Previous UTC week · Insufficient history: expected market-open candles are missing/)).toBeVisible();
  await page.screenshot({path:info.outputPath("forex-incomplete.png")});
});

test("structure replay paints CHOCH at the confirming step and removes it on rewind",async ({page},info)=>{
  await page.addInitScript(()=>{
    const painted=new WeakMap<HTMLCanvasElement,string[]>();
    const {fillText,clearRect,fillRect}=CanvasRenderingContext2D.prototype;
    CanvasRenderingContext2D.prototype.fillText=function(label,x,y,maxWidth){
      const labels=painted.get(this.canvas)??[];
      if(label==="CHOCH"||label==="BOS")labels.push(label);
      painted.set(this.canvas,labels);
      if(maxWidth===undefined)fillText.call(this,label,x,y);else fillText.call(this,label,x,y,maxWidth);
    };
    CanvasRenderingContext2D.prototype.clearRect=function(x,y,w,h){
      painted.delete(this.canvas);clearRect.call(this,x,y,w,h);
    };
    CanvasRenderingContext2D.prototype.fillRect=function(x,y,w,h){
      if(x===0&&y===0&&w>=this.canvas.clientWidth&&h>=this.canvas.clientHeight)painted.delete(this.canvas);
      fillRect.call(this,x,y,w,h);
    };
    Object.assign(window,{readStructure:(canvas:HTMLCanvasElement)=>painted.get(canvas)??[]});
  });
  const start=Date.parse("2026-11-02T00:00:00Z")/1000, step=300;
  const market={source:"DEMO",synthetic:true,price_basis:"SIMULATED",feed_id:"demo-v1",
    candles:[10,12,10,13,11,14,12,15,13,10,11].map((price,index)=>({
      time:start+index*step,available_at:start+(index+1)*step,
      open:price,high:price,low:price,close:price,
    }))};
  await page.route("**/research/api/replay/sessions",route=>route.fulfill({status:201,json:{
    id:"structure-fixture",market,checkpoint:{position:8,bookmarks:[]},views:{},
  }}));
  await terminal(page);
  await page.getByText("Indicator library",{exact:true}).click();
  await enable(page,"confirmed market structure","market_structure");
  const group=page.getByRole("group",{name:"Confirmed market structure"});
  await group.getByLabel("Left bars").fill("1");
  await group.getByLabel("Right confirmation bars").fill("1");
  await group.getByRole("combobox",{name:"Pivot price",exact:true}).selectOption("close");
  await page.getByRole("button",{name:"Bar replay",exact:true}).click();
  const replay=page.getByLabel("Historical replay");
  await expect(replay.getByLabel("Replay timeline")).toHaveValue("8");
  const rendered=()=>page.locator(primary+" canvas").evaluateAll(canvases=>canvases.flatMap(canvas=>
    (window as unknown as {readStructure:(node:HTMLCanvasElement)=>string[]}).readStructure(canvas as HTMLCanvasElement)));
  await expect.poll(rendered).toContain("BOS");
  expect(await rendered()).not.toContain("CHOCH");
  await page.locator(primary).scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath("structure-before-choch.png")});
  await replay.getByRole("button",{name:"Next candle"}).click();
  await expect(group.getByLabel("Recent structure events")).toContainText("CHOCH · confirmed · 2026-11-02 00:50 UTC");
  await expect.poll(rendered).toContain("CHOCH");
  await page.locator(primary).scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath("structure-after-choch.png")});
  await group.getByLabel("Show breaks",{exact:true}).uncheck();
  await expect.poll(rendered).not.toContain("CHOCH");
  await group.getByLabel("Show breaks",{exact:true}).check();
  await expect.poll(rendered).toContain("CHOCH");
  await replay.getByRole("button",{name:"Previous candle"}).click();
  await expect.poll(rendered).toContain("BOS");
  expect(await rendered()).not.toContain("CHOCH");
  await page.reload();
  await page.getByText("Indicator library",{exact:true}).click();
  await expect(group.getByLabel("Right confirmation bars")).toHaveValue("1");
  await expect(group.getByRole("combobox",{name:"Pivot price",exact:true})).toHaveValue("close");
  await group.getByRole("button",{name:"Disable Confirmed market structure",exact:true}).click();
  await expect(group).toHaveCount(0);
  await expect.poll(rendered).not.toContain("BOS");
});

test("a pending market structure result stays tied to the original chart scope",async ({page})=>{
  let release!:()=>void;
  const hold=new Promise<void>(resolve=>{release=resolve;});
  let entered!:()=>void;
  const started=new Promise<void>(resolve=>{entered=resolve;});
  await page.route("**/research/api/indicators/evaluate",async route=>{
    const payload=route.request().postDataJSON();
    if(payload.symbol==="EURUSD"&&payload.timeframe==="M5"){
      entered();
      await hold;
    }
    try{await route.continue();}catch{ /* The app aborts removed chart scopes. */ }
  });
  await terminal(page);
  await page.getByText("Indicator library",{exact:true}).click();
  await enable(page,"confirmed market structure","market_structure");
  await started;
  const current=page.waitForResponse(response=>response.url().endsWith("/indicators/evaluate")&&
    response.request().postDataJSON().timeframe==="H1"&&response.request().postDataJSON().symbol==="GBPUSD");
  await page.getByRole("button",{name:"H1",exact:true}).click();
  await page.locator(".market-watchlist").getByRole("button",{name:/GBPUSD/}).click();
  const result=await (await current).json();
  expect(result.results[0].provenance).toMatchObject({symbol:"GBPUSD",timeframe:"H1"});
  release();
  await expect(page.locator(".primary-chart .pane-label")).toContainText("GBPUSD · H1");
  const library=page.locator("details.indicator-library");
  if(await library.getAttribute("open")===null)await page.getByText("Indicator library",{exact:true}).click();
  await expect(page.getByRole("group",{name:"Confirmed market structure"}).getByRole("status")).toContainText("Structure");
});

test("older structure pages are bounded and leave the latest chart and state intact",async ({page},info)=>{
  const prices=[10,12,10,13,11,14,12,15,13,10,11];
  const start=Date.parse("2026-11-02T00:00:00Z")/1000;
  await page.route("**/research/api/indicators/evaluate",route=>{
    const data=route.request().postDataJSON();
    return route.continue({postData:JSON.stringify({...data,cutoff:start+250*300,
      candles:Array.from({length:250},(_,index)=>{
        const price=prices[index%prices.length];
        return {time:start+index*300,open:price,close:price,high:price,low:price};
      })})});
  });
  await terminal(page);
  await page.getByText("Indicator library",{exact:true}).click();
  await enable(page,"confirmed market structure","market_structure");
  const group=page.getByRole("group",{name:"Confirmed market structure"});
  await group.getByLabel("Left bars").fill("1");
  await group.getByLabel("Right confirmation bars").fill("1");
  await group.getByRole("combobox",{name:"Structure algorithm"}).selectOption("2");
  await expect(group.getByText(/Trading hours unknown · Source unverified/)).toBeVisible();
  const latest=group.getByLabel("Recent structure events");
  await expect(latest).toBeVisible();
  const current=await latest.innerText();
  const pageResponse=page.waitForResponse(response=>response.url().endsWith("/indicators/evaluate")&&
    response.request().postDataJSON().indicators[0]?.structure_delivery?.pivot_cursor);
  await group.getByRole("button",{name:/Older pivots/}).click();
  const first=(await (await pageResponse).json()).results[0];
  const evidence=group.getByLabel("Historical structure evidence");
  await expect(evidence.getByRole("listitem")).toHaveCount(40);
  const label=await evidence.locator("p").innerText();
  const nextResponse=page.waitForResponse(response=>response.url().endsWith("/indicators/evaluate")&&
    response.request().postDataJSON().indicators[0]?.structure_delivery?.pivot_cursor===
      first.event_page.pivots.next_cursor);
  await evidence.getByRole("button",{name:"Next older page"}).click();
  const second=(await (await nextResponse).json()).results[0];
  expect(second.event_page.snapshot).toBe(first.event_page.snapshot);
  expect(second.event_page.pivots.end).toBe(first.event_page.pivots.start);
  await expect(evidence.locator("p")).not.toHaveText(label);
  expect(await latest.innerText()).toBe(current);
  await evidence.scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath("bounded-structure-evidence.png")});
  await evidence.getByRole("button",{name:"Close evidence"}).click();
  await expect(evidence).toHaveCount(0);
});

test("calendar structure confirms a real reopening BOS and removes it on replay rewind",async ({page},info)=>{
  const close=Date.parse("2026-11-06T22:00:00Z")/1000;
  const reopen=Date.parse("2026-11-08T22:00:00Z")/1000;
  const market={source:"DEMO",synthetic:true,price_basis:"SIMULATED",feed_id:"demo-v1",
    candles:[10,12,10,13,11,14,12,15].map((price,index)=>{
      const time=index<7?close+(index-7)*300:reopen;
      return {time,available_at:time+300,open:price,high:price,low:price,close:price};
    })};
  await page.route("**/research/api/replay/sessions",route=>route.fulfill({status:201,
    json:{id:"weekend-fixture",market,checkpoint:{position:6,bookmarks:[]},views:{}}}));
  await terminal(page);
  await page.getByText("Indicator library",{exact:true}).click();
  await enable(page,"confirmed market structure","market_structure");
  const group=page.getByRole("group",{name:"Confirmed market structure"});
  await group.getByLabel("Left bars").fill("1");
  await group.getByLabel("Right confirmation bars").fill("1");
  await group.getByRole("combobox",{name:"Structure algorithm"}).selectOption("2");
  await group.getByRole("combobox",{name:"Structure market schedule"}).selectOption("forex_weekly");
  await page.getByRole("button",{name:"Bar replay",exact:true}).click();
  const replay=page.getByLabel("Historical replay");
  await expect(replay.getByLabel("Replay timeline")).toHaveValue("6");
  await expect(group.getByLabel("Recent structure events")).not.toContainText("2026-11-08");
  const response=page.waitForResponse(res=>res.url().endsWith("/indicators/evaluate")&&
    res.request().postDataJSON().cutoff===reopen+300);
  await replay.getByRole("button",{name:"Next candle"}).click();
  const result=(await (await response).json()).results[0];
  expect(result.continuity.counts).toEqual({SCHEDULED_CLOSURE:1});
  expect(result.breaks.at(-1)).toMatchObject({type:"BOS",direction:"BULLISH",level_price:14,
    break_price:15,break_time:reopen,available_at:reopen+300,opening_status:"CLOSE_CONFIRMED"});
  await expect(group.getByLabel("Recent structure events")).toContainText("BOS · confirmed · 2026-11-08 22:05 UTC");
  await page.locator(primary).scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath("weekend-structure-replay.png")});
  await replay.getByRole("button",{name:"Previous candle"}).click();
  await expect(group.getByLabel("Recent structure events")).not.toContainText("2026-11-08");
  await expect(group.getByText(/scheduled closure 1/)).toHaveCount(0);
});

test("liquidity replay exposes the sweep only after its closed evidence and keeps it inspectable",async ({page},info)=>{
  const start=Date.parse("2026-11-02T00:00:00Z")/1000, step=300;
  const market={source:"DEMO",synthetic:true,price_basis:"SIMULATED",feed_id:"demo-v1",
    candles:[10,12,10,12,10,11].map((price,index)=>({
      time:start+index*step,available_at:start+(index+1)*step,
      open:price,high:index===5?13:price,low:price,close:price,
    }))};
  await page.route("**/research/api/replay/sessions",route=>route.fulfill({status:201,
    json:{id:"liquidity-fixture",market,checkpoint:{position:4,bookmarks:[]},views:{}}}));
  await terminal(page);
  await page.getByText("Indicator library",{exact:true}).click();
  await enable(page,"liquidity pools","liquidity_sweeps");
  const group=page.getByRole("group",{name:"Liquidity pools and sweeps"});
  await group.getByLabel("Left bars").fill("1");
  await group.getByLabel("Right confirmation bars").fill("1");
  await page.getByRole("button",{name:"Bar replay",exact:true}).click();
  const replay=page.getByLabel("Historical replay");
  const evidence=group.getByLabel("Liquidity evidence",{exact:true});
  await expect(evidence.getByRole("button",{name:/BSL · ACTIVE/})).toBeVisible();
  await expect(evidence.getByRole("button",{name:/BSL · SWEPT/})).toHaveCount(0);
  await page.locator(primary).scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath("liquidity-before.png")});
  await replay.getByRole("button",{name:"Next candle"}).click();
  const hit=evidence.getByRole("button",{name:/BSL · SWEPT/}).first();
  await expect(hit).toBeVisible();
  await hit.click();
  const detail=evidence.getByLabel("Selected liquidity event");
  await expect(detail).toContainText("12 – 12");
  await expect(detail).toContainText("11 / 13 / 11 / 11");
  await expect(detail).toContainText("00:30:00");
  await expect(detail).toContainText("Confirmed pivots");
  await page.locator(primary).scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath("liquidity-after.png")});
  await replay.getByRole("button",{name:"Previous candle"}).click();
  await expect(evidence.getByRole("button",{name:/BSL · ACTIVE/})).toBeVisible();
  await expect(hit).toHaveCount(0);
  await expect(detail).toHaveCount(0);
});

for(const [title,query,id] of [["Liquidity","liquidity pools","liquidity_sweeps"],
  ["FVG","fair value gap","fair_value_gap"]]){
test("a pending "+title+" request cannot replace another instrument or timeframe",async ({page})=>{
  let release!:()=>void;
  const hold=new Promise<void>(resolve=>{release=resolve;});
  let entered!:()=>void;
  const started=new Promise<void>(resolve=>{entered=resolve;});
  await page.route("**/research/api/indicators/evaluate",async route=>{
    const payload=route.request().postDataJSON();
    if(payload.symbol==="EURUSD"&&payload.timeframe==="M5"){entered();await hold;}
    try{await route.continue();}catch{ /* The app aborts old scopes. */ }
  });
  await terminal(page);
  await page.getByText("Indicator library",{exact:true}).click();
  await enable(page,query,id);
  await started;
  const current=page.waitForResponse(response=>response.url().endsWith("/indicators/evaluate")&&
    response.request().postDataJSON().timeframe==="H1"&&response.request().postDataJSON().symbol==="GBPUSD");
  await page.getByRole("button",{name:"H1",exact:true}).click();
  await page.locator(".market-watchlist").getByRole("button",{name:/GBPUSD/}).click();
  const result=await (await current).json();
  expect(result.results[0].provenance).toMatchObject({symbol:"GBPUSD",timeframe:"H1"});
  release();
  await expect(page.locator(".primary-chart .pane-label")).toContainText("GBPUSD · H1");
  const library=page.locator("details.indicator-library");
  if(await library.getAttribute("open")===null)await page.getByText("Indicator library",{exact:true}).click();
  await expect(page.getByLabel(title+" evidence",{exact:true})).toBeVisible();
});
}

test("FVG paints a causal zone and updates its remaining depth during replay",async ({page},info)=>{
  await page.addInitScript(()=>{
    const fill=CanvasRenderingContext2D.prototype.fillRect;
    CanvasRenderingContext2D.prototype.fillRect=function(x,y,w,h){
      if(typeof this.fillStyle==="string" && /^rgba\(45,\s*212,\s*191,/.test(this.fillStyle)&&w>0&&h>0)
        this.canvas.dataset.fvgPainted="true";
      return fill.call(this,x,y,w,h);
    };
  });
  const start=Date.parse("2026-11-02T00:00:00Z")/1000,step=300;
  const market={source:"DEMO",synthetic:true,price_basis:"SIMULATED",feed_id:"demo-v1",
    candles:[[99,100,98,99],[100,106,100,105],[105,110,105,108],[106,107,103.75,106]].map(
      ([open,high,low,close],index)=>({open,high,low,close,time:start+index*step,available_at:start+(index+1)*step}))};
  await page.route("**/research/api/replay/sessions",route=>route.fulfill({status:201,
    json:{id:"fvg-fixture",market,checkpoint:{position:1,bookmarks:[]},views:{}}}));
  await terminal(page);
  await page.getByText("Indicator library",{exact:true}).click();
  await enable(page,"fair value gap","fair_value_gap");
  await enable(page,"liquidity pools","liquidity_sweeps");
  await page.getByRole("button",{name:"Bar replay",exact:true}).click();
  const replay=page.getByLabel("Historical replay");
  const evidence=page.getByLabel("FVG evidence",{exact:true});
  const formed=evidence.getByRole("button",{name:"BULLISH · ACTIVE · 0.0%"});
  const partial=evidence.getByRole("button",{name:"BULLISH · PARTIALLY MITIGATED · 25.0%"});
  await expect(evidence).toContainText("Current FVGs 0 / 0");
  await expect(page.getByLabel("Liquidity evidence",{exact:true})).toBeVisible();
  await page.locator(primary).scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath("fvg-before.png")});
  await replay.getByRole("button",{name:"Next candle"}).click();
  await expect(formed).toBeVisible();
  await expect(page.locator(primary).locator('canvas[data-fvg-painted="true"]').first()).toBeAttached();
  await page.locator(primary).scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath("fvg-formed.png")});
  await replay.getByRole("button",{name:"Next candle"}).click();
  await partial.click();
  await expect(evidence.getByLabel("Selected FVG")).toContainText("100 – 103.75");
  await page.locator(primary).scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath("fvg-mitigated.png")});
  await replay.getByRole("button",{name:"Previous candle"}).click();
  await expect(partial).toHaveCount(0);
  await expect(formed).toBeVisible();
  await replay.getByRole("button",{name:"Previous candle"}).click();
  await expect(evidence).toContainText("Current FVGs 0 / 0");
});

test("FVG selection survives a rolling MT5 refresh and drops corrected evidence",async ({page},info)=>{
  const start=1793577600,step=300;
  const candles=Array.from({length:601},(_,index)=>{
    const [open,high,low,close]=index<588?[99,100,98,99]:
      ([[99,100,98,99],[100,106,100,105],[105,110,105,108],[106,107,103.75,106]][index-588]??[106,107,106,106]);
    return {open,high,low,close,time:start+index*step,available_at:start+(index+1)*step};
  });
  let phase=0;
  await page.route("**/research/api/market/candles?**",route=>{
    const window=phase===0?candles.slice(0,600):candles.slice(1);
    const rows=phase===2?window.map(row=>row.time===candles[589].time?{...row,close:104}:row):window;
    return route.fulfill({json:{source:"MT5",synthetic:false,price_basis:"BID",feed_id:"identity-fixture",candles:rows}});
  });
  const calculated=(match:(bars:typeof candles)=>boolean)=>page.waitForResponse(response=>
    response.url().endsWith("/indicators/evaluate")&&match(response.request().postDataJSON().candles));
  const result=async(response:Awaited<ReturnType<typeof calculated>>)=>
    (await response.json()).results[0].current_fvgs.items.filter((event:{c_time:number})=>event.c_time===candles[590].time);
  await page.goto("/");
  await page.getByRole("button",{name:"Market Terminal",exact:true}).click();
  await page.getByRole("combobox",{name:"Market data source"}).selectOption("MT5");
  await page.getByText("Indicator library",{exact:true}).click();
  const first=calculated(bars=>bars[0]?.time===start);
  await enable(page,"fair value gap","fair_value_gap");
  const original=await result(await first);
  expect(original).toHaveLength(1);
  const evidence=page.getByLabel("FVG evidence",{exact:true});
  const row=evidence.getByRole("button",{name:"BULLISH · PARTIALLY MITIGATED · 25.0%"});
  const detail=evidence.getByLabel("Selected FVG");
  await row.click();
  await expect(detail).toContainText("100 – 103.75");
  const refresh=calculated(bars=>bars[0]?.time===start+step);
  phase=1;
  const refreshed=await result(await refresh);
  expect(refreshed).toHaveLength(1);
  expect(refreshed[0].id).toBe(original[0].id);
  await expect(detail).toContainText("identity policy 2");
  await expect(row).toHaveCount(1);
  await detail.scrollIntoViewIfNeeded();
  await page.screenshot({path:info.outputPath("fvg-selection-rolling.png")});
  const correction=calculated(bars=>bars.some(bar=>bar.time===candles[589].time&&bar.close===104));
  phase=2;
  const corrected=await result(await correction);
  expect(corrected).toHaveLength(1);
  expect(corrected[0].id).not.toBe(original[0].id);
  await expect(detail).toHaveCount(0);
  await expect(row).toBeVisible();
});
