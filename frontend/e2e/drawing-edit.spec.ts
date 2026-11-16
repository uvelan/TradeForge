import {writeFile} from "node:fs/promises";
import {expect,test,type Page} from "@playwright/test";

test.use({trace:"on"});

type Anchor={time:number;price:string};
type Drawing={id:string;kind:string;anchors:Anchor[];locked:boolean;visible:boolean;label:string;color:string;created_at:number;updated_at:number};
type Scene={revision:number;schema_version:number;items:Drawing[]};
type Point={x:number;y:number};
const path=(frame="M3")=>"/research/api/drawings?scope="+encodeURIComponent("DEMO:EURUSD:"+frame);
const scene=async(page:Page,frame?:string):Promise<Scene>=>(await page.request.get(path(frame))).json();
const posted=(page:Page)=>page.waitForResponse(res=>res.request().method()==="POST"&&res.url().includes("/drawings?"));
async function saved(response:ReturnType<typeof posted>):Promise<Scene>{
  const res=await response;expect(res.status(),await res.text()).toBe(200);return res.json();
}

async function instrument(page:Page){
  await page.addInitScript(()=>{
    const handles=new WeakMap<HTMLCanvasElement,{x:number;y:number}[]>();
    const strokes=new WeakMap<HTMLCanvasElement,Point[]>();
    const {clearRect,arc,moveTo,lineTo}=CanvasRenderingContext2D.prototype;
    CanvasRenderingContext2D.prototype.moveTo=function(x,y){
      if(this.lineWidth===1.5)strokes.set(this.canvas,[{x,y}]);
      moveTo.call(this,x,y);
    };
    CanvasRenderingContext2D.prototype.lineTo=function(x,y){
      if(this.lineWidth===1.5)strokes.get(this.canvas)?.push({x,y});
      lineTo.call(this,x,y);
    };
    CanvasRenderingContext2D.prototype.clearRect=function(x,y,width,height){
      handles.delete(this.canvas);strokes.delete(this.canvas);clearRect.call(this,x,y,width,height);
    };
    CanvasRenderingContext2D.prototype.arc=function(x,y,r,start,stop,ccw){
      if(r===5&&(this.strokeStyle==="#fff0b8"||this.strokeStyle==="#9caab9")){
        const points=handles.get(this.canvas)??[];
        points.push({x,y});handles.set(this.canvas,points);
      }
      arc.call(this,x,y,r,start,stop,ccw);
    };
    Object.assign(window,{drawingHandleProbe:(canvas:HTMLCanvasElement)=>handles.get(canvas)??[],drawingStrokeProbe:(canvas:HTMLCanvasElement)=>strokes.get(canvas)??[]});
    document.addEventListener("pointerdown",event=>{
      if((event.target as Element).closest(".primary-chart .chart-input"))
        Object.assign(window,{drawingPointer:event.pointerId});
    },true);
  });
}

async function plot(page:Page){
  const row=page.locator(".primary-chart .chart-input tr").first();
  await expect(row.locator("canvas").first()).toBeVisible();
  const widths=await row.locator("td").evaluateAll(cells=>cells.map(cell=>cell.getBoundingClientRect().width));
  return row.locator("td").nth(widths.indexOf(Math.max(...widths))).locator("canvas").first();
}
async function plotBox(page:Page){
  const canvas=await plot(page);
  await canvas.evaluate(node=>{if(!document.fullscreenElement)node.scrollIntoView({block:"center",inline:"center"});});
  const box=await canvas.boundingBox();
  if(!box)throw Error("Plot not rendered");
  return box;
}
async function clickAt(page:Page,x:number,y:number){
  const box=await plotBox(page);
  await page.mouse.click(box.x+box.width*x,box.y+box.height*y);
}
async function handles(page:Page,count:number):Promise<Point[]>{
  await plotBox(page);
  const canvas=(await plot(page)).locator("xpath=following-sibling::canvas[1]");
  const read=(node:HTMLCanvasElement)=>{
    const rect=node.getBoundingClientRect();
    const points=(window as unknown as {drawingHandleProbe:(canvas:HTMLCanvasElement)=>Point[]}).drawingHandleProbe(node);
    return points.map(p=>({x:p.x+rect.left,y:p.y+rect.top}));
  };
  await expect.poll(()=>canvas.evaluate(read)).toHaveLength(count);
  return canvas.evaluate(read);
}
async function clickStroke(page:Page){
  const box=await plotBox(page);
  const canvas=await plot(page);
  const read=(node:HTMLCanvasElement)=>(window as unknown as {drawingStrokeProbe:(canvas:HTMLCanvasElement)=>Point[]}).drawingStrokeProbe(node);
  await expect.poll(()=>canvas.evaluate(read)).toHaveLength(2);
  const [a,b]=await canvas.evaluate(read);
  const point=midpoint(a,b);
  await page.mouse.click(box.x+point.x,box.y+point.y);
}
async function drag(page:Page,start:Point,end:Point){
  await page.mouse.move(start.x,start.y);
  await page.mouse.down();
  await page.mouse.move(end.x,end.y,{steps:8});
  await page.mouse.up();
}
function shifted(start:Point,x:number,y:number){return {x:start.x+x,y:start.y+y};}

