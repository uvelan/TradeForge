import { expect, test } from "@playwright/test";

test("opens the research dashboard, replays demo bars, and links to the preserved catalog", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "Market Terminal", exact: true }).click();
  const source = page.getByRole("combobox").filter({ has: page.getByRole("option", { name: "DEMO (synthetic)", exact: true }) }).last();
  await expect(source).toHaveValue("MT5");
  await source.selectOption("DEMO");
  const chart = page.locator('[aria-label="Interactive market candlestick chart"] canvas').first();
  await expect(chart).toBeVisible();
  await page.getByRole("button", { name: "Bar replay", exact: true }).click();
  await expect(page.getByRole("button", { name: "Exit replay", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Data Library", exact: true }).click();
  await page.getByRole("link", { name: "Open fixed-point catalog" }).click();
  await expect(page.getByRole("button", { name: "Generate EURUSD simulation" })).toBeVisible();
  expect(errors).toEqual([]);
});


test("shows a saved background SMT watch and stops it from Market Terminal", async ({ page }) => {
  const watch = {primary_symbol:"EURUSD", comparison_symbol:"GBPUSD", timeframe:"M5",
    period:3, alignment_bars:2, tolerance_bps:0.5, confirmation_bars:1};
  let active = true;
  await page.route("**/research/api/indicators/smt/monitor", async route => {
    if (route.request().method() === "POST") {
      expect(route.request().postDataJSON()).toEqual({...watch,enabled:false});
      active = false;
      await route.fulfill({json:{enabled:false}});
      return;
    }
    await route.fulfill({json:{configuration:active ? watch : null,last_poll_at:100}});
  });
  await page.goto("/");
  await page.getByRole("button",{name:"Market Terminal",exact:true}).click();
  await page.getByText("Background SMT",{exact:true}).click();
  await expect(page.getByText(/EURUSD \/ GBPUSD.*Polling/)).toBeVisible();
  await page.getByRole("button",{name:"Stop monitoring"}).click();
  await expect(page.getByRole("button",{name:"Stop monitoring"})).toHaveCount(0);
  expect(active).toBe(false);
});


test("navigates by keyboard and moves recorded signal evidence into a persisted draft", async ({page})=>{
  await page.setViewportSize({width:1366,height:768});
  const pivot=(time:number,price:number)=>({time,price,confirmed_at:time+180});
  const signal={event_key:'a'.repeat(64),id:'fixture',direction:'bearish',leader:'primary',
    primary:{previous:pivot(120,12),current:pivot(360,14)},
    comparison:{previous:pivot(120,25),current:pivot(360,24)},
    confirmed_at:600,status:'SMT_CONFIRMED',version:2,
    transitions:[{status:'SMT_CANDIDATE',time:540},{status:'SMT_CONFIRMED',time:600}],
    rules:{period:2,alignment_bars:0,tolerance_bps:0.5,confirmation_bars:1},
    provenance:{authority:'VERIFIED_SAVED_DATASET',timeframe:'M1',
      primary:{symbol:'EURUSD',source:'DUKASCOPY_SAVED',feed_id:'test',price_basis:'BID'},
      comparison:{symbol:'GBPUSD',source:'DUKASCOPY_SAVED',feed_id:'test',price_basis:'BID'}}};
  await page.route('**/indicators/signals?*',route=>route.fulfill({json:{items:[signal],next_cursor:null}}));
  const bars=Array.from({length:10},(_,i)=>({time:i*60,open:10,high:14,low:9,close:11}));
  await page.route('**/indicators/signals/*/candles?*',route=>route.fulfill({json:{primary:bars,comparison:bars}}));
  await page.route('**/replay/sessions',route=>route.fulfill({json:{id:'fixture-session',
    market:{candles:bars},views:{comparison:{candles:bars}},checkpoint:null}}));
  await page.route('**/indicators/smt',route=>{
    const {primary}=route.request().postDataJSON();
    return route.fulfill({json:{events:primary.length===10?[signal]:[],alerts:[],authority:'CLIENT_SUPPLIED_UNVERIFIED'}});
  });
  await page.goto('/');
  await expect(page.getByRole('button',{name:/Commands/})).toBeVisible();
  await page.keyboard.press('Control+k');
  const palette=page.getByRole('dialog');
  await expect(palette).toBeVisible();
  await palette.getByLabel('Search workspace commands').fill('Signal Explorer');
  await palette.getByLabel('Search workspace commands').press('Enter');
  await page.getByRole('button',{name:/EURUSD \/ GBPUSD/}).click();
  await expect(page.getByRole('heading',{name:'Evidence Inspector'})).toBeVisible();
  await expect(page.getByText('EURUSD · Higher high')).toBeVisible();
  await page.getByRole('button',{name:'Open recorded chart'}).click();
  await expect(page.locator('[aria-label="Evidence Inspector"] canvas').first()).toBeVisible();
  await page.getByRole('button',{name:'Replay recorded evidence'}).click();
  await expect(page.getByText('No visible confirmation at this cutoff')).toBeVisible();
  await page.getByRole('button',{name:'Last candle'}).click();
  await expect(page.getByRole('status').filter({hasText:/^Confirmed$/})).toBeVisible();
  await page.getByRole('button',{name:'Exit replay'}).click();
  const captureStarted=Promise.withResolvers<void>();
  const settleCapture=Promise.withResolvers<void>();
  await page.route('**/research/api/chart-snapshots', async route=>{
    captureStarted.resolve();
    await settleCapture.promise;
    await route.fulfill({status:422,json:{detail:'Standalone capture rejection fixture'}});
  });
  const saveCapture=page.getByRole('button',{name:'Save chart evidence'});
  await saveCapture.click();
  await captureStarted.promise;
  await expect(saveCapture).toBeDisabled();
  settleCapture.resolve();
  await expect(page.getByText('Standalone capture rejection fixture')).toBeVisible();
  await expect(saveCapture).toBeEnabled();
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole('heading',{name:'Evidence Inspector'})).toBeVisible();
  await page.getByRole('button',{name:'Create plan draft'}).click();
  await expect(page.getByRole('combobox',{name:'Instrument',exact:true})).toHaveValue('EURUSD');
  await expect(page.getByRole('combobox',{name:'Timeframe',exact:true})).toHaveValue('M1');
  await expect(page.getByLabel('Entry',{exact:true})).toHaveValue('');
  await page.getByLabel('Entry',{exact:true}).fill('1.125');
  await page.reload();
  await expect(page.getByLabel('Entry',{exact:true})).toHaveValue('1.125');
});


