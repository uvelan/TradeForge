import {useCallback,useEffect,useLayoutEffect,useMemo,useRef,useState} from 'react';
import type {IChartApi,IPrimitivePaneRenderer,PrimitiveHoveredItem,ISeriesApi,ISeriesPrimitive,Time,UTCTimestamp} from 'lightweight-charts';
import {useWorkspacePreference} from './useWorkspacePreference';
import {api,ApiError} from './api';
import {bodyDistance,handlesFor,sameAnchors,type DrawingHandle} from './drawingGeometry';
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
type ChartInteraction=
  | {mode:'navigate';notice?:string}
  | {mode:'level'}
  | {mode:'plan';prices:string[]}
  | {mode:'place'|'edit';draft:Edit};
const now=()=>Math.floor(Date.now()/1000);

function bracketsEntry(anchors:Anchor[]){
  const [entry,stop,target]=anchors.map(point=>Number(point.price));
  return (stop<entry&&entry<target)||(target<entry&&entry<stop);
}

function useDrawingDocument(scope:string) {
  const endpoint='/drawings?'+new URLSearchParams({scope});
  const [loaded,setLoaded]=useState<{scope:string;version:Version}>();
  const [error,setError]=useState('');
  const [refresh,setRefresh]=useState(0);
  const [saving,setSaving]=useState(false);
  const [conflict,setConflict]=useState(false);
  const current=loaded?.scope===scope?loaded.version:undefined;
  const active=useRef<symbol|undefined>(undefined);
  const writing=useRef(false);
  useEffect(()=>{
    if(!scope)return;
    const key=Symbol(scope);
    active.current=key;
    setLoaded(undefined);setError('');setSaving(false);setConflict(false);writing.current=false;
    api.get<Version>(endpoint).then(version=>{if(active.current===key)setLoaded({scope,version});})
      .catch(reason=>{if(active.current===key)setError(reason.message);});
    return ()=>{if(active.current===key)active.current=undefined;};
  },[scope,endpoint,refresh]);
  async function write(items:Drawing[]){
    if(!current||writing.current||conflict)return;
    const key=active.current;
    writing.current=true;
    setSaving(true);setError('');
    try{
      const version=await api.post<Version>(endpoint,{...current,items});
      if(active.current!==key)return;
      setLoaded({scope,version});
      return version;
    }catch(reason){
      if(active.current===key){
        setError((reason as Error).message);
        if(reason instanceof ApiError&&reason.status===409)setConflict(true);
      }
    }
    finally{if(active.current===key){writing.current=false;setSaving(false);}}
  }
  return {scope,current,error,setError,saving,conflict,write,writing,refresh,reload:()=>setRefresh(value=>value+1)};
}

type History={undo:Drawing[][];redo:Drawing[][]};
const emptyHistory:History={undo:[],redo:[]};

type DrawingPane={scope:string;step:number;candles?:Candle[]};
const noDrawings:Drawing[]=[];