async function setup(page:Page,kind:string,points:number[][],withEma=false){
  await instrument(page);
  await page.setViewportSize({width:1536,height:900});
  await page.goto("/");
  await page.getByRole("button",{name:"Market Terminal",exact:true}).click();
  await page.getByRole("combobox",{name:"Market data source"}).selectOption("DEMO");
  await page.getByRole("button",{name:"M3",exact:true}).click();
  if(withEma){
    const evaluated=page.waitForResponse(res=>res.url().endsWith("/indicators/evaluate"));
    await page.getByRole("combobox",{name:"Indicator",exact:true}).selectOption("ema");
    const response=await evaluated;
    expect((await response.json()).results[0].series.EMA.length).toBeGreaterThan(0);
  }
  await page.getByText(/^Drawing toolbox/).click();
  await page.getByLabel("Drawing tool").selectOption(kind);
  await page.getByRole("button",{name:"Draw",exact:true}).click();
  for(const [x,y] of points.slice(0,-1))await clickAt(page,x,y);
  const response=posted(page);
  const [x,y]=points.at(-1)!;
  await clickAt(page,x,y);
  const created=await saved(response);
  const item=created.items.at(-1)!;
  await expect(page.getByLabel("Select drawing")).toHaveValue(item.id);
  return {created,item};
}
function itemAt(version:Scene,id:string){return version.items.find(item=>item.id===id)!;}
function midpoint(a:Point,b:Point){return {x:(a.x+b.x)/2,y:(a.y+b.y)/2};}
function immutableFields(drawing:Drawing){
  const {anchors,updated_at,...fields}=drawing;
  return fields;
}