for(const stressed of [false,true])test(
  stressed?"persists a trendline and resumes replay during delayed analytical and persistence responses":
    "persists a trendline and resumes a frozen replay checkpoint", async ({page})=>{
  const errors:string[]=[];
  page.on("pageerror",error=>errors.push(error.message));
  await page.setViewportSize({width:1366,height:768});
  await page.goto("/");
  await page.getByRole("button",{name:"Market Terminal",exact:true}).click();
  await page.getByRole("combobox",{name:"Market data source"}).selectOption("DEMO");
  const row=page.locator(".primary-chart .chart-input tr").first();
  await expect(row.locator("canvas").first()).toBeVisible();
  const widths=await row.locator("td").evaluateAll(cells=>cells.map(cell=>cell.getBoundingClientRect().width));
  const chart=row.locator("td").nth(widths.indexOf(Math.max(...widths)));
  const analytical=Promise.withResolvers<void>();
  const evaluationReady=Promise.withResolvers<void>();
  const persisted=Promise.withResolvers<void>();
  const saveReady=Promise.withResolvers<void>();
  if(stressed){
    await page.route("**/indicators/evaluate",async route=>{
      const response=await route.fetch();
      if(route.request().postDataJSON().indicators.length===8){
        evaluationReady.resolve();
        await analytical.promise;
      }
      await route.fulfill({response});
    });
    await page.route("**/drawings?*",async route=>{
      if(route.request().method()!=="POST")return route.continue();
      const response=await route.fetch();
      saveReady.resolve();
      await persisted.promise;
      await route.fulfill({response});
    });
    const library=page.getByText("Indicator library",{exact:true});
    await library.click();
    const ids=["ema","session_asia","session_london","previous_day","market_structure","fair_value_gap",
      "liquidity_sweeps","order_blocks"];
    for(const id of ids){
      await page.getByLabel("Add indicator").selectOption(id);
      await page.getByRole("button",{name:"Enable indicator"}).click();
    }
    await library.click();
    await evaluationReady.promise;
  }
  await page.getByText(/^Drawing toolbox/).click();
  await page.getByRole("button",{name:"Draw",exact:true}).click();
  async function clickPlot(x:number,y:number){
    const position=await chart.evaluate((canvas,[xFraction,yFraction])=>({
      x:canvas.clientWidth*xFraction,y:canvas.clientHeight*yFraction,
    }),[x,y]);
    await chart.click({position});
  }
  await clickPlot(.45,.38);
  await expect(page.getByText(/Trendline.*select 2/)).toBeVisible();
  if(stressed){
    const refreshed=page.waitForResponse(response=>response.url().endsWith("/indicators/evaluate")&&
      response.request().postDataJSON().indicators.length===8);
    analytical.resolve();
    const response=await refreshed;
    expect(response.ok()).toBeTruthy();
    expect((await response.json()).results).toHaveLength(8);
    await expect(page.getByText(/Trendline.*select 2/)).toBeVisible();
  }
  const drawingResponse=page.waitForResponse(response=>response.request().method()==="POST"&&response.url().includes("/drawings?"));
  await clickPlot(.62,.31);
  if(stressed){
    await saveReady.promise;
    await expect(page.getByRole("button",{name:"Draw",exact:true})).toBeDisabled();
    expect(await page.getByLabel("Select drawing").evaluateAll(selects=>
      selects.every(item=>(item as HTMLSelectElement).value===""))).toBeTruthy();
    persisted.resolve();
  }
  const drawing=await drawingResponse;
  expect(drawing.ok()).toBeTruthy();
  await page.getByText(/^Drawing toolbox/).scrollIntoViewIfNeeded();
  await expect(page.getByLabel("Select drawing")).toHaveValue(/^[a-f0-9-]{36}$/);
  const id=await page.getByLabel("Select drawing").inputValue();
  expect((await drawing.json()).items.some((item:{id:string})=>item.id===id)).toBe(true);
  await page.getByRole("button",{name:"Save chart evidence"}).click();
  await expect(page.getByText("Chart capture saved")).toBeVisible();
  await page.reload();
  await page.getByText(/^Drawing toolbox/).click();
  await expect(page.getByLabel("Select drawing").locator(`option[value="${id}"]`)).toHaveCount(1);
  const replayBuilt=Promise.withResolvers<void>();
  const publishReplay=Promise.withResolvers<void>();
  if(stressed){
    await page.route("**/replay/sessions",async route=>{
      if(route.request().method()!=="POST")return route.continue();
      const response=await route.fetch();
      replayBuilt.resolve();
      await publishReplay.promise;
      await route.fulfill({response});
    });
  }
  const replayResponse=page.waitForResponse(response=>response.request().method()==="POST"&&response.url().endsWith("/replay/sessions"));
  await page.getByRole("button",{name:"Bar replay",exact:true}).click();
  if(stressed){
    await replayBuilt.promise;
    await expect(page.getByLabel("Historical replay")).toHaveCount(0);
    await expect(page.getByRole("button",{name:"Bar replay",exact:true})).toBeDisabled();
    publishReplay.resolve();
  }
  const replay=await replayResponse;
  expect(replay.ok()).toBeTruthy();
  const session=await replay.json();
  const bookmarks:number[]=session.checkpoint?.bookmarks??[];
  const controls=page.getByLabel("Historical replay");
  const timeline=controls.getByLabel("Replay timeline");
  await expect(controls).toBeVisible();
  if(await timeline.inputValue()!=="0")await controls.getByRole("button",{name:"Restart"}).click();
  let position=0;
  do{
    await controls.getByRole("button",{name:"Next candle"}).click();
    await expect(timeline).toHaveValue(String(++position));
  }while(bookmarks.includes(position));
  const bookmark=controls.getByRole("button",{name:"Bookmark candle"});
  await expect(bookmark).toBeEnabled();
  await bookmark.click();
  const checkpointResponse=page.waitForResponse(response=>response.request().method()==="POST"&&response.url().endsWith("/checkpoint"));
  await controls.getByRole("button",{name:"Save replay position"}).click();
  const checkpoint=await checkpointResponse;
  expect(checkpoint.ok()).toBeTruthy();
  const expected=[...bookmarks,position].sort((left,right)=>left-right);
  expect(checkpoint.request().postDataJSON()).toEqual({position,bookmarks:expected});
  await expect(page.getByText("Replay position saved")).toBeVisible();
  await page.reload();
  await page.getByLabel("Resume saved replay").selectOption(session.id);
  await expect(page.getByLabel("Replay timeline")).toHaveValue(String(position));
  expect(await page.getByLabel("Bookmarks").locator("option").evaluateAll(options=>
    options.slice(1).map(option=>Number((option as HTMLOptionElement).value)-1))).toEqual(expected);
  if(stressed)await page.unrouteAll({behavior:"wait"});
  expect(errors).toEqual([]);
});


