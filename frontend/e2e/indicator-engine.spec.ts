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
