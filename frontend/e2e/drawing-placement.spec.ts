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

type AnchorRecord={time:number;price:string};
type DrawingRecord={id:string;kind:string;anchors:AnchorRecord[]};
type Scene={revision:number;items:DrawingRecord[]};
const endpoint=(frame="M5")=>"/research/api/drawings?scope="+encodeURIComponent("DEMO:EURUSD:"+frame);
const savedScene=async(page:Page,frame?:string):Promise<Scene>=>(await page.request.get(endpoint(frame))).json();
const postDrawing=(page:Page)=>page.waitForResponse(res=>res.url().includes("/drawings?")&&res.request().method()==="POST");

async function primaryPlot(page:Page){
  const row=page.locator(".primary-chart .chart-input tr").first();
  await expect(row.locator("canvas").first()).toBeVisible();
  const widths=await row.locator("td").evaluateAll(cells=>cells.map(cell=>cell.getBoundingClientRect().width));
  const plot=row.locator("td").nth(widths.indexOf(Math.max(...widths))).locator("canvas").first();
  return {plot,leftWidth:widths[0]};
}

async function plotPoint(page:Page,x:number,y:number){
  const {plot}=await primaryPlot(page);
  if(!await plot.evaluate(element=>document.fullscreenElement?.contains(element)??false))
    await plot.evaluate(element=>element.scrollIntoView({block:"center",inline:"center"}));
  const box=await plot.boundingBox();
  if(!box)throw Error("Primary drawing plot unavailable");
  return {x:box.x+box.width*x,y:box.y+box.height*y};
}

async function clickPlot(page:Page,x:number,y:number){
  const p=await plotPoint(page,x,y);
  await page.mouse.click(p.x,p.y);
}

async function savedChange(response:ReturnType<typeof postDrawing>):Promise<Scene>{
  const result=await response;
  expect(result.status(),await result.text()).toBe(200);
  return result.json();
}

const placementCases=[
  {kind:"trend",points:[[.41,.36],[.62,.64]]},
  {kind:"rectangle",points:[[.41,.35],[.63,.64]]},
  {kind:"risk-reward",points:[[.41,.49],[.63,.71],[.63,.26]]},
];

for(const {kind,points} of placementCases){
  test("persists in-place edits of every "+kind+" anchor",async ({page},info)=>{
    await page.setViewportSize({width:1366,height:768});
    await page.goto("/");
    await openTool(page,kind);
    const prior=await savedScene(page);
    for(const [x,y] of points.slice(0,-1))await clickPlot(page,x,y);
    const create=postDrawing(page);
    await clickPlot(page,...points[points.length-1] as [number,number]);
    const placed=await savedChange(create);
    expect(placed.items).toHaveLength(prior.items.length+1);
    const item=placed.items.at(-1)!;
    expect(item.kind).toBe(kind);
    expect(item.anchors).toHaveLength(points.length);
    await expect(page.getByLabel("Select drawing")).toHaveValue(item.id);
    await page.getByLabel("Select drawing").selectOption("");
    // The corner/endpoint is on the primitive stroke. Click the visible shape, then use the existing edit control.
    await clickPlot(page,...points[0] as [number,number]);
    await expect(page.getByLabel("Select drawing")).toHaveValue(item.id);
    let previous=placed;
    for(const [index,[x,y]] of points.entries()){
      await page.getByRole("button",{name:"Set anchor "+(index+1),exact:true}).click();
      const response=postDrawing(page);
      await clickPlot(page,x+.05,y+.035);
      const revision=await savedChange(response);
      expect(revision.revision).toBe(previous.revision+1);
      expect(revision.items.map(row=>row.id)).toEqual(previous.items.map(row=>row.id));
      const old=previous.items.find(row=>row.id===item.id)!;
      const current=revision.items.find(row=>row.id===item.id)!;
      expect(current.anchors[index].time).toBeGreaterThan(old.anchors[index].time);
      expect(Number(current.anchors[index].price)).toBeLessThan(Number(old.anchors[index].price));
      expect(current.anchors.filter((_,i)=>i!==index)).toEqual(old.anchors.filter((_,i)=>i!==index));
      previous=revision;
      await expect(page.getByRole("button",{name:"Set anchor "+(index+1),exact:true})).toBeEnabled();
    }
    await page.screenshot({path:info.outputPath(kind+"-edited.png")});
    const reloaded=page.waitForResponse(res=>res.url().includes(endpoint())&&res.request().method()==="GET");
    await page.reload();
    expect(await (await reloaded).json()).toEqual(previous);
    await page.getByText(/^Drawing toolbox/).click();
    await expect(page.getByLabel("Select drawing").locator('option[value="'+item.id+'"]')).toHaveCount(1);
  });
}

