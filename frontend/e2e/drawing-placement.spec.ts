import {expect,test,type Locator,type Page} from "@playwright/test";

test.use({trace:"on"});

async function coloredPixels(canvas:Locator,color:number[]){
  return canvas.evaluate((surface:HTMLCanvasElement,color)=>{
    const data=surface.getContext("2d")!.getImageData(0,0,surface.width,surface.height).data;
    let count=0;
    for(let i=0;i<data.length;i+=4){
      if(data[i]===color[0]&&data[i+1]===color[1]&&data[i+2]===color[2])count++;
    }
    return count;
  },color);
}

async function openTool(page:Page,kind:string){
  await page.getByRole("button",{name:"Market Terminal",exact:true}).click();
  await page.getByRole("combobox",{name:"Market data source"}).selectOption("DEMO");
  await page.getByText(/^Drawing toolbox/).click();
  await page.getByLabel("Drawing tool").selectOption(kind);
  await expect(page.getByRole("button",{name:"Draw",exact:true})).toBeEnabled();
  const title=page.getByText(/^Drawing toolbox/);
  const prior=Number((await title.innerText()).match(/\((\d+)\)/)?.[1]??0);
  await page.getByRole("button",{name:"Draw",exact:true}).click();
  const canvas=page.locator(".primary-chart canvas").first();
  await canvas.evaluate(element=>element.scrollIntoView({block:"center",inline:"center"}));
  const bounds=await canvas.boundingBox();
  if(!bounds)throw Error("Primary plot not rendered");
  return {canvas,prior,point:(x:number,y:number)=>({x:bounds.x+bounds.width*x,y:bounds.y+bounds.height*y})};
}

for(const viewport of [{width:1366,height:768},{width:1920,height:1080}]){
  test(`drag places one trendline at ${viewport.width}x${viewport.height}`,async ({page},info)=>{
    await page.setViewportSize(viewport);
    await page.goto("/");
    const {point,canvas,prior}=await openTool(page,"trend");
    const a=point(.42,.41),b=point(.63,.62);
    await page.mouse.move(a.x,a.y);
    await page.mouse.down();
    await page.mouse.move(b.x,b.y,{steps:12});
    const cursorCanvas=canvas.locator("xpath=following-sibling::canvas[1]");
    await expect.poll(()=>coloredPixels(cursorCanvas,[255,220,134])).toBeGreaterThan(0);
    await page.mouse.up();
    await expect(page.getByText('Drawing toolbox ('+(prior+1)+')',{exact:true})).toBeVisible();
    await expect.poll(()=>coloredPixels(canvas,[85,217,181])).toBeGreaterThan(0);
    await page.screenshot({path:info.outputPath("draw-result.png")});

  });
}

test("touch places a horizontal drawing and its persisted anchor can be edited",async ({browser},info)=>{
  const context=await browser.newContext({hasTouch:true,viewport:{width:1366,height:768},baseURL:info.project.use.baseURL});
  try{
    const page=await context.newPage();
    await page.goto("/");
    const {point,canvas,prior}=await openTool(page,"horizontal");
    const a=point(.48,.50);
    await page.touchscreen.tap(a.x,a.y);
    await expect(page.getByText('Drawing toolbox ('+(prior+1)+')',{exact:true})).toBeVisible();
    await expect.poll(()=>coloredPixels(canvas,[85,217,181])).toBeGreaterThan(0);
    const id=await page.getByLabel("Select drawing").inputValue();
    await page.getByLabel("Select drawing").selectOption("");
    await page.mouse.click(a.x,a.y);
    await expect(page.getByLabel("Select drawing")).toHaveValue(id);
    await page.getByRole("button",{name:"Set anchor 1",exact:true}).click();
    await canvas.evaluate(element=>element.scrollIntoView({block:"center",inline:"center"}));
    const box=(await canvas.boundingBox())!;
    const response=page.waitForResponse(res=>res.url().includes("/drawings?")&&res.request().method()==="POST");
    await page.touchscreen.tap(box.x+box.width*.48,box.y+box.height*.66);
    const saved=await (await response).json();
    const edited=saved.items.find((item:{id:string})=>item.id===id);
    await page.reload();
    await page.getByText(/^Drawing toolbox/).click();
    await expect(page.getByLabel("Select drawing").locator('option[value="'+id+'"]')).toContainText(edited.anchors[0].price);
  }finally{await context.close();}
});

