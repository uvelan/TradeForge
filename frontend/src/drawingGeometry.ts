import type {Anchor,Drawing} from './Drawings';
import type {DrawPoint} from './drawingPlacement';

export type DrawingHandle={timeIndex:number;priceIndex:number};
export type PanePoint={x:number;y:number};
export type ProjectedHandle=PanePoint&{anchor:DrawingHandle};

// Anchor 1 (index 0) is the 100% swing origin and Anchor 2 (index 1) is the 0% swing extreme.
export const FIBONACCI_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1] as const;

export function fibonacciPrice(anchor100: number, anchor0: number, ratio: number): number {
  return anchor0 + ratio * (anchor100 - anchor0);
}

export function fibonacciPercentLabel(ratio: number): string {
  if (ratio === 0) return '0%';
  if (ratio === 1) return '100%';
  if (ratio === 0.5) return '50%';
  return `${(ratio * 100).toFixed(1)}%`;
}

export function handlesFor(kind:Drawing['kind'],points:PanePoint[]):ProjectedHandle[]{
  const point=(timeIndex:number,priceIndex=timeIndex)=>({
    x:points[timeIndex].x,y:points[priceIndex].y,anchor:{timeIndex,priceIndex},
  });
  switch(kind){
    case 'horizontal':return [point(0)];
    case 'trend':
    case 'ray':
    case 'fib':return [point(0),point(1)];
    case 'rectangle':return [point(0),point(1),point(0,1),point(1,0)];
    case 'risk-reward':return [point(0),point(1),point(2)];
    default:return [];
  }
}

export function distanceToStroke(p:PanePoint,a:PanePoint,b:PanePoint){
  const dx=b.x-a.x,dy=b.y-a.y;
  const t=Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy||1)));
  return Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy);
}

export function distanceToRay(p:PanePoint,a:PanePoint,b:PanePoint){
  const dx=b.x-a.x,dy=b.y-a.y;
  const t=Math.max(0,((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy||1));
  return Math.hypot(p.x-a.x-t*dx,p.y-a.y-t*dy);
}

export function bodyDistance(kind:Drawing['kind'],p:PanePoint,points:PanePoint[]){
  const [a,b]=points;
  if(kind==='horizontal')return Math.abs(p.y-a.y);
  if(kind==='trend')return distanceToStroke(p,a,b);
  if(kind==='ray')return distanceToRay(p,a,b);
  const xs=points.map(point=>point.x),ys=points.map(point=>point.y);
  const left=Math.min(...xs),right=Math.max(...xs),top=Math.min(...ys),bottom=Math.max(...ys);
  if(kind==='fib'){
    const fibRight=Math.max(right,left+96);
    let minDistance=distanceToStroke(p,a,b);
    for(const ratio of FIBONACCI_LEVELS){
      const y=b.y+ratio*(a.y-b.y);
      minDistance=Math.min(minDistance,distanceToStroke(p,{x:left,y},{x:fibRight,y}));
    }
    return minDistance;
  }
  return Math.min(distanceToStroke(p,{x:left,y:top},{x:right,y:top}),
    distanceToStroke(p,{x:left,y:bottom},{x:right,y:bottom}),
    distanceToStroke(p,{x:left,y:top},{x:left,y:bottom}),
    distanceToStroke(p,{x:right,y:top},{x:right,y:bottom}),
    kind==='risk-reward'?distanceToStroke(p,{x:left,y:a.y},{x:right,y:a.y}):Infinity);
}

export function sameAnchors(a:Anchor[],b:Anchor[]){
  return a.length===b.length&&a.every((point,i)=>point.time===b[i].time&&Number(point.price)===Number(b[i].price));
}

export function transformDrawing(item:Drawing,handle:DrawingHandle|undefined,start:DrawPoint,end:DrawPoint,
  digits:number,hasBar:(time:number)=>boolean):Anchor[]|undefined{
  const horizontal=item.kind==='horizontal';
  if(!horizontal&&(end.time===undefined||(!handle&&start.time===undefined)))return;
  const shift=horizontal||handle?0:end.time!-start.time!;
  const change=end.price-start.price;
  const anchors=item.anchors.map((point,index)=>{
    const time=horizontal?point.time:handle?(index===handle.timeIndex?end.time!:point.time):point.time+shift;
    const price=handle?(index===handle.priceIndex?end.price:Number(point.price)):Number(point.price)+change;
    if(time===point.time&&price===Number(point.price))return point;
    return {time,price:price.toFixed(Math.max(0,Math.min(digits,10)))};
  });
  if(anchors.some((point,index)=>!Number.isSafeInteger(point.time)||point.time<0||point.time>32_503_680_000||
      (point.time!==item.anchors[index].time&&!hasBar(point.time))||!/^\d{1,12}(\.\d{1,10})?$/.test(point.price)))return;
  const [a,b,c]=anchors;
  if((item.kind==='trend'||item.kind==='ray')&&a.time===b.time&&Number(a.price)===Number(b.price))return;
  if(item.kind==='fib'&&Number(a.price)===Number(b.price))return;
  if(item.kind==='rectangle'&&(a.time===b.time||Number(a.price)===Number(b.price)))return;
  if(item.kind==='risk-reward'){
    const direction=Math.sign(Number(item.anchors[0].price)-Number(item.anchors[1].price));
    if(direction*(Number(a.price)-Number(b.price))<=0||direction*(Number(c.price)-Number(a.price))<=0)return;
  }
  return anchors;
}