test("previews and saves both position directions and rejects an invalid edit",async ({page},info)=>{
  await page.setViewportSize({width:1366,height:768});
  await page.goto("/");
  await openTool(page,"risk-reward");
  const createdIds:string[]=[];
  let expectedScene:Scene|undefined;
  for(const direction of ["long","short"]){
    if(direction==="short")await page.getByRole("button",{name:"Draw",exact:true}).click();
    const prior=await savedScene(page);
    const long=direction==="long", stopY=long ? .73 : .25,targetY=long ? .25 : .73;
    await clickPlot(page,.43,.49);
    await clickPlot(page,.64,stopY);
    // Same side as stop: keep the existing scene and continue targeting the third point.
    await clickPlot(page,.70,stopY);
    await expect(page.getByText(/Select the target again/)).toBeVisible();
    await expect(page.getByText(/Risk\/reward.*select 3/)).toBeVisible();
    expect(await savedScene(page)).toEqual(prior);
    const target=await plotPoint(page,.67,targetY);
    await page.mouse.move(target.x,target.y);
    const {plot}=await primaryPlot(page);
    await expect.poll(()=>coloredPixels(plot.locator("xpath=following-sibling::canvas[1]"),[255,220,134])).toBeGreaterThan(0);
    await page.screenshot({path:info.outputPath(direction+"-preview.png")});
    const response=postDrawing(page);
    await page.mouse.click(target.x,target.y);
    const saved=await savedChange(response);
    expect(saved.items).toHaveLength(prior.items.length+1);
    const item=saved.items.at(-1)!;
    createdIds.push(item.id);
    const [entry,stop,profit]=item.anchors.map(point=>Number(point.price));
    if(long){expect(stop).toBeLessThan(entry);expect(profit).toBeGreaterThan(entry);}
    else{expect(stop).toBeGreaterThan(entry);expect(profit).toBeLessThan(entry);}
    expect(await savedScene(page)).toEqual(saved);
    if(!long){
      const writes:string[]=[];
      page.on("request",request=>{
        if(request.method()==="POST"&&request.url().includes("/drawings?"))writes.push(request.url());
      });
      await page.getByRole("button",{name:"Set anchor 3",exact:true}).click();
      await clickPlot(page,.70,stopY);
      await expect(page.getByText(/Stop and target must bracket entry/)).toBeVisible();
      await expect(page.getByText(/Risk\/reward.*select 3/)).toBeVisible();
      expect(await savedScene(page)).toEqual(saved);
      expect(writes).toHaveLength(0);
      const correction=postDrawing(page);
      await clickPlot(page,.70,.81);
      expectedScene=await savedChange(correction);
      expect(writes).toHaveLength(1);
      expect(expectedScene.revision).toBe(saved.revision+1);
      expect(expectedScene.items.map(row=>row.id)).toEqual(saved.items.map(row=>row.id));
      const corrected=expectedScene.items.at(-1)!;
      expect(corrected.anchors.slice(0,2)).toEqual(item.anchors.slice(0,2));
      expect(Number(corrected.anchors[2].price)).toBeLessThan(profit);
    }
  }
  const reloaded=page.waitForResponse(res=>res.url().includes(endpoint())&&res.request().method()==="GET");
  await page.reload();
  expect(await (await reloaded).json()).toEqual(expectedScene);
  await page.getByText(/^Drawing toolbox/).click();
  for(const id of createdIds)
    await expect(page.getByLabel("Select drawing").locator('option[value="'+id+'"]')).toHaveCount(1);
});