function useDrawingBook(document:ReturnType<typeof useDrawingDocument>,snap:boolean,setSnap:(value:boolean)=>void,
  pane:DrawingPane,cutoff?:number) {
  const {scope,current,setError,writing,conflict,refresh}=document;
  const releases=useMemo(()=>pane.candles&&new Map(pane.candles.map(bar=>
    [bar.time,bar.available_at??bar.time+pane.step])),[pane.candles,pane.step]);
  const eligible=useCallback((anchors:Anchor[])=>cutoff===undefined||anchors.every(({time})=>
    (releases?(releases.get(time)??Infinity):time+pane.step)<=cutoff),[cutoff,releases,pane.step]);
  const items=useMemo(()=>cutoff===undefined?(current?.items??noDrawings):
    current?.items.filter(item=>eligible(item.anchors))??noDrawings,[current,cutoff,eligible]);
  const [selectedId,setSelected]=useState('');
  const selected=items.some(item=>item.id===selectedId)?selectedId:'';
  useLayoutEffect(()=>{if(selectedId&&!selected)setSelected('');},[selectedId,selected]);
  const [storedInteraction,setInteraction]=useState<ChartInteraction>({mode:'navigate'});
  const draft='draft' in storedInteraction?storedInteraction.draft:undefined;
  const original=draft?.item;
  const latest=original&&current?.items.find(item=>item.id===original.id);
  // A book write decodes fresh objects even when it only changes a different drawing.
  const superseded=Boolean(original&&(!latest||latest.locked||
    (latest!==original&&JSON.stringify(latest)!==JSON.stringify(original))));
  const canceled=draft&&(superseded||!eligible(draft.anchors)||(original&&!eligible(original.anchors)));
  const interaction:ChartInteraction=canceled?{mode:'navigate'}:storedInteraction;
  const edit=canceled?undefined:draft;
  useLayoutEffect(()=>{
    if(canceled)setInteraction({mode:'navigate',notice:superseded?'Drawing edit canceled because the saved drawing changed.':undefined});
  },[canceled,superseded]);
  const setEdit=useCallback((draft?:Edit)=>setInteraction(
    draft?{mode:draft.item?'edit':'place',draft}:{mode:'navigate'},
  ),[]);
  const [historyAt,setHistory]=useState<{revision:number;entries:History}>();
  const history=current&&historyAt?.revision===current.revision?historyAt.entries:emptyHistory;
  const owner=useRef<symbol|undefined>(undefined);
  useLayoutEffect(()=>{
    owner.current=Symbol(scope);
    setEdit(undefined);setSelected('');setHistory(undefined);
    return ()=>{owner.current=undefined;};
  },[scope,refresh,setEdit]);
  // History contains whole revision snapshots. Replaying one is safe only if its
  // unavailable items are identical to the current persisted document.
  function preservesHidden(next:Drawing[]){
    if(cutoff===undefined)return true;
    const hidden=(list:Drawing[])=>list.filter(item=>!eligible(item.anchors));
    return JSON.stringify(hidden(next))===JSON.stringify(hidden(current?.items??[]));
  }
  const undo=history.undo.at(-1),redo=history.redo.at(-1);
  async function save(items:Drawing[],mode:'edit'|'undo'|'redo'='edit',selectAfter?:string){
    if(!current||!preservesHidden(items))return;
    const key=owner.current;
    const version=await document.write(items);
    if(!version||key!==owner.current)return;
    if(selectAfter&&version.items.some(item=>item.id===selectAfter))setSelected(selectAfter);
    const next=mode==='undo'?{undo:history.undo.slice(0,-1),redo:[...history.redo,current.items]}:
      mode==='redo'?{undo:[...history.undo,current.items],redo:history.redo.slice(0,-1)}:
      {undo:[...history.undo,current.items].slice(-25),redo:[]};
    setHistory({revision:version.revision,entries:next});
  }
  function begin(kind:DrawingTool){
    if(current&&current.items.length<100&&!writing.current&&!conflict)setEdit({kind,anchors:[]});
  }
  function point(bar:Candle,price:number,digits:number):Anchor {
    const nearest=snap?(Math.abs(price-bar.high)<Math.abs(price-bar.low)?bar.high:bar.low):price;
    return {time:bar.time,price:nearest.toFixed(Math.min(digits,10))};
  }
  function anchor(additions:Anchor[],digits:number) {
    if(!edit||!current||writing.current||!additions.length||additions.some(point=>Number(point.price)<0))return;
    const next=additions[additions.length-1];
    const nearest=Number(next.price);
    if(edit.item&&edit.mode!==undefined){
      if(!latest||latest.locked||superseded)return;
      const item=latest;
      const anchors=item.anchors.map((point,index)=>{
        if(edit.mode===index)return next;
        if(edit.mode==='move'){
          return {time:point.time+next.time-item.anchors[0].time,
            price:(Number(point.price)+nearest-Number(item.anchors[0].price)).toFixed(Math.min(digits,10))};
        }
        return point;
      });
      if(anchors.some(point=>point.time<0||Number(point.price)<0)){setError('Move would leave the supported time or price range');return;}
      if(edit.mode==='move'&&!eligible(anchors)){
        setError('A moved anchor falls outside the available replay candles. Choose another origin.');
        return;
      }
      if(item.kind==='risk-reward'&&!bracketsEntry(anchors)){
        setError('Stop and target must bracket entry. Select the anchor again.');
        return;
      }
      setEdit(undefined);void save(current.items.map(drawing=>drawing.id===item.id?{...item,anchors,updated_at:now()}:drawing));
      return;
    }
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
    setEdit(undefined);
    void save([...current.items,item],'edit',item.id);
  }
  function saveDirect(original:Drawing,anchors:Anchor[]){
    if(!current||conflict||writing.current||interaction.mode!=='navigate'||selected!==original.id||
      original.locked||!original.visible||!current.items.includes(original)||sameAnchors(anchors,original.anchors))return;
    void save(current.items.map(item=>item===original?{...item,anchors,updated_at:now()}:item));
  }
  return {current,items,eligible,canUndo:Boolean(undo&&preservesHidden(undo)),canRedo:Boolean(redo&&preservesHidden(redo)),error:document.error,saving:document.saving,conflict,saveDirect,selected,setSelected,interaction,setInteraction,edit,setEdit,snap,setSnap,history,save,begin,anchor,point,reload:document.reload};
}

