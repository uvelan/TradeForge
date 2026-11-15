import {useEffect,useRef,useState} from 'react';
import type {IChartApi,IPrimitivePaneRenderer,PrimitiveHoveredItem,ISeriesApi,ISeriesPrimitive,Time,UTCTimestamp} from 'lightweight-charts';
import {api} from './api';
import type {Candle} from './types';

export const tools={
  horizontal:['Horizontal line',1],vertical:['Vertical line',1],trend:['Trendline',2],ray:['Ray',2],
  rectangle:['Rectangle',2],'price-range':['Price range',2],'time-range':['Time range',2],
  'risk-reward':['Risk/reward',3],entry:['Entry line',1],stop:['Stop line',1],target:['Target line',1],text:['Text',1],
  session:['Session high/low',2],liquidity:['Liquidity zone',2],fvg:['Manual FVG zone',2],smt:['Manual SMT connector',2],measure:['Measurement',2],
} as const;
type DrawingTool=keyof typeof tools;
export type Anchor={time:number;price:string};
export type Drawing={
  id:string;kind:DrawingTool;anchors:Anchor[];label:string;color:string;visible:boolean;locked:boolean;created_at:number;updated_at:number;
};
type Version={schema_version:1;revision:number;items:Drawing[]};
type Edit={kind:DrawingTool;anchors:Anchor[];item?:Drawing;mode?:'move'|number};
const now=()=>Math.floor(Date.now()/1000);

function bracketsEntry(anchors:Anchor[]){
  const [entry,stop,target]=anchors.map(point=>Number(point.price));
  return (stop<entry&&entry<target)||(target<entry&&entry<stop);
}

export function useDrawingBook(scope:string) {
  const endpoint='/drawings?'+new URLSearchParams({scope});
  const [loaded,setLoaded]=useState<{scope:string;version:Version}>();
  const [error,setError]=useState('');
  const [refresh,setRefresh]=useState(0);
  const [saving,setSaving]=useState(false);
  const [selected,setSelected]=useState('');
  const [edit,setEdit]=useState<Edit>();
  const [snap,setSnap]=useState(false);
  const [history,setHistory]=useState<{undo:Drawing[][];redo:Drawing[][]}>({undo:[],redo:[]});
  const current=loaded?.scope===scope?loaded.version:undefined;
  const active=useRef<symbol|undefined>(undefined);
  const writing=useRef(false);
  useEffect(()=>{
    if(!scope)return;
    const key=Symbol(scope);
    active.current=key;
    setLoaded(undefined);setError('');setEdit(undefined);setSelected('');setSaving(false);writing.current=false;
    setHistory({undo:[],redo:[]});
    api.get<Version>(endpoint).then(version=>{if(active.current===key)setLoaded({scope,version});})
      .catch(reason=>{if(active.current===key)setError(reason.message);});
    return ()=>{if(active.current===key)active.current=undefined;};
  },[scope,endpoint,refresh]);
  async function save(items:Drawing[],mode:'edit'|'undo'|'redo'='edit'){
    if(!current||writing.current)return;
    const key=active.current;
    writing.current=true;
    setSaving(true);setError('');
    try{
      const version=await api.post<Version>(endpoint,{...current,items});
      if(active.current!==key)return;
      setLoaded({scope,version});
      const next=mode==='undo'?{undo:history.undo.slice(0,-1),redo:[...history.redo,current.items]}:
        mode==='redo'?{undo:[...history.undo,current.items],redo:history.redo.slice(0,-1)}:
        {undo:[...history.undo,current.items].slice(-25),redo:[]};
      setHistory(next);
    }catch(reason){if(active.current===key)setError((reason as Error).message);}
    finally{if(active.current===key){writing.current=false;setSaving(false);}}
  }
  function begin(kind:DrawingTool){
    if(current&&current.items.length<100&&!writing.current)setEdit({kind,anchors:[]});
  }
  function point(bar:Candle,price:number,digits:number):Anchor {
    const nearest=snap?(Math.abs(price-bar.high)<Math.abs(price-bar.low)?bar.high:bar.low):price;
    return {time:bar.time,price:nearest.toFixed(Math.min(digits,10))};
  }
  function anchor(points:{bar:Candle;price:number}[],digits:number) {
    if(!edit||!current||writing.current||!points.length||points.some(point=>point.price<0))return;
    const last=points[points.length-1];
    const next=point(last.bar,last.price,digits);
    const nearest=Number(next.price);
    if(edit.item&&edit.mode!==undefined){
      const item=edit.item;
      const anchors=item.anchors.map((point,index)=>{
        if(edit.mode===index)return next;
        if(edit.mode==='move'){
          return {time:point.time+next.time-item.anchors[0].time,
            price:(Number(point.price)+nearest-Number(item.anchors[0].price)).toFixed(Math.min(digits,10))};
        }
        return point;
      });
      if(anchors.some(point=>point.time<0||Number(point.price)<0)){setError('Move would leave the supported time or price range');return;}
      if(item.kind==='risk-reward'&&!bracketsEntry(anchors)){
        setError('Stop and target must bracket entry. Select the anchor again.');
        return;
      }
      setEdit(undefined);void save(current.items.map(drawing=>drawing.id===item.id?{...item,anchors,updated_at:now()}:drawing));
      return;
    }
    const additions=points.map(({bar,price})=>point(bar,price,digits));
    const anchors=[...edit.anchors,...additions];
    if(anchors.length>tools[edit.kind][1]||
      (additions.length>1&&additions[0].time===next.time&&additions[0].price===next.price))return;
    if(anchors.length<tools[edit.kind][1]){setEdit({...edit,anchors});return;}
    if(edit.kind==='risk-reward'&&!bracketsEntry(anchors)){
      setError('Target and stop must be on opposite sides of entry. Select the target again.');
      return;
    }
    const item:Drawing={id:crypto.randomUUID(),kind:edit.kind,anchors,label:edit.kind==='text'?'Annotation':'',
      visible:true,locked:false,color:'#55d9b5',created_at:now(),updated_at:now()};
    setEdit(undefined);setSelected(item.id);
    void save([...current.items,item]);
  }
  return {current,error,saving,selected,setSelected,edit,setEdit,snap,setSnap,history,save,begin,anchor,point,
    reload:()=>setRefresh(value=>value+1)};
}