test("select, move and resize a trendline; reload and undo/redo exact revisions",async ({page},info)=>{
  const {created,item}=await setup(page,"trend",[[.39,.37],[.64,.58]]);
  await clickAt(page,.75,.81);
  await expect(page.getByLabel("Select drawing")).toHaveValue("");
  await clickStroke(page);
  await expect(page.getByLabel("Select drawing")).toHaveValue(item.id);
  let position=await handles(page,2);
  const start=midpoint(...position as [Point,Point]);
  await page.mouse.move(start.x,start.y);await page.mouse.down();
  const end=shifted(start,54,26);
  await page.mouse.move(end.x,end.y,{steps:10});
  expect(await scene(page)).toEqual(created);
  await page.screenshot({path:info.outputPath("trend-preview.png")});
  const body=posted(page);
  await page.mouse.up();
  let previous=await saved(body);
  expect(previous.revision).toBe(created.revision+1);
  const moved=itemAt(previous,item.id);
  expect(immutableFields(moved)).toEqual(immutableFields(item));
  const delta=moved.anchors.map((anchor,i)=>[anchor.time-item.anchors[i].time,Number(anchor.price)-Number(item.anchors[i].price)]);
  expect(delta[0][0]).toBeGreaterThan(0);
  expect(delta[1][0]).toBe(delta[0][0]);
  expect(delta[0][1]).toBeLessThan(0);
  expect(delta[1][1]).toBeCloseTo(delta[0][1],5);

  for(const index of [0,1]){
    position=await handles(page,2);
    const response=posted(page);
    await drag(page,position[index],shifted(position[index],34,index===0?-34:27));
    const next=await saved(response);
    expect(next.revision).toBe(previous.revision+1);
    expect(itemAt(next,item.id).anchors[1-index]).toEqual(itemAt(previous,item.id).anchors[1-index]);
    expect(itemAt(next,item.id).anchors[index]).not.toEqual(itemAt(previous,item.id).anchors[index]);
    if(index===1){
      const undo=posted(page);await page.getByRole("button",{name:"Undo drawing edit"}).click();
      const undone=await saved(undo);expect(undone.items).toEqual(previous.items);expect(undone.revision).toBe(next.revision+1);
      const redo=posted(page);await page.getByRole("button",{name:"Redo drawing edit"}).click();
      const redone=await saved(redo);expect(redone.items).toEqual(next.items);expect(redone.revision).toBe(undone.revision+1);
      previous=redone;
    }else previous=next;
  }
  const same=await handles(page,2);
  await page.mouse.click(same[0].x,same[0].y);
  expect(await scene(page)).toEqual(previous);
  await writeFile(info.outputPath("persisted-trendline.json"),JSON.stringify({before:created,after:previous},null,2)+"\n");
  await page.reload();
  await page.getByText(/^Drawing toolbox/).click();
  await expect(page.getByLabel("Select drawing").locator('option[value="'+item.id+'"]')).toHaveCount(1);
  expect(await scene(page)).toEqual(previous);
});

test("all four rectangle handles edit only their corresponding boundaries",async ({page},info)=>{
  const {created,item}=await setup(page,"rectangle",[[.40,.32],[.65,.66]]);
  let version=created;
  for(const [index,timeIndex,priceIndex,x,y] of [[0,0,0,-20,-20],[1,1,1,20,20],[2,0,1,-18,18],[3,1,0,18,-18]]){
    const points=await handles(page,4);
    const response=posted(page);
    await drag(page,points[index],shifted(points[index],x,y));
    const next=await saved(response);
    expect(next.revision).toBe(version.revision+1);
    const before=itemAt(version,item.id).anchors,after=itemAt(next,item.id).anchors;
    expect(after[1-timeIndex].time).toBe(before[1-timeIndex].time);
    expect(after[1-priceIndex].price).toBe(before[1-priceIndex].price);
    expect(after[timeIndex].time).not.toBe(before[timeIndex].time);
    expect(after[priceIndex].price).not.toBe(before[priceIndex].price);
    version=next;
  }
  await page.screenshot({path:info.outputPath("rectangle-handles.png")});
});

for(const direction of ["long","short"]){
  test(direction+" position rejects crossed handles and preserves direction on a valid resize",async ({page},info)=>{
    const long=direction==="long";
    const {created,item}=await setup(page,"risk-reward",[[.39,.49],[.61,long ? .69 : .25],[.68,long ? .25 : .69]]);
    const points=await handles(page,3);
    const writes:string[]=[];
    page.on("request",request=>{if(request.method()==="POST"&&request.url().includes("/drawings?"))writes.push(request.url());});
    for(const index of [1,2]){
      const crossed={x:points[index].x,y:points[0].y+(points[index].y>points[0].y?-14:14)};
      await drag(page,points[index],crossed);
      expect(await scene(page)).toEqual(created);
      expect(writes).toHaveLength(0);
    }
    const response=posted(page);
    await drag(page,points[2],shifted(points[2],24,long?-24:24));
    const changed=await saved(response);
    expect(changed.revision).toBe(created.revision+1);
    expect(writes).toHaveLength(1);
    const anchors=itemAt(changed,item.id).anchors;
    expect(anchors.slice(0,2)).toEqual(item.anchors.slice(0,2));
    const [entry,stop,target]=anchors.map(p=>Number(p.price));
    expect(long?stop<entry&&entry<target:target<entry&&entry<stop).toBe(true);
    await page.screenshot({path:info.outputPath(direction+"-handles.png")});
  });
}