test("rejects outside release and Escape then saves a rectangle with two clicks",async ({page})=>{
  await page.setViewportSize({width:1366,height:768});
  await page.goto("/");
  const {point,canvas,prior}=await openTool(page,"rectangle");
  const a=point(.42,.38),b=point(.62,.62),outside=point(.55,1.025);
  await page.mouse.move(a.x,a.y);await page.mouse.down();
  await page.mouse.move(outside.x,outside.y,{steps:6});await page.mouse.up();
  await expect(page.getByText('Drawing toolbox ('+prior+')',{exact:true})).toBeVisible();
  await page.mouse.click(a.x,a.y);
  await expect(page.getByText(/Rectangle.*select 2/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByText(/Rectangle.*select 2/)).toHaveCount(0);
  await page.getByRole("button",{name:"Draw",exact:true}).click();
  await canvas.evaluate(element=>element.scrollIntoView({block:"center",inline:"center"}));
  const box=(await canvas.boundingBox())!;
  await page.mouse.click(box.x+box.width*.42,box.y+box.height*.38);
  await page.mouse.click(box.x+box.width*.62,box.y+box.height*.62);
  await expect(page.getByText('Drawing toolbox ('+(prior+1)+')',{exact:true})).toBeVisible();
  await expect.poll(()=>coloredPixels(canvas,[85,217,181])).toBeGreaterThan(0);
});

test("risk/reward commits valid prices after zoom and excludes the comparison axis",async ({page},info)=>{
  await page.setViewportSize({width:1920,height:1080});
  await page.goto("/");
  const {prior}=await openTool(page,"risk-reward");
  await page.getByLabel("SMT comparison",{exact:true}).selectOption("GBPUSD");
  await expect(page.getByLabel("Comparison placement")).toHaveValue("Overlay");
  const plot=page.locator(".primary-chart .chart-input canvas").locator("visible=true");
  await expect.poll(()=>plot.count()).toBeGreaterThan(4);
  // The widest canvas in the first table row is the primary plot, even with a left price scale.
  const row=page.locator(".primary-chart .chart-input tr").first();
  const widths=await row.locator("td").evaluateAll(cells=>cells.map(cell=>cell.getBoundingClientRect().width));
  const primary=row.locator("td").nth(widths.indexOf(Math.max(...widths))).locator("canvas").first();
  await primary.evaluate(element=>element.scrollIntoView({block:"center",inline:"center"}));
  const box=(await primary.boundingBox())!;
  expect(widths[0]).toBeGreaterThan(0);
  await page.mouse.click(box.x-4,box.y+box.height*.40);
  await expect(page.getByText(/Risk\/reward.*select 1/)).toBeVisible();
  await page.mouse.move(box.x+box.width*.50,box.y+box.height*.48);
  await page.mouse.wheel(0,-180);
  await page.mouse.click(box.x+box.width*.42,box.y+box.height*.45);
  await page.mouse.click(box.x+box.width*.52,box.y+box.height*.63);
  await expect(page.getByText(/Risk\/reward.*select 3/)).toBeVisible();
  const posted=page.waitForResponse(res=>res.url().includes("/drawings?")&&res.request().method()==="POST");
  await page.mouse.click(box.x+box.width*.63,box.y+box.height*.24);
  const version=await (await posted).json();
  const item=version.items.at(-1);
  expect(item.kind).toBe("risk-reward");
  expect(Number(item.anchors[1].price)).toBeLessThan(Number(item.anchors[0].price));
  expect(Number(item.anchors[2].price)).toBeGreaterThan(Number(item.anchors[0].price));
  await expect(page.getByText('Drawing toolbox ('+(prior+1)+')',{exact:true})).toBeVisible();
  await page.screenshot({path:info.outputPath("risk-reward-result.png")});
  await page.getByLabel("Comparison placement").selectOption("New pane");
  await expect(page.getByLabel("Comparison placement")).toHaveValue("New pane");
});

test("a stale drawing save reports 409 and reload preserves the competing scene",async ({page})=>{
  await page.goto("/");
  const {point,prior}=await openTool(page,"horizontal");
  const endpoint="/research/api/drawings?scope=DEMO%3AEURUSD%3AM5";
  const scene=await (await page.request.get(endpoint)).json();
  const stamp=Math.floor(Date.now()/1000);
  const other={id:"abdc895e-ba83-49b1-900a-ff123a795abb",kind:"horizontal",
    anchors:[{time:180,price:"1.10000"}],label:"Concurrent drawing",color:"#ffa500",
    visible:true,locked:false,created_at:stamp,updated_at:stamp};
  const write=await page.request.post(endpoint,{data:{...scene,items:[...scene.items,other]}});
  expect(write.status()).toBe(200);
  const response=page.waitForResponse(res=>res.url().includes("/drawings?")&&res.request().method()==="POST");
  const p=point(.48,.47);await page.mouse.click(p.x,p.y);
  expect((await response).status()).toBe(409);
  await expect(page.getByRole("button",{name:"Reload drawings"})).toBeVisible();
  await page.getByRole("button",{name:"Reload drawings"}).click();
  await expect(page.getByText('Drawing toolbox ('+(prior+1)+')',{exact:true})).toBeVisible();
  await expect(page.getByLabel("Select drawing").locator('option[value="'+other.id+'"]')).toHaveCount(1);
});

test("blank chart space and a secondary plot cannot save into the primary scope",async ({page})=>{
  await page.setViewportSize({width:1920,height:1080});
  await page.goto("/");
  const {prior}=await openTool(page,"horizontal");
  await page.getByRole("combobox",{name:"Chart layout"}).selectOption("2");
  const secondary=page.getByRole("region",{name:"Chart view 2",exact:true}).locator("canvas").first();
  await secondary.evaluate(element=>element.scrollIntoView({block:"center",inline:"center"}));
  const other=(await secondary.boundingBox())!;
  await page.mouse.click(other.x+other.width*.50,other.y+other.height*.50);
  const primary=page.locator(".primary-chart canvas").first();
  await primary.evaluate(element=>element.scrollIntoView({block:"center",inline:"center"}));
  const box=(await primary.boundingBox())!;
  await page.mouse.click(box.x+box.width-2,box.y+box.height*.40);
  await expect(page.getByText(/Horizontal line.*select 1/)).toBeVisible();
  await expect(page.getByText('Drawing toolbox ('+prior+')',{exact:true})).toBeVisible();
  await page.mouse.click(box.x+box.width*.48,box.y+box.height*.40);
  await expect(page.getByText('Drawing toolbox ('+(prior+1)+')',{exact:true})).toBeVisible();
});