test("releases pointer ownership on candle refresh and scope remount",async ({page},info)=>{
  await page.addInitScript(()=>{
    const ids=new WeakMap<Element,number>();
    const listeners=new WeakMap<EventTarget,Set<EventListenerOrEventListenerObject>>();
    const capture=Element.prototype.setPointerCapture;
    Element.prototype.setPointerCapture=function(id:number){
      ids.set(this,id);return capture.call(this,id);
    };
    const add=EventTarget.prototype.addEventListener, remove=EventTarget.prototype.removeEventListener;
    EventTarget.prototype.addEventListener=function(type,listener,options){
      if(type==="pointercancel"&&listener&&this instanceof Element&&this.classList.contains("chart-input")){
        const entries=listeners.get(this)??new Set();
        entries.add(listener);listeners.set(this,entries);
      }
      return add.call(this,type,listener,options);
    };
    EventTarget.prototype.removeEventListener=function(type,listener,options){
      if(type==="pointercancel"&&listener)listeners.get(this)?.delete(listener);
      return remove.call(this,type,listener,options);
    };
    Object.assign(window,{inspectDrawingInput:(node:Element)=>{
      const pointer=ids.get(node);
      return {connected:node.isConnected,captured:pointer!==undefined&&node.hasPointerCapture(pointer),
        cancelListeners:listeners.get(node)?.size??0};
    }});
  });
  await page.setViewportSize({width:1366,height:768});
  await page.goto("/");
  await openTool(page,"trend");
  const prior=await savedScene(page), other=await savedScene(page,"M15");
  const presses:string[]=[];
  page.on("request",request=>{
    if(request.method()==="POST"&&request.url().includes("/drawings?"))
      presses.push(new URL(request.url()).searchParams.get("scope")??"");
  });
  const inspect=(input:NonNullable<Awaited<ReturnType<Locator["elementHandle"]>>>)=>input.evaluate(node=>
    (window as unknown as {inspectDrawingInput:(el:Element)=>{connected:boolean;captured:boolean;cancelListeners:number}}).inspectDrawingInput(node));
  const input=page.locator(".primary-chart .chart-input");
  async function beginDrag(){
    const host=await input.elementHandle();
    if(!host)throw Error("Chart host missing");
    const a=await plotPoint(page,.44,.42);
    await page.mouse.move(a.x,a.y);
    await page.mouse.down();
    expect(await inspect(host)).toEqual({connected:true,captured:true,cancelListeners:1});
    return host;
  }
  const scopeIs=(frame:string)=>(res:import("@playwright/test").Response)=>{
    const url=new URL(res.url());
    return url.pathname.endsWith("/market/candles")&&url.searchParams.get("source")==="DEMO"&&
      url.searchParams.get("timeframe")===frame;
  };

  const stale=await beginDrag();
  const refreshed=page.waitForResponse(scopeIs("M5"));
  await page.getByTitle("Refresh candles",{exact:true}).evaluate((button:HTMLButtonElement)=>button.click());
  await refreshed;
  await expect.poll(()=>inspect(stale)).toEqual({connected:false,captured:false,cancelListeners:0});
  const now=await plotPoint(page,.61,.61);
  await page.mouse.move(now.x,now.y);
  await page.mouse.up();
  expect(presses).toEqual([]);
  expect(await savedScene(page)).toEqual(prior);

  // A new gesture in the same scope saves once after the StrictMode mount/cleanup cycle.
  const refreshedInput=await beginDrag();
  const end=await plotPoint(page,.61,.61);
  const complete=postDrawing(page);
  await page.mouse.move(end.x,end.y,{steps:8});
  await page.mouse.up();
  const saved=await savedChange(complete);
  expect(saved.revision).toBe(prior.revision+1);
  expect(saved.items).toHaveLength(prior.items.length+1);
  expect(await inspect(refreshedInput)).toEqual({connected:true,captured:false,cancelListeners:1});

  await page.getByRole("button",{name:"Draw",exact:true}).click();
  const oldScope=await beginDrag();
  const nextCandles=page.waitForResponse(scopeIs("M15"));
  await page.getByRole("button",{name:"M15",exact:true}).evaluate((button:HTMLButtonElement)=>button.click());
  await nextCandles;
  await expect.poll(()=>inspect(oldScope)).toEqual({connected:false,captured:false,cancelListeners:0});
  const next=await plotPoint(page,.63,.60);
  await page.mouse.move(next.x,next.y);
  await page.mouse.up();
  expect(presses).toEqual(["DEMO:EURUSD:M5"]);
  expect(await savedScene(page)).toEqual(saved);
  expect(await savedScene(page,"M15")).toEqual(other);

  await page.getByRole("button",{name:"Draw",exact:true}).click();
  await clickPlot(page,.40,.36);
  const nextResponse=postDrawing(page);
  await clickPlot(page,.62,.64);
  const nextScene=await savedChange(nextResponse);
  expect(nextScene.revision).toBe(other.revision+1);
  expect(presses).toEqual(["DEMO:EURUSD:M5","DEMO:EURUSD:M15"]);
  await page.screenshot({path:info.outputPath("scope-remount.png")});
});