test("vertical horizontal edit works after zoom, resize and fullscreen with left scale and comparison pane",async ({page},info)=>{
  const {created,item}=await setup(page,"horizontal",[[.47,.42]]);
  await page.getByLabel("SMT comparison",{exact:true}).selectOption("GBPUSD");
  await expect(page.getByLabel("Comparison placement")).toHaveValue("Overlay");
  await page.getByLabel("Chart layout").selectOption("2");
  await page.setViewportSize({width:1920,height:1080});
  const surface=page.locator(".primary-chart .chart-surface");
  await surface.getByRole("button",{name:"Fullscreen chart"}).click();
  await expect(surface.getByRole("button",{name:"Exit fullscreen"})).toBeVisible();
  const box=await plotBox(page);
  expect(box.x).toBeGreaterThan(0); // GBPUSD price scale occupies the left gutter in fullscreen.
  await page.mouse.move(box.x+box.width*.6,box.y+box.height*.35);
  await page.mouse.wheel(0,-200);
  const [handle]=await handles(page,1);
  const response=posted(page);
  await drag(page,handle,shifted(handle,60,83));
  const moved=await saved(response);
  expect(moved.revision).toBe(created.revision+1);
  const anchor=itemAt(moved,item.id).anchors[0];
  expect(anchor.time).toBe(item.anchors[0].time);
  expect(Number(anchor.price)).toBeLessThan(Number(item.anchors[0].price));
  await page.screenshot({path:info.outputPath("horizontal-fullscreen.png")});
  await surface.getByRole("button",{name:"Exit fullscreen"}).click();
  await expect(surface.getByRole("button",{name:"Fullscreen chart"})).toBeVisible();
  expect(await scene(page)).toEqual(moved);
});

test("overlapping strokes prefer the later item; locked and hidden drawings cannot drag",async ({page})=>{
  const {item}=await setup(page,"horizontal",[[.48,.53]]);
  const duplicate=posted(page);
  await page.getByRole("button",{name:"Duplicate drawing",exact:true}).click();
  const doubled=await saved(duplicate),top=doubled.items.at(-1)!;
  await clickAt(page,.77,.15);
  await clickStroke(page);
  await expect(page.getByLabel("Select drawing")).toHaveValue(top.id);

  const lock=posted(page);
  await page.getByRole("button",{name:"Lock",exact:true}).click();
  const locked=await saved(lock);
  const [lockedHandle]=await handles(page,1);
  await drag(page,lockedHandle,shifted(lockedHandle,0,40));
  expect(await scene(page)).toEqual(locked);
  const hide=posted(page);
  await page.getByRole("button",{name:"Hide",exact:true}).click();
  const hidden=await saved(hide);
  expect(itemAt(hidden,top.id).visible).toBe(false);
  await clickAt(page,.77,.15);
  await clickStroke(page);
  await expect(page.getByLabel("Select drawing")).toHaveValue(item.id);
  expect(await scene(page)).toEqual(hidden);
});

test("Escape and pointer cancellation discard edits; a scope change cannot save an obsolete gesture",async ({page},info)=>{
  const {created}=await setup(page,"trend",[[.41,.36],[.63,.59]]);
  const other=await scene(page,"M15");
  const input=page.locator(".primary-chart .chart-input");
  for(const cancel of ["escape","pointercancel","scope"]){
    const [a,b]=await handles(page,2);
    const start=midpoint(a,b),end=shifted(start,47,37);
    await page.mouse.move(start.x,start.y);await page.mouse.down();
    const pointerId=await page.evaluate(()=>(window as unknown as {drawingPointer:number}).drawingPointer);
    expect(await input.evaluate((el,id)=>el.hasPointerCapture(id),pointerId)).toBe(true);
    await page.mouse.move(end.x,end.y,{steps:7});
    if(cancel==="escape"){
      await page.keyboard.press("Escape");
      expect(await input.evaluate((el,id)=>el.hasPointerCapture(id),pointerId)).toBe(false);
    }else if(cancel==="pointercancel"){
      await input.dispatchEvent("pointercancel",{pointerId,pointerType:"touch",isPrimary:true});
      expect(await input.evaluate((el,id)=>el.hasPointerCapture(id),pointerId)).toBe(false);
    }else{
      const old=await input.elementHandle();
      await page.getByRole("button",{name:"M15",exact:true}).evaluate((button:HTMLButtonElement)=>button.click());
      await expect.poll(()=>old!.evaluate(el=>el.isConnected)).toBe(false);
      expect(await old!.evaluate((el,id)=>el.hasPointerCapture(id),pointerId)).toBe(false);
    }
    await page.mouse.up();
    expect(await scene(page)).toEqual(created);
    expect(await scene(page,"M15")).toEqual(other);
  }
  await expect(page.locator(".primary-chart .pane-label")).toContainText("M15");
  await page.screenshot({path:info.outputPath("canceled-scope.png")});
});