export type DrawingBook=ReturnType<typeof useDrawingBook>;

export function usePaneDrawingBooks(cutoff:number|undefined,primary:DrawingPane,second:DrawingPane,third:DrawingPane,fourth:DrawingPane) {
  const scopes=[primary.scope,second.scope,third.scope,fourth.scope];
  // Identical chart markets share one document and write lock. Gestures, selection and
  // undo belong to an individual chart and cannot follow a delayed response into another.
  const first=useDrawingDocument(primary.scope);
  const two=useDrawingDocument(scopes.indexOf(second.scope)===1?second.scope:'');
  const three=useDrawingDocument(scopes.indexOf(third.scope)===2?third.scope:'');
  const four=useDrawingDocument(scopes.indexOf(fourth.scope)===3?fourth.scope:'');
  const documents=[first,two,three,four];
  const [snapValue,saveSnap]=useWorkspacePreference('drawing-snap','off',['on','off']);
  const snap=snapValue==='on';
  const setSnap=(value:boolean)=>saveSnap(value?'on':'off');
  const primaryBook=useDrawingBook(first,snap,setSnap,primary,cutoff);
  const secondBook=useDrawingBook(documents[scopes.indexOf(second.scope)],snap,setSnap,second,cutoff);
  const thirdBook=useDrawingBook(documents[scopes.indexOf(third.scope)],snap,setSnap,third,cutoff);
  const fourthBook=useDrawingBook(documents[scopes.indexOf(fourth.scope)],snap,setSnap,fourth,cutoff);
  return {primary:primaryBook,'2':secondBook,'3':thirdBook,'4':fourthBook};
}

export function drawingChartProps(book:DrawingBook,digits:number,active:boolean) {
  const {edit,interaction}=book;
  const draft=active?edit:undefined;
  const drag=interaction.mode==='level'||(interaction.mode==='place'&&tools[interaction.draft.kind][1]===1)?'release-point':
    interaction.mode==='place'&&!interaction.draft.anchors.length&&['trend','rectangle','risk-reward'].includes(interaction.draft.kind)?'two-points':'none';
  return {
    drawMode:active&&interaction.mode!=='navigate',placementInteraction:interaction,
    placementDrag:drag as 'release-point'|'two-points'|'none',requirePlacementCandle:interaction.mode==='level',
    placementDraft:draft&&{kind:draft.kind,anchors:draft.anchors},
    onDrawingSelect:(id:string)=>{if(!id||book.items.some(item=>item.visible&&item.id===id))book.setSelected(id);},
    directEdit:{selected:active?book.selected:'',enabled:active&&Boolean(book.current&&!book.saving&&!book.conflict),
      revision:book.current?.revision,snap:book.snap,save:book.saveDirect},
    resolvePlacementAnchor:draft?(bar:Candle,price:number)=>book.point(bar,price,digits):undefined,
    onAnchor:draft?(anchors:Anchor[])=>book.anchor(anchors,digits):undefined,
    onPlacementPreview:draft?(anchors:Anchor[])=>({kind:draft.kind,
      anchors:[...draft.anchors,...anchors]}):undefined,
    drawingPrompt:draft?`${tools[draft.kind][0]} · select ${draft.mode==='move'?'new origin':typeof draft.mode==='number'?draft.mode+1:draft.anchors.length+1}`:undefined,
  };
}