test("maps anchors across fullscreen and resized plots with a left comparison scale",async ({page},info)=>{
  await page.addInitScript(()=>{
    const proto=CanvasRenderingContext2D.prototype;
    type XY=[number,number];
    const points=new WeakMap<CanvasRenderingContext2D,XY[]>();
    const painted=new WeakMap<HTMLCanvasElement,number[]>();
    const {beginPath,moveTo,lineTo,stroke}=proto;
    proto.beginPath=function(){points.set(this,[]);beginPath.call(this);};
    proto.moveTo=function(x,y){points.get(this)?.push([x,y]);moveTo.call(this,x,y);};
    proto.lineTo=function(x,y){points.get(this)?.push([x,y]);lineTo.call(this,x,y);};
    proto.stroke=function(...args:[]|[Path2D]){
      const path=points.get(this);
      if(this.strokeStyle==="#55d9b5"&&path?.length===2&&path[0][0]===0&&path[0][1]===path[1][1]){
        const matrix=this.getTransform();
        const y=matrix.d*path[0][1]+matrix.f;
        const rows=painted.get(this.canvas)??[];
        rows.push(y);painted.set(this.canvas,rows.slice(-32));
      }
      if(args.length)stroke.call(this,args[0]);else stroke.call(this);
    };
    Object.assign(window,{lastDrawingRows:(canvas:HTMLCanvasElement)=>painted.get(canvas)??[]});
  });
  await page.setViewportSize({width:1366,height:768});
  await page.goto("/");
  const primaryData=page.waitForResponse(res=>{
    const url=new URL(res.url());
    return url.pathname.endsWith("/market/candles")&&url.searchParams.get("source")==="DEMO"&&
      url.searchParams.get("symbol")==="EURUSD"&&url.searchParams.get("limit")==="600";
  });
  await openTool(page,"horizontal");
  const {candles}=await (await primaryData).json() as {candles:{time:number;open:number;high:number;low:number;close:number}[]};
  await page.getByLabel("SMT comparison",{exact:true}).selectOption("GBPUSD");
  await expect(page.getByLabel("Comparison placement")).toHaveValue("Overlay");
  const surface=page.locator(".primary-chart .chart-surface");
  const full=surface.getByRole("button",{name:"Fullscreen chart",exact:true});
  await full.click();
  await expect(surface.getByRole("button",{name:"Exit fullscreen",exact:true})).toBeVisible();
  expect(await surface.evaluate(element=>document.fullscreenElement===element)).toBe(true);
  const sizeBefore=(await primaryPlot(page)).plot;
  const width=await sizeBefore.evaluate(el=>el.getBoundingClientRect().width);

  for(const state of ["fullscreen","resized"]){
    if(state==="resized"){
      await surface.getByRole("button",{name:"Exit fullscreen",exact:true}).click();
      await expect(full).toBeVisible();
      expect(await page.evaluate(()=>document.fullscreenElement===null)).toBe(true);
      await page.setViewportSize({width:1536,height:900});
      await expect.poll(async()=>Math.round((await (await primaryPlot(page)).plot.boundingBox())!.width)).not.toBe(Math.round(width));
      await page.getByRole("button",{name:"Draw",exact:true}).click();
    }
    expect((await primaryPlot(page)).leftWidth).toBeGreaterThan(0);
    const expectedRow=state==="fullscreen" ? .63 : .74;
    const p=await plotPoint(page,.57,expectedRow);
    // The independently rendered crosshair legend identifies the expected primary candle.
    await page.mouse.move(p.x-18,p.y);
    await page.mouse.move(p.x,p.y);
    const legend=surface.locator(".chart-legend-row").first();
    await expect(legend).toContainText("EURUSD");
    const rendered=(await legend.locator("span").allTextContents()).map(value=>Number(value.trim().split(/\s+/).at(-1)));
    const candle=candles.find(bar=>[bar.open,bar.high,bar.low,bar.close]
      .every((price,index)=>Number(price.toFixed(5))===rendered[index]));
    expect(candle,"crosshair should resolve to a primary candle").toBeDefined();

    const prior=await savedScene(page);
    const response=postDrawing(page);
    await page.mouse.click(p.x,p.y);
    const scene=await savedChange(response);
    expect(scene.items).toHaveLength(prior.items.length+1);
    const anchor=scene.items.at(-1)!.anchors[0];
    expect(anchor.time).toBe(candle!.time);
    expect(Number(anchor.price)).toBeGreaterThan(1.0);
    expect(Number(anchor.price)).toBeLessThan(1.25);
    const {plot}=await primaryPlot(page);
    // Measure a full-width saved primitive on the actual canvas, allowing the effect of price rounding.
    await expect.poll(()=>plot.evaluate((element:HTMLCanvasElement,relativeY)=>{
      const rows=(window as unknown as {lastDrawingRows:(canvas:HTMLCanvasElement)=>number[]}).lastDrawingRows(element);
      // Saving adds toolbox controls above the plot and can shift its screen position.
      return Math.min(Infinity,...rows.map(row=>Math.abs(row-relativeY*element.height)));
    },expectedRow)).toBeLessThan(4);
    await page.screenshot({path:info.outputPath(state+"-placement.png")});
  }
});