test("a delayed drawing save cannot select a drawing in a different chart scope",async ({page})=>{
  await page.setViewportSize({width:1366,height:768});
  await page.goto("/");
  await page.getByRole("button",{name:"Market Terminal",exact:true}).click();
  await page.getByRole("combobox",{name:"Market data source"}).selectOption("DEMO");
  await page.getByRole("button",{name:"M1",exact:true}).click();
  const toolbox=page.getByText(/^Drawing toolbox/);
  await toolbox.click();
  await page.getByLabel("Drawing tool").selectOption("horizontal");
  const completed=Promise.withResolvers<string>();
  const release=Promise.withResolvers<void>();
  await page.route("**/drawings?*",async route=>{
    if(route.request().method()!=="POST")return route.continue();
    const response=await route.fetch();
    expect(response.ok()).toBeTruthy();
    const pending=route.request().postDataJSON().items.at(-1);
    completed.resolve(pending.id);
    await release.promise;
    await route.fulfill({response});
  });
  await page.getByRole("button",{name:"Draw",exact:true}).click();
  const plot=page.locator(".primary-chart .chart-input tr").first().locator("td").nth(1);
  await plot.click({position:{x:320,y:145}});
  const id=await completed.promise;
  await page.getByRole("button",{name:"M5",exact:true}).click();
  const selected=page.getByLabel("Select drawing");
  await expect(page.locator(".primary-chart .pane-label")).toContainText("M5");
  const written=page.waitForResponse(res=>res.request().method()==="POST"&&res.url().includes("/drawings?"));
  release.resolve();
  expect((await written).ok()).toBeTruthy();
  await expect(selected.locator(`option[value="${id}"]`)).toHaveCount(0);
  expect(await selected.evaluateAll(selects=>
    selects.every(item=>(item as HTMLSelectElement).value===""))).toBeTruthy();
  await page.getByRole("button",{name:"M1",exact:true}).click();
  await expect(selected.locator(`option[value="${id}"]`)).toHaveCount(1);
});
test("links four chart views and opens a saved simulation after reload",async ({page})=>{
  await page.setViewportSize({width:1920,height:1080});
  const errors:string[]=[];
  page.on("pageerror",reason=>errors.push(reason.message));
  await page.goto("/");
  await page.getByRole("button",{name:"Market Terminal",exact:true}).click();
  await page.getByRole("combobox",{name:"Market data source"}).selectOption("DEMO");
  await page.getByRole("combobox",{name:"Chart layout"}).selectOption("4");
  await page.getByText("Chart synchronization",{exact:true}).click();
  await page.getByRole("checkbox",{name:"Timeframe",exact:true}).check();
  const secondary=page.getByRole("region",{name:"Chart view 4",exact:true});
  await expect(secondary.getByText("M5",{exact:true})).toBeVisible();
  await expect(page.locator('.terminal-chart-columns [aria-label="Interactive market candlestick chart"]')).toHaveCount(4);
  await page.getByRole("button",{name:"Strategy Lab",exact:true}).click();
  await page.getByRole("combobox",{name:"Data source",exact:true}).selectOption("DEMO");
  await page.getByRole("button",{name:"Run historical simulation"}).click();
  await expect(page.getByText(/^Run [a-f0-9]{64}$/)).toBeVisible();
  const id=await page.getByLabel("Open saved run").inputValue();
  await page.reload();
  await page.getByLabel("Open saved run").selectOption(id);
  await expect(page.getByText("Run "+id,{exact:true})).toBeVisible();
  expect(errors).toEqual([]);
});
