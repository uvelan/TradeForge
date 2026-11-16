import {expect,test} from "@playwright/test";

test("unchanged live liquidity remains on chart beyond the revision page",async ({page},info)=>{
  const start=1793577600, step=300;
  const candles=Array.from({length:180},(_,i)=>({
    time:start+i*step,open:10,close:10,
    high:[1,3,179].includes(i)?20:i>=5&&i%2?11+i/100:10,
    low:[1,3,179].includes(i)?5:10,
  }));
  await page.addInitScript(()=>{
    localStorage.setItem("tradeforge.workspace.indicators-v1",JSON.stringify([{
      id:"liquidity_sweeps",structure:{left:1,right:1},liquidity:{equality_bps:0},
    }]));
    const labels=new WeakMap<HTMLCanvasElement,string[]>();
    const {fillText,clearRect,fillRect}=CanvasRenderingContext2D.prototype;
    CanvasRenderingContext2D.prototype.fillText=function(text,x,y,maxWidth){
      if(text==="EQH \u00b7 BSL"||text==="EQL \u00b7 SSL")labels.set(this.canvas,[...(labels.get(this.canvas)??[]),text]);
      if(maxWidth===undefined)fillText.call(this,text,x,y);else fillText.call(this,text,x,y,maxWidth);
    };
    CanvasRenderingContext2D.prototype.clearRect=function(x,y,w,h){
      labels.delete(this.canvas);clearRect.call(this,x,y,w,h);
    };
    CanvasRenderingContext2D.prototype.fillRect=function(x,y,w,h){
      if(x===0&&y===0&&w>=this.canvas.clientWidth&&h>=this.canvas.clientHeight)labels.delete(this.canvas);
      fillRect.call(this,x,y,w,h);
    };
    Object.assign(window,{liquidityLabels:(canvas:HTMLCanvasElement)=>labels.get(canvas)??[]});
  });
  await page.route("**/research/api/market/candles?**",route=>route.fulfill({json:{
    source:"DEMO",synthetic:true,price_basis:"SIMULATED",feed_id:"demo-v1",candles,
  }}));
  await page.setViewportSize({width:1536,height:900});
  await page.goto("/");
  await page.getByRole("button",{name:"Market Terminal",exact:true}).click();
  const response=page.waitForResponse(res=>res.url().endsWith("/indicators/evaluate"));
  await page.getByRole("combobox",{name:"Market data source"}).selectOption("DEMO");
  const result=(await (await response).json()).results[0];
  expect(result.liquidity_page.pools.total).toBeGreaterThan(64);
  expect(result.pools.some((row:{upper:number;lower:number})=>row.upper===20||row.lower===5)).toBe(false);
  const chart=page.locator(".primary-chart .chart-input");
  await chart.scrollIntoViewIfNeeded();
  const visible=()=>chart.locator("canvas").evaluateAll(elements=>elements.flatMap(element=>
    (window as unknown as {liquidityLabels:(canvas:HTMLCanvasElement)=>string[]}).liquidityLabels(element as HTMLCanvasElement)));
  expect(result.current_pools.items.filter((pool:{state:string})=>pool.state==="ACTIVE")
    .map((pool:{liquidity_side:string})=>pool.liquidity_side)).toEqual(["SSL","BSL"]);
  await expect.poll(async()=>[...new Set(await visible())].sort()).toEqual(["EQH \u00b7 BSL","EQL \u00b7 SSL"]);
  await page.screenshot({path:info.outputPath("active-pools-after.png")});
  await page.getByText("Indicator library",{exact:true}).click();
  const evidence=page.getByLabel("Liquidity evidence",{exact:true});
  await expect(evidence.getByRole("status")).toContainText("12 of 89 current pools");
  await expect(evidence.getByRole("status")).toContainText("Limited view");
  const historic=page.waitForResponse(res=>res.url().endsWith("/indicators/evaluate")&&
    Boolean(res.request().postDataJSON().indicators[0]?.liquidity_delivery?.pool_cursor));
  await evidence.getByRole("button",{name:"Older liquidity pools"}).click();
  expect((await historic).ok()).toBe(true);
  await chart.scrollIntoViewIfNeeded();
  await expect.poll(async()=>[...new Set(await visible())].sort()).toEqual(["EQH \u00b7 BSL","EQL \u00b7 SSL"]);
  const currentPage=page.waitForResponse(res=>res.url().endsWith("/indicators/evaluate")&&
    Boolean(res.request().postDataJSON().indicators[0]?.liquidity_delivery?.current_cursor));
  await evidence.getByRole("button",{name:"More current pools"}).click();
  const paged=(await (await currentPage).json()).results[0].current_pools;
  expect(paged.items).toHaveLength(40);
  expect(paged.items.every((pool:{state:string})=>pool.state==="FORMING")).toBe(true);
  await chart.scrollIntoViewIfNeeded();
  await expect.poll(async()=>[...new Set(await visible())].sort()).toEqual(["EQH \u00b7 BSL","EQL \u00b7 SSL"]);
});