type LegacyLevel={id:string;price:number};
const localLevels=(page:Page):Promise<LegacyLevel[]>=>page.evaluate(()=>
  JSON.parse(localStorage.getItem("tf-levels:DEMO:EURUSD:M5")??"[]"));

test("the horizontal rail takes precedence over an unfinished trendline",async ({page},info)=>{
  await page.goto("/");
  await openTool(page,"trend");
  await clickPlot(page,.40,.34);
  const scene=await savedScene(page);
  const levels=await localLevels(page);
  await page.getByTitle("Place horizontal level (Alt+H)",{exact:true}).click();
  await clickPlot(page,.60,.63);
  await page.screenshot({path:info.outputPath("rail-mode.png")});
  expect(await savedScene(page)).toEqual(scene);
  expect(await localLevels(page)).toHaveLength(levels.length+1);
});

test("a horizontal drawing drag uses the release candle and price after zoom and resize",async ({page},info)=>{
  await page.goto("/");
  const data=page.waitForResponse(res=>{
    const url=new URL(res.url());
    return url.pathname.endsWith("/market/candles")&&url.searchParams.get("source")==="DEMO"&&
      url.searchParams.get("symbol")==="EURUSD"&&url.searchParams.get("limit")==="600";
  });
  await openTool(page,"horizontal");
  const {candles}=await (await data).json() as {candles:{time:number;open:number;high:number;low:number;close:number}[]};
  const scene=await savedScene(page);
  await page.getByLabel("SMT comparison",{exact:true}).selectOption("GBPUSD");
  await expect(page.getByLabel("Comparison placement")).toHaveValue("Overlay");
  await page.getByLabel("Chart layout",{exact:true}).selectOption("2");
  await page.setViewportSize({width:1536,height:900});
  expect((await primaryPlot(page)).leftWidth).toBeGreaterThan(0);
  const middle=await plotPoint(page,.55,.48);
  await page.mouse.move(middle.x,middle.y);
  await page.mouse.wheel(0,-150);
  const start=await plotPoint(page,.42,.36), release=await plotPoint(page,.65,.68);
  await page.mouse.move(release.x,release.y);
  const legend=page.locator(".primary-chart .chart-legend-row").first();
  await expect(legend).toContainText("EURUSD");
  const rendered=(await legend.locator("span").allTextContents()).map(value=>Number(value.trim().split(/\s+/).at(-1)));
  const candle=candles.find(bar=>[bar.open,bar.high,bar.low,bar.close]
    .every((price,index)=>Number(price.toFixed(5))===rendered[index]));
  expect(candle).toBeDefined();

  await page.mouse.move(start.x,start.y);
  await page.mouse.down();
  await page.mouse.move(release.x,release.y,{steps:8});
  const response=postDrawing(page);
  await page.mouse.up();
  const after=await savedChange(response);
  expect(after.revision).toBe(scene.revision+1);
  expect(after.items).toHaveLength(scene.items.length+1);
  const item=after.items.at(-1)!;
  expect(item.anchors[0].time).toBe(candle!.time);
  // Same plot-relative location by click is the control for the drag's release price.
  await page.getByRole("button",{name:"Draw",exact:true}).click();
  const click=postDrawing(page);
  await clickPlot(page,.65,.68);
  const clicked=await savedChange(click);
  expect(clicked.items.at(-1)!.anchors).toEqual(item.anchors);
  await page.screenshot({path:info.outputPath("horizontal-drag.png")});
  const fetched=page.waitForResponse(res=>res.url().includes(endpoint())&&res.request().method()==="GET");
  await page.reload();
  expect((await (await fetched).json() as Scene).items.find(row=>row.id===item.id)).toEqual(item);
});

