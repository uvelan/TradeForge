import type {Page} from '@playwright/test';

type Stroke={color:string;row:number};
type Gesture={type:'selection'|'save';selector:string;start?:number;observed?:number}
  | {type:'drag';selector:string;start?:number;observed?:number;expectedRow:number};
type ChartSurface={left:number;top:number;width:number;height:number;strokes:Stroke[]};

declare global {
  interface Window {
    drawingCapacity:{
      surface:(selector:string)=>ChartSurface|undefined;
      armSelection:(selector:string,select:HTMLSelectElement,id:string)=>void;
      armDrag:(selector:string,expectedRow:number)=>void;
      armSave:(selector:string)=>void;
      observedMs:()=>number|undefined;
      started:()=>number|undefined;
      frames:()=>void;
      stopFrames:()=>number[];
    };
  }
}

// Read completed horizontal strokes on the application's actual media canvases.
// A distinct color identifies each seeded drawing. A gold stroke is the edit preview.
export async function installDrawingCapacityProbe(page:Page){
  await page.addInitScript(()=>{
    let armed:Gesture|undefined;
    const painted=new WeakMap<HTMLCanvasElement,Map<string,number>>();
    const starts=new WeakMap<CanvasRenderingContext2D,Stroke>();
    const {clearRect,beginPath,moveTo,stroke}=CanvasRenderingContext2D.prototype;
    CanvasRenderingContext2D.prototype.clearRect=function(x,y,width,height){
      painted.delete(this.canvas);
      clearRect.call(this,x,y,width,height);
    };
    CanvasRenderingContext2D.prototype.beginPath=function(){
      starts.delete(this);
      beginPath.call(this);
    };
    CanvasRenderingContext2D.prototype.moveTo=function(x,y){
      if(x===0&&this.lineWidth===1.5){
        const color=this.strokeStyle;
        if(typeof color==='string'&&(color.startsWith('#c1')||color==='#ffdc86'))starts.set(this,{color,row:y});
      }
      moveTo.call(this,x,y);
    };
    CanvasRenderingContext2D.prototype.stroke=function(...args:[]|[Path2D]){
      if(args.length)stroke.call(this,args[0]);else stroke.call(this);
      const point=starts.get(this);
      if(!point)return;
      if(point.color==='#ffdc86'&&armed?.type==='drag'&&armed.start!==undefined&&armed.observed===undefined&&
        Math.abs(point.row-armed.expectedRow)<4&&this.canvas.closest(armed.selector))armed.observed=performance.now();
      let rows=painted.get(this.canvas);
      if(!rows){rows=new Map();painted.set(this.canvas,rows);}
      rows.set(point.color,point.row);
    };

    function capture(event:PointerEvent){
      if(!armed||armed.start!==undefined||!(event.target instanceof Element)||!event.target.closest(armed.selector))return;
      if((armed.type==='drag'&&event.type==='pointermove')||
        (armed.type!=='drag'&&event.type==='pointerup')){
        armed.start=performance.now();
      }
    }
    document.addEventListener('pointermove',capture,true);
    document.addEventListener('pointerup',capture,true);

    let intervals:number[]=[];
    let previous:number|undefined;
    let frame=0;
    function tick(now:number){
      if(previous!==undefined)intervals.push(now-previous);
      previous=now;
      frame=requestAnimationFrame(tick);
    }
    window.drawingCapacity={
      surface:selector=>{
        const plot=document.querySelector(selector);
        if(!plot)return;
        const canvases=Array.from(plot.querySelector('tr')?.querySelectorAll('canvas')??[]);
        const widest=canvases.reduce((width,canvas)=>Math.max(width,canvas.getBoundingClientRect().width),0);
        const drawingCanvases=canvases.filter(canvas=>canvas.getBoundingClientRect().width===widest);
        const box=drawingCanvases[0]?.getBoundingClientRect();
        if(!box||!box.width||!box.height)return;
        const strokes=drawingCanvases.flatMap(canvas=>Array.from(painted.get(canvas)??[],([color,row])=>({color,row})));
        return {left:box.left,top:box.top,width:box.width,height:box.height,
          strokes:strokes.filter(({row})=>row>=0&&row<box.height)};
      },
      armSelection:(selector,select,id)=>{
        const selection:Gesture={type:'selection',selector};
        armed=selection;
        function observe(){
          if(armed!==selection||!select.isConnected)return;
          if(selection.start!==undefined&&select.value===id){selection.observed=performance.now();return;}
          requestAnimationFrame(observe);
        }
        requestAnimationFrame(observe);
      },
      armDrag:(selector,expectedRow)=>{armed={type:'drag',selector,expectedRow};},
      armSave:selector=>{armed={type:'save',selector};},
      observedMs:()=>armed?.start!==undefined&&armed.observed!==undefined?armed.observed-armed.start:undefined,
      started:()=>armed?.start,
      frames:()=>{intervals=[];previous=undefined;frame=requestAnimationFrame(tick);},
      stopFrames:()=>{cancelAnimationFrame(frame);return intervals;},
    };
  });
}