export function DrawingToolbox({book,onBegin}: {book:ReturnType<typeof useDrawingBook>;onBegin:(kind:DrawingTool)=>void}) {
  const [tool,setTool]=useState<DrawingTool>('trend');
  const item=book.current?.items.find(drawing=>drawing.id===book.selected);
  const [draft,setDraft]=useState<{id:string;label:string;color:string}>();
  const options=draft?.id===item?.id?draft:undefined;
  const blocked=book.saving||!book.current;
  const update=(changes:Partial<Drawing>)=>{
    if(item&&book.current)void book.save(book.current.items.map(drawing=>drawing.id===item.id?
      {...drawing,...changes,updated_at:now()}:drawing));
  };
  return <details className="data-trust"><summary>Drawing toolbox {book.current?`(${book.current.items.length})`:''}</summary>
    {book.error&&<p role="alert">{book.error} <button className="small-tool" disabled={book.saving} onClick={book.reload}>Reload drawings</button></p>}
    {!book.current&&!book.error&&<p role="status">Loading drawings</p>}
    <div className="research-controls">
      <label>Drawing tool<select value={tool} onChange={event=>setTool(event.target.value as DrawingTool)}>
        {Object.entries(tools).map(([value,[label]])=><option key={value} value={value}>{label}</option>)}
      </select></label>
      <button className="small-tool" disabled={blocked||book.current?.items.length===100} onClick={()=>onBegin(tool)}>Draw</button>
      <label><input type="checkbox" checked={book.snap} onChange={event=>book.setSnap(event.target.checked)}/> Snap to wick</label>
      {book.edit&&<button className="small-tool" onClick={()=>book.setEdit(undefined)}>Cancel drawing</button>}
      <button className="small-tool" disabled={blocked||!book.history.undo.length}
        onClick={()=>void book.save(book.history.undo.at(-1)!,'undo')}>Undo drawing edit</button>
      <button className="small-tool" disabled={blocked||!book.history.redo.length}
        onClick={()=>void book.save(book.history.redo.at(-1)!,'redo')}>Redo drawing edit</button>
      <button className="small-tool" disabled={blocked||!book.current?.items.some(drawing=>!drawing.locked)} onClick={()=>{
        if(book.current&&window.confirm('Remove all unlocked drawings?'))void book.save(book.current.items.filter(drawing=>drawing.locked));
      }}>Clear drawings</button>
    </div>
    {Boolean(book.current?.items.length)&&<label className="field">Select drawing<select value={book.selected}
      onChange={event=>{book.setSelected(event.target.value);setDraft(undefined);}}>
      <option value="">Choose drawing</option>{book.current?.items.map(drawing=>
        <option key={drawing.id} value={drawing.id}>{drawing.label||tools[drawing.kind][0]} · {drawing.anchors[0].price}{drawing.locked?' · locked':''}</option>)}
    </select></label>}
    {item&&<div className="research-controls">
      <label>Label<input value={options?.label??item.label} maxLength={120} disabled={item.locked}
        onChange={event=>setDraft({id:item.id,color:options?.color??item.color,label:event.target.value})}/></label>
      <label>Color<input type="color" value={options?.color??item.color} disabled={item.locked}
        onChange={event=>setDraft({id:item.id,color:event.target.value,label:options?.label??item.label})}/></label>
      <button className="small-tool" disabled={blocked||item.locked||!options} onClick={()=>{update({label:options?.label,color:options?.color});setDraft(undefined);}}>Save style</button>
      <button className="small-tool" disabled={blocked} onClick={()=>update({visible:!item.visible})}>{item.visible?'Hide':'Show'}</button>
      <button className="small-tool" disabled={blocked} onClick={()=>update({locked:!item.locked})}>{item.locked?'Unlock':'Lock'}</button>
      <button className="small-tool" disabled={blocked||item.locked} onClick={()=>book.setEdit({kind:item.kind,anchors:[],item,mode:'move'})}>Move drawing</button>
      {item.anchors.map((_,index)=><button key={index} className="small-tool" disabled={blocked||item.locked}
        onClick={()=>book.setEdit({kind:item.kind,anchors:[],item,mode:index})}>Set anchor {index+1}</button>)}
      <button className="small-tool" disabled={blocked||book.current?.items.length===100} onClick={()=>{
        if(!book.current)return;
        const copy={...item,id:crypto.randomUUID(),locked:false,created_at:now(),updated_at:now()};
        book.setSelected(copy.id);void book.save([...book.current.items,copy]);
      }}>Duplicate drawing</button>
      <button className="small-tool" disabled={blocked||item.locked} onClick={()=>{
        if(book.current)void book.save(book.current.items.filter(drawing=>drawing.id!==item.id));
        book.setSelected('');
      }}>Delete drawing</button>
    </div>}
  </details>;
}