test("Alt+H and the rail discard rectangle and risk/reward draft anchors",async ({page})=>{
  await page.goto("/");
  await openTool(page,"rectangle");
  const before=await savedScene(page);
  await clickPlot(page,.41,.38);
  await page.keyboard.press("Alt+h");
  await clickPlot(page,.59,.63);
  expect(await savedScene(page)).toEqual(before);
  expect(await localLevels(page)).toHaveLength(1);

  await page.getByLabel("Drawing tool").selectOption("risk-reward");
  await page.getByRole("button",{name:"Draw",exact:true}).click();
  await clickPlot(page,.41,.49);
  await clickPlot(page,.62,.70);
  await expect(page.getByText(/Risk\/reward.*select 3/)).toBeVisible();
  await page.getByTitle("Place horizontal level (Alt+H)",{exact:true}).click();
  await clickPlot(page,.59,.34);
  expect(await savedScene(page)).toEqual(before);
  expect(await localLevels(page)).toHaveLength(2);
});

test("toolbox activation clears other modes and repeated switches save only the final intended drawing",async ({page},info)=>{
  await page.goto("/");
  await openTool(page,"horizontal");
  const before=await savedScene(page);
  const rail=page.getByTitle("Place horizontal level (Alt+H)",{exact:true});
  await rail.click();
  await page.getByLabel("Drawing tool").selectOption("trend");
  await page.getByRole("button",{name:"Draw",exact:true}).click();
  await clickPlot(page,.41,.37);
  await expect(page.getByText(/Trendline.*select 2/)).toBeVisible();

  await page.getByRole("button",{name:"Select plan levels",exact:true}).click();
  await clickPlot(page,.51,.43);
  await expect(page.getByText("Click primary price for stop")).toBeVisible();
  await page.getByLabel("Drawing tool").selectOption("rectangle");
  await page.getByRole("button",{name:"Draw",exact:true}).click();
  await expect(page.getByText("Click primary price for stop")).toHaveCount(0);
  await clickPlot(page,.42,.35);
  await page.getByLabel("Drawing tool").selectOption("trend");
  await page.getByRole("button",{name:"Draw",exact:true}).click();
  await clickPlot(page,.43,.37);
  const response=postDrawing(page);
  await clickPlot(page,.65,.63);
  const saved=await savedChange(response);
  expect(saved.revision).toBe(before.revision+1);
  expect(saved.items.slice(0,-1)).toEqual(before.items);
  expect(saved.items.at(-1)?.kind).toBe("trend");
  expect(await localLevels(page)).toEqual([]);

  await page.getByRole("button",{name:"Set anchor 1",exact:true}).click();
  await page.getByTitle("Crosshair (Esc)",{exact:true}).click();
  await clickPlot(page,.53,.54);
  expect(await savedScene(page)).toEqual(saved);
  await page.screenshot({path:info.outputPath("exclusive-modes.png")});
});