test("a native touchscreen cancel releases the selected drawing without writing",async ({page})=>{
  const {created}=await setup(page,"horizontal",[[.45,.52]]);
  const [point]=await handles(page,1);
  const session=await page.context().newCDPSession(page);
  await session.send("Emulation.setTouchEmulationEnabled",{enabled:true,maxTouchPoints:1});
  const input=page.locator(".primary-chart .chart-input");
  await session.send("Input.dispatchTouchEvent",{type:"touchStart",touchPoints:[point]});
  const pointerId=await page.evaluate(()=>(window as unknown as {drawingPointer:number}).drawingPointer);
  expect(await input.evaluate((el,id)=>el.hasPointerCapture(id),pointerId)).toBe(true);
  await session.send("Input.dispatchTouchEvent",{type:"touchMove",touchPoints:[shifted(point,0,34)]});
  await session.send("Input.dispatchTouchEvent",{type:"touchCancel",touchPoints:[]});
  expect(await input.evaluate((el,id)=>el.hasPointerCapture(id),pointerId)).toBe(false);
  expect(await scene(page)).toEqual(created);
});

test("a real 409 blocks further edits and reload preserves the competing drawing",async ({page},info)=>{
  const {created,item}=await setup(page,"horizontal",[[.47,.43]]);
  const [point]=await handles(page,1);
  const response=await page.request.post(path(),{data:{...created,
    items:created.items.map(row=>row.id===item.id?{...row,label:"Competing edit"}:row)}});
  expect(response.status()).toBe(200);
  const remote=await response.json();
  const conflict=posted(page);
  await drag(page,point,shifted(point,0,46));
  expect((await conflict).status()).toBe(409);
  await expect(page.getByRole("alert")).toContainText("Drawings changed in another window");
  await expect(page.getByRole("button",{name:"Draw",exact:true})).toBeDisabled();
  const unexpected:string[]=[];
  page.on("request",request=>{if(request.method()==="POST"&&request.url().includes("/drawings?"))unexpected.push(request.url());});
  const [stillSaved]=await handles(page,1);
  await drag(page,stillSaved,shifted(stillSaved,0,34));
  expect(unexpected).toHaveLength(0);
  expect(await scene(page)).toEqual(remote);
  await page.screenshot({path:info.outputPath("conflict.png")});
  await page.getByRole("button",{name:"Reload drawings"}).click();
  await expect(page.getByRole("button",{name:"Draw",exact:true})).toBeEnabled();
  await expect(page.getByLabel("Select drawing").locator('option[value="'+item.id+'"]')).toContainText("Competing edit");
  expect(await scene(page)).toEqual(remote);
});

test("a canonical EMA overlay leaves creating and editing a drawing functional",async ({page},info)=>{
  const {created,item}=await setup(page,"horizontal",[[.48,.53]],true);
  const [handle]=await handles(page,1);
  const response=posted(page);
  await drag(page,handle,shifted(handle,0,31));
  const edited=await saved(response);
  expect(edited.revision).toBe(created.revision+1);
  expect(itemAt(edited,item.id).anchors[0].time).toBe(item.anchors[0].time);
  expect(itemAt(edited,item.id).anchors[0].price).not.toBe(item.anchors[0].price);
  await page.screenshot({path:info.outputPath("drawing-with-ema.png")});
});