export class DrawingLayer implements ISeriesPrimitive<Time> {
  items:Drawing[]=[];
  repaint?:()=>void;
  preview?:{kind:Drawing['kind'];anchors:Anchor[]};
  showPreview(preview?:DrawingLayer['preview']){this.preview=preview;this.repaint?.();}
  constructor(private chart:IChartApi,private series:ISeriesApi<'Candlestick'>){}
  attached({requestUpdate}:{requestUpdate:()=>void}){this.repaint=requestUpdate;}
  detached(){this.repaint=undefined;}
  update(items:Drawing[]){this.items=items;this.repaint?.();}
  hitTest(x:number,y:number):PrimitiveHoveredItem|null{
    const scale=this.chart.timeScale();
    const distance=(ax:number,ay:number,bx:number,by:number)=>{
      const dx=bx-ax,dy=by-ay;
      const t=Math.max(0,Math.min(1,((x-ax)*dx+(y-ay)*dy)/(dx*dx+dy*dy||1)));
      return Math.hypot(x-ax-t*dx,y-ay-t*dy);
    };
    let hit:PrimitiveHoveredItem|null=null;
    for(const item of this.items){
      if(!item.visible||!['horizontal','trend','rectangle','risk-reward'].includes(item.kind))continue;
      const points=item.anchors.map(anchor=>({
        x:scale.timeToCoordinate(anchor.time as UTCTimestamp),y:this.series.priceToCoordinate(Number(anchor.price)),
      }));
      const [a,b,c]=points;
      if(!a||a.y===null)continue;
      let d=Infinity;
      if(item.kind==='horizontal')d=Math.abs(y-a.y);
      else if(a.x!==null&&b?.x!==null&&b?.x!==undefined&&b.y!==null){
        if(item.kind==='trend')d=distance(a.x,a.y,b.x,b.y);
        else{
          const left=Math.min(a.x,b.x,c?.x??a.x),right=Math.max(a.x,b.x,c?.x??a.x);
          const top=Math.min(a.y,b.y,c?.y??a.y),bottom=Math.max(a.y,b.y,c?.y??a.y);
          d=Math.min(distance(left,top,right,top),distance(left,bottom,right,bottom),
            distance(left,top,left,bottom),distance(right,top,right,bottom));
          if(item.kind==='risk-reward')d=Math.min(d,distance(left,a.y,right,a.y));
        }
      }
      if(d<=6&&d<=(hit?.distance??Infinity))hit={distance:d,externalId:'drawing:'+item.id,zOrder:'normal'};
    }
    return hit;
  }
  private renderer(previewOnly:boolean):IPrimitivePaneRenderer{return {draw:target=>target.useMediaCoordinateSpace(({context:ctx,mediaSize})=>{
    const {width,height}=mediaSize;
    const timeScale=this.chart.timeScale();
    const preview=this.preview;
    const items=previewOnly?(preview?.anchors.length?[{...preview,visible:true,label:'',color:'#ffdc86'}]:[]):this.items;
    for(const item of items){
      if(!item.visible)continue;
      const [a,b,c]=item.anchors;
      const x=timeScale.timeToCoordinate(a.time as UTCTimestamp),y=this.series.priceToCoordinate(Number(a.price));
      const xx=b?timeScale.timeToCoordinate(b.time as UTCTimestamp):null,yy=b?this.series.priceToCoordinate(Number(b.price)):null;
      if(y===null)continue;
      ctx.save();
      ctx.strokeStyle=item.color;ctx.fillStyle=item.color;ctx.lineWidth=1.5;ctx.font='11px system-ui';
      if(previewOnly){ctx.setLineDash([5,4]);if(x!==null){ctx.beginPath();ctx.arc(x,y,4,0,Math.PI*2);ctx.fill();}}
      function line(x1:number,y1:number,x2:number,y2:number){ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke();}
      function box(x1:number,y1:number,x2:number,y2:number,fill:string){
        ctx.fillStyle=fill;ctx.fillRect(Math.min(x1,x2),Math.min(y1,y2),Math.abs(x2-x1),Math.abs(y2-y1));
        ctx.strokeRect(Math.min(x1,x2),Math.min(y1,y2),Math.abs(x2-x1),Math.abs(y2-y1));ctx.fillStyle=item.color;
      }
      let label=item.label;
      if(['horizontal','entry','stop','target'].includes(item.kind)){line(0,y,width,y);label||=tools[item.kind][0];}
      else if(item.kind==='vertical'&&x!==null)line(x,0,x,height);
      else if(item.kind==='text'&&x!==null){ctx.fillText(label,x+4,y-4);label='';}
      else if(x!==null&&xx!==null&&yy!==null){
        if(item.kind==='trend'||item.kind==='smt'||item.kind==='measure')line(x,y,xx,yy);
        else if(item.kind==='ray'){
          if(xx===x)line(x,y,x,yy<y?0:height);
          else{const end=xx>x?width:0;line(x,y,end,y+(yy-y)*(end-x)/(xx-x));}
        }else if(item.kind==='time-range')box(x,0,xx,height,item.color+'1f');
        else if(item.kind==='risk-reward'&&c){
          const xc=timeScale.timeToCoordinate(c.time as UTCTimestamp),yc=this.series.priceToCoordinate(Number(c.price));
          if(xc!==null&&yc!==null){
            const edge=Math.max(x,xx,xc),left=Math.min(x,xx,xc);
            box(left,y,edge,yy,'#e86a752f');box(left,y,edge,yc,'#42d5ad2f');
            const risk=Math.abs(Number(a.price)-Number(b.price));
            label||=risk?`${(Math.abs(Number(c.price)-Number(a.price))/risk).toFixed(2)} R`:'';
          }
        }else if(item.kind==='risk-reward'){box(x,y,xx,yy,'#e86a752f');}
        else box(x,y,xx,yy,item.color+'26');
        if(item.kind==='measure'||item.kind==='price-range'){
          const decimals=Math.max(a.price.split('.')[1]?.length??0,b.price.split('.')[1]?.length??0);
          label||=`${(Number(b.price)-Number(a.price)).toFixed(decimals)} · ${Math.abs(b.time-a.time)}s`;
        }
        if(item.kind==='time-range')label||=`${Math.abs(b.time-a.time)}s`;
      }
      if(label)ctx.fillText(label,Math.max(4,x??4)+4,Math.min(height-2,Math.max(14,y-5)));
      ctx.restore();
    }
  })};}
  private savedRenderer=this.renderer(false);
  private previewRenderer=this.renderer(true);
  private view={renderer:()=>this.savedRenderer};
  // Chart snapshots omit the top layer unless explicitly requested.
  private previewView={renderer:()=>this.previewRenderer,zOrder:()=>'top' as const};
  paneViews(){return this.preview?.anchors.length?[this.view,this.previewView]:[this.view];}
}