test("Escape cancels all armed modes and an active drag cannot complete in a replacement mode",async ({page})=>{
  await page.goto("/");
  await openTool(page,"trend");
  const response=postDrawing(page);
  await clickPlot(page,.39,.35);
  await clickPlot(page,.62,.59);
  const saved=await savedChange(response);
  const modes=[
    async()=>{await page.getByTitle("Place horizontal level (Alt+H)",{exact:true}).click();},
    async()=>{await page.getByRole("button",{name:"Draw",exact:true}).click();await clickPlot(page,.43,.4);},
    async()=>{await page.getByRole("button",{name:"Set anchor 2",exact:true}).click();},
    async()=>{await page.getByRole("button",{name:"Select plan levels",exact:true}).click();await clickPlot(page,.5,.41);},
  ];
  for(const activate of modes){
    await activate();
    await page.keyboard.press("Escape");
    await clickPlot(page,.56,.59);
    expect(await savedScene(page)).toEqual(saved);
    expect(await localLevels(page)).toEqual([]);
  }

  await page.getByRole("button",{name:"Draw",exact:true}).click();
  const start=await plotPoint(page,.42,.37),end=await plotPoint(page,.66,.63);
  await page.mouse.move(start.x,start.y);
  await page.mouse.down();
  await page.mouse.move(end.x,end.y,{steps:8});
  const {plot}=await primaryPlot(page);
  const top=plot.locator("xpath=following-sibling::canvas[1]");
  await expect.poll(()=>coloredPixels(top,[255,220,134])).toBeGreaterThan(0);
  await page.keyboard.press("Alt+h");
  await expect.poll(()=>coloredPixels(top,[255,220,134])).toBe(0);
  await page.mouse.up();
  expect(await savedScene(page)).toEqual(saved);
  expect(await localLevels(page)).toEqual([]);
  await clickPlot(page,.55,.53);
  expect(await localLevels(page)).toHaveLength(1);
});

