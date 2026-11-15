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


test("persists a trendline and resumes a frozen replay checkpoint", async ({page})=>{
  await page.setViewportSize({width:1366,height:768});
  await page.goto("/");
  await page.getByRole("button",{name:"Market Terminal",exact:true}).click();
  await page.getByRole("combobox",{name:"Market data source"}).selectOption("DEMO");
  const chart=page.locator(".primary-chart canvas").first();
  await expect(chart).toBeVisible();
  await page.getByText(/^Drawing toolbox/).click();
  await page.getByRole("button",{name:"Draw",exact:true}).click();
  await chart.evaluate(element=>element.scrollIntoView({block:"center",inline:"center"}));
  const box=await chart.boundingBox();
  if(!box)throw Error("Primary chart has no bounds");
  await page.mouse.click(box.x+box.width*.45,box.y+box.height*.38);
  await expect(page.getByText(/Trendline.*select 2/)).toBeVisible();
  const second=await chart.boundingBox();
  if(!second)throw Error("Primary chart moved off screen");
  await page.mouse.click(second.x+second.width*.62,second.y+second.height*.31);
  await page.getByText(/^Drawing toolbox/).scrollIntoViewIfNeeded();
  await expect(page.getByLabel("Select drawing")).toHaveValue(/^[a-f0-9-]{36}$/);
  const id=await page.getByLabel("Select drawing").inputValue();
  await page.getByRole("button",{name:"Save chart evidence"}).click();
  await expect(page.getByText("Chart capture saved")).toBeVisible();
  await page.reload();
  await page.getByText(/^Drawing toolbox/).click();
  await expect(page.getByLabel("Select drawing").locator(`option[value="${id}"]`)).toHaveCount(1);
  await page.getByRole("button",{name:"Bar replay",exact:true}).click();
  const controls=page.getByLabel("Historical replay");
  await expect(controls).toBeVisible();
  await controls.getByRole("button",{name:"Restart"}).click();
  await controls.getByRole("button",{name:"Next candle"}).click();
  await controls.getByRole("button",{name:"Bookmark candle"}).click();
  await controls.getByRole("button",{name:"Save replay position"}).click();
  await expect(page.getByText("Replay position saved")).toBeVisible();
  await page.reload();
  await page.getByLabel("Resume saved replay").selectOption({index:1});
  await expect(page.getByLabel("Replay timeline")).toHaveValue("1");
  await expect(page.getByLabel("Bookmarks").locator("option")).toHaveCount(2);
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

test("restricts legacy trade metrics and disables mixed saved-run comparisons", async ({page})=>{
  const legacyId="a".repeat(64), verifiedId="b".repeat(64);
  const model={version:2,max_quote_gap_ms:60_000,slippage_bps:"0",commission_bps_per_side:"0"};
  const legacyTrust={version:1,status:"LEGACY_UNVERIFIED_ORDER",reason:"Historical cross-file order is unverified.",
    performance_eligible:false,broker_execution_verified:false};
  const verifiedTrust={...legacyTrust,status:"VERIFIED_PROVENANCE",reason:"Verified fixtures.",performance_eligible:true};
  const meta={created_at:"2024-01-01T00:00:00Z",count:1,source:"DUKASCOPY_SAVED",symbol:"EURUSD",
    timeframe:"M1",model:"TICK",synthetic:false};
  await page.route("**/research/api/backtest/runs",route=>route.fulfill({json:[
    {...meta,id:legacyId,trust:legacyTrust,expectancy_r:null},
    {...meta,id:verifiedId,trust:verifiedTrust,expectancy_r:2.5},
  ]}));
  await page.route("**/research/api/backtest/runs/*",route=>{
    const legacy=route.request().url().endsWith(legacyId);
    const score=legacy?99:2.5;
    return route.fulfill({json:{...meta,id:legacy?legacyId:verifiedId,market:{candles:[]},
      result:{...meta,trust:legacy?legacyTrust:verifiedTrust,eligible_signals:1,ambiguous_excluded:0,
        win_rate:100,expectancy_r:score,total_r:score,execution_model:model,
        trades:[{entry_time:180,entry:101,stop:99,target:105,side:"BUY",result_r:score}]}}});
  });
  await page.goto("/");
  await page.getByRole("button",{name:"Strategy Lab",exact:true}).click();
  await page.getByLabel("Open saved run").selectOption(legacyId);
  await expect(page.getByText("UNVERIFIED LEGACY REPLAY")).toBeVisible();
  await expect(page.getByRole("link",{name:"Download raw audit record"})).toBeVisible();
  await expect(page.getByRole("button",{name:"Rerun with verified archives"})).toBeVisible();
  await expect(page.getByLabel("Compare with")).toBeDisabled();
  await expect(page.getByText("99 R",{exact:true})).toHaveCount(0);
  await expect(page.getByRole("img",{name:"Modelled cumulative result in R"})).toHaveCount(0);
  await page.getByLabel("Open saved run").selectOption(verifiedId);
  await expect(page.getByRole("img",{name:"Modelled cumulative result in R"})).toBeVisible();
  await expect(page.getByLabel("Compare with")).toBeEnabled();
  await expect(page.getByLabel("Compare with").locator(`option[value="${legacyId}"]`)).toBeDisabled();
});
