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