export function DrawingToolbox({book,destination}: {book:DrawingBook;destination?:string}) {
  const [tool,setTool]=useWorkspacePreference<DrawingTool>('drawing-tool','trend',Object.keys(tools) as DrawingTool[]);
  const [expanded,setExpanded]=useWorkspacePreference("drawing-toolbox","closed",["open","closed"]);
  const item=book.items.find(drawing=>drawing.id===book.selected);
  const [draft,setDraft]=useState<{id:string;label:string;color:string}>();
  const options=draft?.id===item?.id?draft:undefined;
  const blocked=book.saving||book.conflict||!book.current;
  const update=(changes:Partial<Drawing>)=>{
    if(item&&book.current)void book.save(book.current.items.map(drawing=>drawing.id===item.id?
      {...drawing,...changes,updated_at:now()}:drawing));
  };
  return <details className="data-trust" open={expanded==="open"} onToggle={e=>setExpanded(e.currentTarget.open?"open":"closed")}><summary>Drawing toolbox {book.current?`(${book.items.length})`:''}{destination&&<small className="muted"> · {destination}</small>}</summary>
    {book.error&&<p role="alert">{book.error} <button className="small-tool" disabled={book.saving} onClick={book.reload}>Reload drawings</button></p>}
    {book.interaction.mode==='navigate'&&book.interaction.notice&&<p role="status">{book.interaction.notice}</p>}
    {!book.current&&!book.error&&<p role="status">Loading drawings</p>}
    <div className="research-controls">
      <label>Drawing tool<select aria-label="Drawing tool" value={tool} onChange={event=>setTool(event.target.value as DrawingTool)}>
        {Object.entries(tools).map(([value,[label]])=><option key={value} value={value}>{label}</option>)}
      </select></label>
      <button className="small-tool" disabled={blocked||book.current?.items.length===100} onClick={()=>book.begin(tool)}>Draw</button>
      <label><input type="checkbox" checked={book.snap} onChange={event=>book.setSnap(event.target.checked)}/> Snap to wick</label>
      {book.edit&&<button className="small-tool" onClick={()=>book.setEdit(undefined)}>Cancel drawing</button>}
      <button className="small-tool" disabled={blocked||!book.canUndo}
        onClick={()=>void book.save(book.history.undo.at(-1)!,'undo')}>Undo drawing edit</button>
      <button className="small-tool" disabled={blocked||!book.canRedo}
        onClick={()=>void book.save(book.history.redo.at(-1)!,'redo')}>Redo drawing edit</button>
      <button className="small-tool" disabled={blocked||!book.items.some(drawing=>!drawing.locked)} onClick={()=>{
        if(book.current&&window.confirm('Remove all available unlocked drawings?'))void book.save(book.current.items.filter(drawing=>drawing.locked||!book.eligible(drawing.anchors)));
      }}>Clear drawings</button>
    </div>
    {Boolean(book.items.length)&&<label className="field">Select drawing<select value={book.selected}
      onChange={event=>{book.setSelected(event.target.value);setDraft(undefined);}}>
      <option value="">Choose drawing</option>{book.items.map(drawing=>
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
        void book.save([...book.current.items,copy],'edit',copy.id);
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
  selected='';
  editPreview?:Drawing;
  select(id:string){this.selected=id;this.repaint?.();}
  showEdit(item?:Drawing){this.editPreview=item;this.repaint?.();}

  preview?:{kind:Drawing['kind'];anchors:Anchor[]};
  showPreview(preview?:DrawingLayer['preview']){this.preview=preview;this.repaint?.();}
  constructor(private chart:IChartApi,private series:ISeriesApi<'Candlestick'>){}
  attached({requestUpdate}:{requestUpdate:()=>void}){this.repaint=requestUpdate;}
  detached(){this.repaint=undefined;}
  update(items:Drawing[]){this.items=items;this.repaint?.();}
  private geometry(item:Drawing){
    if(!item.visible||!['horizontal','trend','rectangle','risk-reward'].includes(item.kind))return;
    const scale=this.chart.timeScale();
    const points=item.anchors.flatMap(anchor=>{
      const y=this.series.priceToCoordinate(Number(anchor.price));
      const projected=scale.timeToCoordinate(anchor.time as UTCTimestamp);
      const x=item.kind==='horizontal'?scale.width()*.5:projected;
      return x===null||y===null?[]:[{x,y}];
    });
    if(points.length===item.anchors.length)return points;
  }
  target(x:number,y:number):{item:Drawing;distance:number;handle?:DrawingHandle}|undefined{
    const selected=this.items.find(item=>item.id===this.selected);
    const projection=selected&&this.geometry(selected);
    if(selected&&projection){
      for(const handle of handlesFor(selected.kind,projection)){
        const distance=Math.hypot(handle.x-x,handle.y-y);
        if(distance<=7)return {item:selected,distance,handle:handle.anchor};
      }
    }
    let hit:{item:Drawing;distance:number}|undefined;
    for(const item of this.items){
      const points=this.geometry(item);
      if(!points)continue;
      const distance=bodyDistance(item.kind,{x,y},points);
      if(distance<=6&&distance<=(hit?.distance??Infinity))hit={item,distance};
    }
    return hit;
  }
  hitTest(x:number,y:number):PrimitiveHoveredItem|null{
    const hit=this.target(x,y);
    if(!hit)return null;
    return {distance:hit.distance,externalId:'drawing:'+hit.item.id,zOrder:'normal',
      hitTestPriority:hit.handle?2:1,cursorStyle:hit.item.locked?'default':hit.handle?'crosshair':
        hit.item.id===this.selected?'move':'pointer'};
  }
  private renderer(previewOnly:boolean):IPrimitivePaneRenderer{return {draw:target=>target.useMediaCoordinateSpace(({context:ctx,mediaSize})=>{
    const {width,height}=mediaSize;
    const timeScale=this.chart.timeScale();
    const preview=this.preview;
    const items=previewOnly?(this.editPreview?[{...this.editPreview,color:'#ffdc86'}]:
      preview?.anchors.length?[{...preview,visible:true,label:'',color:'#ffdc86'}]:[]):this.items;
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
    if(previewOnly){
      const selected=this.editPreview??this.items.find(item=>item.id===this.selected);
      const points=selected&&this.geometry(selected);
      if(selected&&points){
        ctx.save();
        ctx.lineWidth=1.5;ctx.strokeStyle=selected.locked?'#9caab9':'#fff0b8';ctx.fillStyle='#183545';
        for(const handle of handlesFor(selected.kind,points)){
          ctx.beginPath();ctx.arc(handle.x,handle.y,5,0,Math.PI*2);ctx.fill();ctx.stroke();
        }
        ctx.restore();
      }
    }
  })};}
  private savedRenderer=this.renderer(false);
  private previewRenderer=this.renderer(true);
  private view={renderer:()=>this.savedRenderer};
  // Chart snapshots omit the top layer unless explicitly requested.
  private previewView={renderer:()=>this.previewRenderer,zOrder:()=>'top' as const};
  paneViews(){return this.preview?.anchors.length||this.selected||this.editPreview?[this.view,this.previewView]:[this.view];}
}
