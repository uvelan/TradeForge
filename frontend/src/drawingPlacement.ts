export type PlacementDrag='none'|'release-point'|'two-points';
export type DrawPoint={x:number;y:number;price:number;time?:number};
type EditGesture={update:(point?:DrawPoint)=>void;commit:(point:DrawPoint)=>void;cancel:()=>void};
type Press={id:number;start:DrawPoint;clientX:number;clientY:number}&
  ({kind:'placement';drag:PlacementDrag}|{kind:'edit';gesture:EditGesture});

// The pointer belongs to the drawing only after a valid primary-plot down.
export function installPlacement(element:HTMLElement, placement:{
  armed:()=>boolean;
  drag:()=>PlacementDrag;
  locate:(event:PointerEvent)=>DrawPoint|undefined;
  preview:(points:DrawPoint[])=>void;
  commit:(points:DrawPoint[])=>void;
  activate:()=>void;
  startEdit?:(point:DrawPoint)=>EditGesture|undefined;
  select?:(point:DrawPoint)=>void;
}){
  let press:Press|undefined;
  let suppressClick=false;
  let tap:{id:number;clientX:number;clientY:number}|undefined;
  function reset(){
    tap=undefined;
    const id=press?.id;
    if(press)suppressClick=true;
    if(press?.kind==='edit')press.gesture.cancel();
    press=undefined;
    if(id!==undefined&&element.hasPointerCapture(id))element.releasePointerCapture(id);
    placement.preview([]);
  }
  function down(event:PointerEvent){
    suppressClick=false;
    placement.activate();
    if(!event.isPrimary||event.button!==0||press)return;
    const point=placement.locate(event);
    if(!point)return;
    const start={id:event.pointerId,start:point,clientX:event.clientX,clientY:event.clientY};
    if(placement.armed())press={...start,kind:'placement',drag:placement.drag()};
    else{
      const gesture=placement.startEdit?.(point);
      if(!gesture){tap=start;return;}
      press={...start,kind:'edit',gesture};
    }
    element.setPointerCapture(event.pointerId);
    event.preventDefault();
    event.stopPropagation();
  }
  function moved(event:PointerEvent,origin:{clientX:number;clientY:number},threshold:number){
    return Math.hypot(origin.clientX-event.clientX,origin.clientY-event.clientY)>threshold;
  }
  function endpoints(event:PointerEvent,point:DrawPoint){
    if(press?.kind!=='placement'||!moved(event,press,6))return [point];
    if(press.drag==='release-point')return [point];
    return press.drag==='two-points'?[press.start,point]:[];
  }
  function move(event:PointerEvent){
    if(tap?.id===event.pointerId&&moved(event,tap,5))tap=undefined;
    if(!event.isPrimary||(press&&press.id!==event.pointerId))return;
    if(press?.kind==='edit'){
      press.gesture.update(moved(event,press,3)?placement.locate(event):undefined);
      event.stopPropagation();
      return;
    }
    if(!placement.armed())return;
    const point=placement.locate(event);
    placement.preview(point?endpoints(event,point):[]);
    if(press)event.stopPropagation();
  }
  function up(event:PointerEvent){
    if(tap?.id===event.pointerId){
      const point=!moved(event,tap,5)&&!placement.armed()?placement.locate(event):undefined;
      tap=undefined;
      if(point)placement.select?.(point);
    }
    if(press?.id!==event.pointerId)return;
    if(press.kind==='edit'){
      const edit=press;
      const point=moved(event,edit,3)?placement.locate(event):undefined;
      event.stopPropagation();
      reset();
      if(point)edit.gesture.commit(point);
      return;
    }
    const point=placement.armed()?placement.locate(event):undefined;
    const points=point?endpoints(event,point):[];
    event.stopPropagation();
    suppressClick=true;
    reset();
    if(points.length)placement.commit(points);
  }
  function cancel(event:PointerEvent){
    if(tap?.id===event.pointerId||press?.id===event.pointerId)reset();
  }
  function click(event:MouseEvent){
    if(!suppressClick)return;
    suppressClick=false;
    event.stopPropagation();
  }
  function leave(){
    if(!press){tap=undefined;placement.preview([]);}
  }
  element.addEventListener('click',click,true);
  element.addEventListener('pointerdown',down,true);
  element.addEventListener('pointermove',move,true);
  element.addEventListener('pointerup',up,true);
  element.addEventListener('pointercancel',cancel,true);
  element.addEventListener('lostpointercapture',cancel);
  element.addEventListener('pointerleave',leave);
  return {reset,cancelEdit(){if(press?.kind==='edit')reset();},dispose(){
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