test("horizontal drags reject outside, missing candle and pointer cancellation",async ({page})=>{
  // Observe the real browser pointer id before the chart claims the gesture.
  await page.addInitScript(()=>document.addEventListener("pointerdown",event=>{
    if((event.target as Element).closest(".primary-chart .chart-input"))
      Object.assign(window,{drawingTestPointer:event.pointerId});
  },true));
  await page.goto("/");
  await openTool(page,"horizontal");
  const before=await savedScene(page);
  const input=page.locator(".primary-chart .chart-input");
  const start=await plotPoint(page,.44,.38);
  const {plot}=await primaryPlot(page);
  const box=(await plot.boundingBox())!;
  for(const end of [{x:box.x+box.width*.5,y:box.y+box.height+3},{x:box.x+box.width-2,y:box.y+box.height*.62}]){
    await page.mouse.move(start.x,start.y);
    await page.mouse.down();
    await page.mouse.move(end.x,end.y,{steps:6});
    await page.mouse.up();
    expect(await savedScene(page)).toEqual(before);
  }

  await page.mouse.move(start.x,start.y);
  await page.mouse.down();
  const pointerId=await page.evaluate(()=>(window as unknown as {drawingTestPointer:number}).drawingTestPointer);
  expect(await input.evaluate((element,id)=>element.hasPointerCapture(id),pointerId)).toBe(true);
  await input.dispatchEvent("pointercancel",{pointerId,isPrimary:true,pointerType:"mouse"});
  expect(await input.evaluate((element,id)=>element.hasPointerCapture(id),pointerId)).toBe(false);
  await page.mouse.up();
  expect(await savedScene(page)).toEqual(before);

  const response=postDrawing(page);
  await clickPlot(page,.56,.63);
  expect((await savedChange(response)).revision).toBe(before.revision+1);
});

test("negative chart prices are rejected and navigation still pans and zooms",async ({page})=>{
  let negative=false;
  await page.route("**/research/api/market/candles?**",async route=>{
    const url=new URL(route.request().url());
    if(!negative||url.searchParams.get("source")!=="DEMO"||url.searchParams.get("symbol")!=="EURUSD")
      return route.continue();
    const response=await route.fetch(),payload=await response.json();
    // Deliberately invalid price domain, but valid chart geometry, to exercise the client guard.
    payload.candles=payload.candles.map((bar:{open:number;high:number;low:number;close:number})=>({
      ...bar,open:bar.open-2,high:bar.high-2,low:bar.low-2,close:bar.close-2,
    }));
    return route.fulfill({response,json:payload});
  });
  await page.goto("/");
  await openTool(page,"horizontal");
  const prior=await savedScene(page);
  await page.getByLabel("Drawing tool").focus();
  await page.keyboard.press("Escape");
  await expect(page.getByText(/Horizontal line.*select 1/)).toHaveCount(0);
  await page.getByLabel("SMT comparison",{exact:true}).selectOption("GBPUSD");
  await expect(page.getByLabel("Comparison placement")).toHaveValue("Overlay");
  const legend=page.locator(".primary-chart .chart-legend-row").first();
  const center=await plotPoint(page,.52,.48);
  await page.mouse.move(center.x,center.y);
  await expect(legend.locator("span")).toHaveCount(4);
  const initial=await legend.innerText();
  const end=await plotPoint(page,.74,.48);
  await page.mouse.down();
  await page.mouse.move(end.x,end.y,{steps:10});
  await page.mouse.up();
  await page.mouse.move(center.x,center.y);
  await expect.poll(()=>legend.innerText()).not.toBe(initial);
  const afterPan=await legend.innerText();
  const offCenter=await plotPoint(page,.65,.48);
  await page.mouse.move(offCenter.x,offCenter.y);
  await page.mouse.wheel(0,-300);
  await page.mouse.move(center.x,center.y);
  await expect.poll(()=>legend.innerText()).not.toBe(afterPan);
  expect(await savedScene(page)).toEqual(prior);

  negative=true;
  const refreshed=page.waitForResponse(res=>res.url().includes("/market/candles?")&&new URL(res.url()).searchParams.get("symbol")==="EURUSD");
  await page.getByTitle("Refresh candles",{exact:true}).click();
  const invalid=await (await refreshed).json();
  expect(invalid.candles.at(-1).high).toBeLessThan(0);
  await page.getByRole("button",{name:"Draw",exact:true}).click();
  await clickPlot(page,.57,.61);
  await expect(page.getByText(/Horizontal line.*select 1/)).toBeVisible();
  expect(await savedScene(page)).toEqual(prior);
});
