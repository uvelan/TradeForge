export type PlacementDrag='none'|'release-point'|'two-points';
export type DrawPoint={x:number;y:number;price:number;time?:number};

// The pointer belongs to the drawing only after a valid primary-plot down.
export function installPlacement(element:HTMLElement, placement:{
  armed:()=>boolean;
  drag:()=>PlacementDrag;
  locate:(event:PointerEvent)=>DrawPoint|undefined;
  preview:(points:DrawPoint[])=>void;
  commit:(points:DrawPoint[])=>void;
  activate:()=>void;
}){
  let press:{id:number;start:DrawPoint;clientX:number;clientY:number;drag:PlacementDrag}|undefined;
  let suppressClick=false;
  function reset(){
    const id=press?.id;
    if(press)suppressClick=true;
    press=undefined;
    if(id!==undefined&&element.hasPointerCapture(id))element.releasePointerCapture(id);
    placement.preview([]);
  }
  function down(event:PointerEvent){
    suppressClick=false;
    placement.activate();
    if(!placement.armed()||!event.isPrimary||event.button!==0||press)return;
    const point=placement.locate(event);
    if(!point)return;
    press={id:event.pointerId,start:point,clientX:event.clientX,clientY:event.clientY,drag:placement.drag()};
    element.setPointerCapture(event.pointerId);
    event.preventDefault();
    event.stopPropagation();
  }
  function endpoints(event:PointerEvent,point:DrawPoint){
    if(!press||Math.hypot(press.clientX-event.clientX,press.clientY-event.clientY)<=6)return [point];
    if(press.drag==='release-point')return [point];
    return press.drag==='two-points'?[press.start,point]:[];
  }
  function move(event:PointerEvent){
    if(!placement.armed()||!event.isPrimary)return;
    if(press&&press.id!==event.pointerId)return;
    const point=placement.locate(event);
    placement.preview(point?endpoints(event,point):[]);
    if(press)event.stopPropagation();
  }
  function up(event:PointerEvent){
    if(press?.id!==event.pointerId)return;
    const point=placement.armed()?placement.locate(event):undefined;
    const points=point?endpoints(event,point):[];
    event.stopPropagation();
    suppressClick=true;
    reset();
    if(points.length)placement.commit(points);
  }
  function cancel(event:PointerEvent){
    if(press?.id===event.pointerId)reset();
  }
  function click(event:MouseEvent){
    if(!suppressClick)return;
    suppressClick=false;
    event.stopPropagation();
  }
  function leave(){
    if(!press)placement.preview([]);
  }
  element.addEventListener('click',click,true);
  element.addEventListener('pointerdown',down,true);
  element.addEventListener('pointermove',move,true);
  element.addEventListener('pointerup',up,true);
  element.addEventListener('pointercancel',cancel,true);
  element.addEventListener('lostpointercapture',cancel);
  element.addEventListener('pointerleave',leave);
  return {reset,dispose(){
    reset();
    element.removeEventListener('click',click,true);
    element.removeEventListener('pointerdown',down,true);
    element.removeEventListener('pointermove',move,true);
    element.removeEventListener('pointerup',up,true);
    element.removeEventListener('pointercancel',cancel,true);
    element.removeEventListener('lostpointercapture',cancel);
    element.removeEventListener('pointerleave',leave);
  }};
}
