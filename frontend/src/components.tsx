import {useEffect,useLayoutEffect,useMemo,useRef,useState} from 'react';
import {CandlestickSeries,LineSeries,createChart,createSeriesMarkers,ColorType,CrosshairMode,LineStyle,LineType, type IChartApi, type ISeriesApi, type IPriceLine, type UTCTimestamp} from 'lightweight-charts';
import type { IndicatorResult } from './indicators';
import type { SmtEvent } from './smt';
import {useCandleColors} from './candleColors';
import {chartTimeOptions,useTimezone} from './timezone';
import {ArrowDownRight,ArrowUpRight,Camera,Check,ChevronDown,LoaderCircle,Maximize,Minimize,SkipForward,TriangleAlert} from 'lucide-react';
import type {Candle} from './types';
import type {ChartLink} from './ChartLink';
import {DrawingLayer,type Anchor,type Drawing} from './Drawings';
import {installPlacement,type DrawPoint,type PlacementDrag} from './drawingPlacement';
import {sameAnchors,transformDrawing} from './drawingGeometry';

export function Badge({children,tone='neutral'}:{children:React.ReactNode;tone?:'neutral'|'green'|'red'|'amber'|'blue'}) {return <span className={`badge badge-${tone}`}>{children}</span>}
export function SectionTitle({eyebrow,title,action}:{eyebrow?:string;title:string;action?:React.ReactNode}){return <div className="section-title"><div>{eyebrow&&<span className="eyebrow">{eyebrow}</span>}<h2>{title}</h2></div>{action}</div>}
export function Card({children,className=''}:{children:React.ReactNode;className?:string}){return <div className={'card '+className}>{children}</div>}
export function Metric({title,value,change,icon}:{title:string;value:string;change?:string;icon?:React.ReactNode}) {return <div className="metric card"><div className="metric-top"><span>{title}</span><div className="metric-icon">{icon}</div></div><div className="metric-number">{value}</div><div className="metric-bottom">{change?<span className="subtle">{change}</span>:<span className="muted">Real records only · no estimates</span>}</div></div>}
export function Empty({icon,title,body}:{icon?:React.ReactNode;title:string;body:string}){return <div className="empty-state">{icon&&<div className="empty-icon">{icon}</div>}<strong>{title}</strong><p>{body}</p></div>}
export function Spinner(){return <LoaderCircle className="spinning" size={19}/>}
export function Notice({children,tone='amber'}:{children:React.ReactNode;tone?:'amber'|'blue'|'red'}){return <div className={`notice notice-${tone}`}><TriangleAlert size={17}/><span>{children}</span></div>}
export function Select({value,onChange,options,label}:{value:string;onChange:(s:string)=>void;options:string[];label?:string}){return <div className="select-wrap"><select aria-label={label} value={value} onChange={e=>onChange(e.target.value)}>{options.map(o=><option key={o} value={o}>{o === "DEMO" ? "DEMO (synthetic)" : o === "BINANCE_SPOT" ? "Binance Spot" : o}</option>)}</select><ChevronDown size={15}/></div>}
export function PriceChange({value}:{value:number}){return <span className={value>=0?'positive':'negative'}>{value>=0?<ArrowUpRight size={14}/>:<ArrowDownRight size={14}/>} {value>=0?'+':''}{value.toFixed(2)}%</span>}

export type ChartLevel = {id:string; price:number};
export type ComparisonPlacement = 'Overlay' | 'New pane';

export function CandleChart({candles,height=340,levels=[],drawMode=false,onAddLevel,showSnapshot=false,drawingPrompt,symbol='EURUSD',precision=5,indicators,comparison,timeframe,focus,link,linkId,drawings,onAnchor,placementDraft,placementInteraction,placementDrag,requirePlacementCandle,onPlacementPreview,onDrawingSelect,directEdit,onSmtSelect,onSaveSnapshot}:{
  candles:Candle[];height?:number;levels?:ChartLevel[];drawMode?:boolean;
  onAddLevel?:(price:number)=>void;showSnapshot?:boolean;symbol?:string;precision?:number;indicators?:IndicatorResult[]
  timeframe?: string;
  drawingPrompt?: string;
  focus?: {epoch:number;sequence:number};
  link?:ChartLink; linkId?:string;
  drawings?:Drawing[]; onAnchor?:(points:{bar:Candle;price:number}[])=>void;
  placementDraft?:DrawingLayer['preview']; placementInteraction?:object;
  placementDrag?:PlacementDrag; requirePlacementCandle?:boolean;
  onPlacementPreview?:(points:{bar:Candle;price:number}[])=>DrawingLayer['preview']; onDrawingSelect?:(id:string)=>void;
  directEdit?:{selected:string;enabled:boolean;revision?:number;snap:boolean;save:(item:Drawing,anchors:Anchor[])=>void}; onSmtSelect?:(event:SmtEvent)=>void; onSaveSnapshot?:(png:string)=>Promise<void>;
  comparison?: {symbol:string; candles:Candle[]; precision:number; placement:ComparisonPlacement;
    onPlacementChange:(placement:ComparisonPlacement)=>void; fixedPlacement?:boolean; smt?:SmtEvent[]}
}){
  const element=useRef<HTMLDivElement>(null);
  const pointerGuide=useRef<HTMLDivElement>(null);
  const surface=useRef<HTMLDivElement>(null);
  const [fullscreen,setFullscreen]=useState(false);
  const [fullscreenError,setFullscreenError]=useState("");
  const [snapshotStatus,setSnapshotStatus]=useState("");
  const [savingSnapshot,setSavingSnapshot]=useState(false);
  const [cursorTime,setCursorTime]=useState<number>();
  const [missingLinkedTime,setMissingLinkedTime]=useState<number>();
  const linkedBars=useRef(new Map<number,Candle>());
  useEffect(()=>{
    const update=()=>setFullscreen(document.fullscreenElement===surface.current);
    document.addEventListener("fullscreenchange",update);
    return ()=>document.removeEventListener("fullscreenchange",update);
  },[]);
  async function toggleFullscreen(){
    const target=surface.current;
    if(!target)return;
    setFullscreenError("");
    try{
      if(document.fullscreenElement===target) await document.exitFullscreen();
      else await target.requestFullscreen();
    }catch{
      setFullscreenError("Fullscreen is unavailable in this browser window.");
    }
  }
  const {zone}=useTimezone();
  const {up,down,comparisonUp,comparisonDown}=useCandleColors();
  const chartRef=useRef<IChartApi|null>(null);
  const seriesRef=useRef<ISeriesApi<'Candlestick'>|null>(null);
  const comparisonRef=useRef<ISeriesApi<'Candlestick'>|null>(null);
  const linesRef=useRef<IPriceLine[]>([]);
  const didFit=useRef(false);
  const clickHandler=useRef(onAddLevel);
  const anchorHandler=useRef(onAnchor);
  const previewHandler=useRef(onPlacementPreview);
  previewHandler.current=onPlacementPreview;
  const draft=useRef(placementDraft);
  draft.current=placementDraft;
  const drag=useRef(placementDrag);
  drag.current=placementDrag;
  const needsCandle=useRef(requirePlacementCandle);
  needsCandle.current=requirePlacementCandle;
  const placementReset=useRef<(()=>void)|undefined>(undefined);
  const editReset=useRef<(()=>void)|undefined>(undefined);
  const editing=useRef(directEdit);
  editing.current=directEdit;
  const drawingSelection=useRef(onDrawingSelect);
  drawingSelection.current=onDrawingSelect;
  const markerSelection=useRef(onSmtSelect);
  markerSelection.current=onSmtSelect;
  const markerEvents=useRef<SmtEvent[]>([]);
  markerEvents.current=comparison?.smt??[];
  anchorHandler.current=onAnchor;
  const paintLayer=useRef<DrawingLayer|null>(null);
  const drawing=useRef(drawMode);
  clickHandler.current=onAddLevel;
  drawing.current=drawMode;
  function showRecentCandles(rows: Candle[]){
    const scale=chartRef.current?.timeScale();
    if(!scale||!rows.length)return;
    const count=Math.max(12,Math.min(65,Math.floor(scale.width()/11)));
    const start=scale.timeToIndex(rows[Math.max(0,rows.length-count)].time as UTCTimestamp);
    const end=scale.timeToIndex(rows[rows.length-1].time as UTCTimestamp);
    if(start!==null&&end!==null)scale.setVisibleLogicalRange({from:start-0.5,to:end+3});
  }
  useEffect(()=>{
    if(!element.current)return;
    const chart=createChart(element.current,{autoSize:true,
      layout:{background:{type:ColorType.Solid,color:'#0c131e'},textColor:'#8395ad',fontFamily:'Inter, system-ui, sans-serif',fontSize:11,attributionLogo:true},
      grid:{vertLines:{color:'#182434'},horzLines:{color:'#182434'}},
      crosshair:{mode:CrosshairMode.Normal,vertLine:{visible:false,labelBackgroundColor:'#27384a'},
        horzLine:{color:'#5a7594',labelBackgroundColor:'#27384a'}},
      rightPriceScale:{borderColor:'#263343',scaleMargins:{top:.10,bottom:.12}},
      timeScale:{borderColor:'#263343',timeVisible:true,secondsVisible:false,rightOffset:3,lockVisibleTimeRangeOnResize:true},
      handleScale:{mouseWheel:true,pinch:true,axisPressedMouseMove:true},
      handleScroll:{mouseWheel:true,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:true}});
    chartRef.current=chart;
    const series=chart.addSeries(CandlestickSeries,{upColor:'#2cd8ae',downColor:'#ea6a80',borderVisible:false,
      wickUpColor:'#43dab5',wickDownColor:'#e87d90',priceFormat:{type:'price',precision,minMove:10**-precision}});
    seriesRef.current=series;
    const toolLayer=new DrawingLayer(chart,series);
    paintLayer.current=toolLayer;
    series.attachPrimitive(toolLayer);
    if(drawing.current)toolLayer.showPreview(draft.current);
    else toolLayer.select(editing.current?.selected??'');
    const release=link&&linkId?link.register(linkId,{chart,series,bars:()=>linkedBars.current,missing:setMissingLinkedTime}):undefined;
    const scale=chart.timeScale();
    const viewport=()=>{editReset.current?.();if(linkId)link?.viewport(linkId);};
    const activate=()=>{if(linkId)link?.activate(linkId);setMissingLinkedTime(undefined);};
    const leave=()=>{if(linkId)link?.clearCursor(linkId);};
    const container=element.current;
    function located(event:PointerEvent):DrawPoint|undefined{
      const bounds=container.getBoundingClientRect();
      const x=event.clientX-bounds.left-chart.priceScale('left').width();
      const y=event.clientY-bounds.top;
      if(x<0||x>=scale.width()||y<0||y>=chart.paneSize(0).height)return;
      const time=scale.coordinateToTime(x);
      const bar=typeof time==='number'?linkedBars.current.get(time):undefined;
      const price=series.coordinateToPrice(y);
      if(price===null||!Number.isFinite(price)||price<0||((anchorHandler.current||needsCandle.current)&&!bar))return;
      return {x,y,price,time:bar?.time};
    }
    function candlePoints(points:DrawPoint[]){
      return points.flatMap(point=>{
        const bar=point.time===undefined?undefined:linkedBars.current.get(point.time);
        return bar?[{bar,price:point.price}]:[];
      });
    }
    const input=installPlacement(container,{
      armed:()=>drawing.current,
      drag:()=>drag.current??'none',
      activate,
      locate:located,
      select:point=>drawingSelection.current?.(toolLayer.target(point.x,point.y)?.item.id??''),
      startEdit:start=>{
        const config=editing.current;
        if(!config?.enabled||!config.selected)return;
        const hit=toolLayer.target(start.x,start.y);
        if(!hit||hit.item.id!==config.selected||hit.item.locked)return;
        const {item,handle}=hit;
        // Use the same wick preference as placement, and require primary candle times.
        function proposal(end?:DrawPoint){
          if(!end)return;
          const bar=end.time===undefined?undefined:linkedBars.current.get(end.time);
          const price=config!.snap&&bar?
            (Math.abs(end.price-bar.high)<Math.abs(end.price-bar.low)?bar.high:bar.low):end.price;
          const anchors=transformDrawing(item,handle,start,{...end,price},precision,
            time=>linkedBars.current.has(time));
          return anchors&&!sameAnchors(item.anchors,anchors)?anchors:undefined;
        }
        return {
          update:end=>{
            const anchors=proposal(end);
            toolLayer.showEdit(anchors?{...item,anchors}:undefined);
          },
          commit:end=>{
            const anchors=proposal(end);
            if(anchors)editing.current?.save(item,anchors);
          },
          cancel:()=>toolLayer.showEdit(),
        };
      },
      preview:points=>toolLayer.showPreview(previewHandler.current?.(candlePoints(points))??draft.current),
      commit:points=>{
        if(anchorHandler.current)anchorHandler.current(candlePoints(points));
        else clickHandler.current?.(points[0].price);
      },
    });
    placementReset.current=input.reset;
    editReset.current=input.cancelEdit;
    const cancelOnEscape=(event:KeyboardEvent)=>{if(event.key==='Escape')input.cancelEdit();};
    const size=()=>[container.clientWidth,container.clientHeight];
    let dimensions=size();
    const resize=new ResizeObserver(()=>{
      const next=size();
      if(next[0]!==dimensions[0]||next[1]!==dimensions[1])input.cancelEdit();
      dimensions=next;
    });
    resize.observe(container);
    document.addEventListener('fullscreenchange',input.cancelEdit);
    window.addEventListener('keydown',cancelOnEscape);
    container.addEventListener('pointerenter',activate);
    container.addEventListener('wheel',activate,{passive:true});
    container.addEventListener('pointerleave',leave);
    scale.subscribeVisibleLogicalRangeChange(viewport);
    chart.subscribeCrosshairMove(param=>{
      const time=typeof param.time==='number'?param.time:undefined;
      setCursorTime(time);
      if(linkId)link?.cursor(linkId,time);
    });
    chart.subscribeClick(param=>{
      if(drawing.current)return;
      if(typeof param.hoveredObjectId==='string'&&param.hoveredObjectId.startsWith('smt:')){
        const event=markerEvents.current.find(item=>item.id===param.hoveredObjectId?.toString().slice(4));
        if(event?.event_key)markerSelection.current?.(event);
        return;
      }
    });
    return ()=>{
      container.removeEventListener('pointerenter',activate);
      input.dispose();placementReset.current=undefined;editReset.current=undefined;
      resize.disconnect();
      document.removeEventListener('fullscreenchange',input.cancelEdit);
      window.removeEventListener('keydown',cancelOnEscape);
      container.removeEventListener('wheel',activate);
      container.removeEventListener('pointerleave',leave);
      scale.unsubscribeVisibleLogicalRangeChange(viewport);
      release?.();
      paintLayer.current=null;
      linesRef.current=[];seriesRef.current=null;chartRef.current=null;didFit.current=false;chart.remove();
    };
  },[symbol,precision,link,linkId]);
  useLayoutEffect(()=>{placementReset.current?.();paintLayer.current?.showPreview(drawMode?placementDraft:undefined);},[drawMode,placementDraft,placementInteraction,symbol,precision]);
  useLayoutEffect(()=>{
    editReset.current?.();
    paintLayer.current?.select(drawMode?'':directEdit?.selected??'');
  },[drawMode,directEdit?.selected,directEdit?.enabled,directEdit?.revision,directEdit?.snap,
    comparison?.symbol,comparison?.placement,height,candles,symbol,precision]);
  useEffect(()=>{paintLayer.current?.update(drawings??[]);},[drawings,symbol,precision]);
  const comparisonSymbol=comparison?.symbol;
  const comparisonPrecision=comparison?.precision;
  const placement=comparison?.placement;
  useEffect(()=>{
    const chart=chartRef.current;
    if(!chart||!comparisonSymbol||comparisonPrecision===undefined)return;
    const overlay=placement==='Overlay';
    const pane=overlay?0:chart.addPane().paneIndex();
    if(overlay)chart.applyOptions({leftPriceScale:{visible:true,borderColor:'#263343'}});
    const series=chart.addSeries(CandlestickSeries,{
      priceScaleId:overlay?'left':'right',title:comparisonSymbol,priceLineVisible:false,
      upColor:'rgba(116,162,255,0.26)',downColor:'rgba(195,147,246,0.26)',
      borderUpColor:'#74a2ff',borderDownColor:'#c393f6',
      wickUpColor:'#74a2ff',wickDownColor:'#c393f6',
      priceFormat:{type:'price',precision:comparisonPrecision,minMove:10**-comparisonPrecision},
    },pane);
    comparisonRef.current=series;
    return ()=>{
      comparisonRef.current=null;
      if(chartRef.current===chart){
        chart.removeSeries(series);
        if(overlay)chart.applyOptions({leftPriceScale:{visible:false}});
      }
    };
  },[comparisonSymbol,comparisonPrecision,placement,symbol,precision]);
  useEffect(()=>{
    seriesRef.current?.applyOptions({upColor:up,downColor:down,wickUpColor:up,wickDownColor:down});
    comparisonRef.current?.applyOptions({
      upColor:`${comparisonUp}42`,downColor:`${comparisonDown}42`,
      borderUpColor:comparisonUp,borderDownColor:comparisonDown,
      wickUpColor:comparisonUp,wickDownColor:comparisonDown,
    });
  },[up,down,comparisonUp,comparisonDown,comparisonSymbol,comparisonPrecision,placement,symbol,precision]);
  useEffect(()=>{
    comparisonRef.current?.setData((comparison?.candles ?? []).map(bar=>({...bar,time:bar.time as UTCTimestamp})));
  },[comparison,placement,symbol,precision]);
  useEffect(()=>{
    const series=seriesRef.current;
    if(!series)return;
    const sorted=[...candles].sort((a,b)=>a.time-b.time).filter((c,i,arr)=>i===0||arr[i-1].time!==c.time);
    linkedBars.current=new Map(sorted.map(bar=>[bar.time,bar]));
    series.setData(sorted.map(c=>({time:c.time as UTCTimestamp,open:c.open,high:c.high,low:c.low,close:c.close})));
    if(!didFit.current&&sorted.length){showRecentCandles(sorted);didFit.current=true}
  },[candles,symbol,precision]);
  useEffect(()=>{
    const series=seriesRef.current;
    if(!series)return;
    linesRef.current.forEach(line=>series.removePriceLine(line));
    linesRef.current=levels.map(level=>series.createPriceLine({price:level.price,color:'#edbd73',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'LEVEL'}));
  },[levels,symbol,precision]);
  useEffect(()=>{
    const format=chartTimeOptions(zone);
    chartRef.current?.applyOptions({timeScale:{tickMarkFormatter:format.tick},localization:{timeFormatter:format.crosshair}});
  },[zone,symbol,precision]);
  useEffect(()=>{
    if(!focus)return;
    const chart=chartRef.current;
    const series=seriesRef.current;
    const bar=candles.find(item=>item.time===focus.epoch);
    if(!chart||!series)return;
    const scale=chart.timeScale();
    const index=scale.timeToIndex(focus.epoch as UTCTimestamp);
    if(index===null)return;
    scale.setVisibleLogicalRange({from:index-12,to:index+12});
    if(bar)chart.setCrosshairPosition(bar.close,bar.time as UTCTimestamp,series);
  },[focus,candles]);
  const drawnIndicators=useRef<{chart:IChartApi; lines:Map<string,ISeriesApi<'Line'>>}|undefined>(undefined);
  useEffect(()=>{
    const chart=chartRef.current;
    const series=seriesRef.current;
    if(!chart||!series)return;
    if(drawnIndicators.current?.chart!==chart)drawnIndicators.current={chart,lines:new Map()};
    const {lines}=drawnIndicators.current;
    const displayed=new Set<string>();
    const colors=["#e8bb79","#8ab4ff","#d5a4f8"];
    const markers=[];
    for(const [indicatorIndex,indicator] of (indicators??[]).entries()){
      if(indicator.render==="markers"){
        for(const [title,points] of Object.entries(indicator.series)){
          markers.push(...points.map(point=>({
            time:(point.pivot_time??point.time) as UTCTimestamp,price:point.value,
            position:title==="Swing high"?"atPriceTop" as const:"atPriceBottom" as const,
            shape:"circle" as const,color:"#e8bb79",text:title,
          })));
        }
        continue;
      }
      let pane:number|undefined;
      for(const [index,[title,points]] of Object.entries(indicator.series).entries()){
        const isSession=indicator.id.startsWith("session_");
        const isRange=isSession||indicator.id.startsWith("previous_");
        for(const provisional of isSession?[true,false]:[false]){
          const key=JSON.stringify([indicator.id,title,provisional]);
          const visible=points.filter(point=>(point.status==="provisional")===provisional);
          if(!visible.length)continue;
          displayed.add(key);
          let line=lines.get(key);
          if(!line){
            const prefix=indicator.id.includes("_")?indicator.id.replaceAll("_"," ")+" ":"";
            pane??=indicator.render==="pane"?chart.addPane(false).paneIndex():0;
            const digits=indicator.unit==="price"?precision:2;
            line=chart.addSeries(LineSeries,{
              priceFormat:{type:"price",precision:digits,minMove:10**-digits},
              title:prefix+title+(provisional?" (developing)":""),
              color:colors[(index+indicatorIndex)%colors.length],lineWidth:1,priceLineVisible:false,
              lineType:isRange?LineType.WithSteps:LineType.Simple,
              lineStyle:provisional?LineStyle.Dashed:LineStyle.Solid,
              pointMarkersVisible:isSession&&!provisional,
            },pane);
            lines.set(key,line);
          }else pane??=line.getPane().paneIndex();
          const byTime=new Map(visible.map(point=>[point.time,point.value]));
          line.setData(linkedBars.current.size&&isRange?[...linkedBars.current.keys()].map(time=>{
            const value=byTime.get(time);
            return value===undefined?{time:time as UTCTimestamp}:{time:time as UTCTimestamp,value};
          }):visible.map(point=>({time:point.time as UTCTimestamp,value:point.value})));
        }
      }
    }
    for(const [key,line] of lines){
      if(!displayed.has(key)){
        chart.removeSeries(line);
        lines.delete(key);
      }
    }
    if(markers.length){
      const plugin=createSeriesMarkers(series,markers.sort((a,b)=>a.time-b.time));
      return ()=>{if(chartRef.current===chart)plugin.detach();};
    }
  },[indicators,symbol,precision,link,linkId]);
  const smtEvents=comparison?.smt;
  const markerCandles=useRef(candles);
  markerCandles.current=candles;
  useEffect(()=>{
    const chart=chartRef.current;
    const series=seriesRef.current;
    if(!chart||!series||!smtEvents?.length)return;
    const other=comparisonRef.current;
    if(!other)return;
    const lines=smtEvents.flatMap(event=>{
      const color=event.status==="SMT_INVALIDATED"?"#7b8897":event.direction==="bearish"?"#f7a77d":"#93e3b8";
      return ([["primary",series],["comparison",other]] as const).map(([side,target])=>{
        const line=chart.addSeries(LineSeries,{
          color,lineWidth:2,lineStyle:event.status==="SMT_CONFIRMED"?0:2,
          priceScaleId:target.options().priceScaleId,priceLineVisible:false,lastValueVisible:false,
          crosshairMarkerVisible:false,autoscaleInfoProvider:()=>null,
        },target.getPane().paneIndex());
        const match=event[side];
        line.setData([match.previous,match.current].map(point=>({time:point.time as UTCTimestamp,value:point.price})));
        return line;
      });
    });
    const primaryBars=new Map(markerCandles.current.map(bar=>[bar.time,bar]));
    const markers=smtEvents.map(event=>({
      id:'smt:'+event.id,time:event.primary.current.time as UTCTimestamp,price:event.primary.current.price,
      position:event.direction==="bearish"?"atPriceTop" as const:"atPriceBottom" as const,
      shape:"circle" as const,
      color:event.status==="SMT_INVALIDATED"?"#7b8897":event.direction==="bearish"?"#f7a77d":"#93e3b8",
      size:0.75,
    }));
    const confirmations=smtEvents.flatMap(event=>{
      const bar=event.confirmation_bar_time==null?undefined:primaryBars.get(event.confirmation_bar_time);
      if(!bar)return [];
      return [{
        id:'smt:'+event.id,time:bar.time as UTCTimestamp,price:event.direction==="bearish"?bar.high:bar.low,
        position:event.direction==="bearish"?"atPriceTop" as const:"atPriceBottom" as const,
        shape:event.direction==="bearish"?"arrowDown" as const:"arrowUp" as const,
        color:event.status==="SMT_INVALIDATED"?"#7b8897":event.direction==="bearish"?"#f7a77d":"#93e3b8",
        size:0.75,text:"SMT close",
      }];
    });
    const plugin=createSeriesMarkers(series,[...markers,...confirmations].sort((a,b)=>a.time-b.time));
    return()=>{if(chartRef.current===chart){plugin.detach();lines.forEach(line=>chart.removeSeries(line));}};
  },[smtEvents,symbol,precision,comparisonSymbol,comparisonPrecision,placement]);
  const snapshot=()=>{
    const canvas=chartRef.current?.takeScreenshot();
    if(!canvas)return;
    const anchor=document.createElement('a');
    anchor.href=canvas.toDataURL('image/png');
    anchor.download=`tradeforge-${symbol}-${new Date().toISOString().replace(/[:.]/g,'-')}.png`;
    anchor.click();
  };
  const comparisonBars=useMemo(()=>new Map(comparison?.candles.map(bar=>[bar.time,bar])),[comparison?.candles]);
  const commonBar=comparison?candles.slice().reverse().find(bar=>comparisonBars.has(bar.time)):undefined;
  const displayTime=cursorTime ?? commonBar?.time ?? candles.at(-1)?.time;
  const primaryBar=candles.find(bar=>bar.time===displayTime);
  const comparisonBar=displayTime===undefined?undefined:comparisonBars.get(displayTime);
  const hoveredSmt=smtEvents?.filter(event=>
    event.confirmation_bar_time===cursorTime||
    [event.primary.previous,event.primary.current,event.comparison.previous,event.comparison.current].some(pivot=>pivot.time===cursorTime));
  const timestamp=useMemo(()=>new Intl.DateTimeFormat("en-GB",{
    timeZone:zone,dateStyle:"medium",timeStyle:"short",
  }),[zone]);
  const at=(seconds:number)=>timestamp.format(seconds*1000);
  function legendRow(name:string, bar:Candle|undefined, digits:number, compared=false){
    return <div className={compared?"chart-legend-row compared":"chart-legend-row"}>
      <strong>{name}</strong>
      {bar?<>{(["open","high","low","close"] as const).map(field=><span key={field}>
        <b>{field[0].toUpperCase()}</b> {bar[field].toFixed(digits)}
      </span>)}</>:<span>No candle at this time</span>}
    </div>;
  }
  return <div ref={surface} className={'chart-container chart-surface '+(drawMode?'drawing-enabled':'')} style={{height}}>
    <div ref={element} className="chart-input" style={{height:'100%',width:'100%'}} aria-label="Interactive market candlestick chart"
      onPointerMove={event=>{
        const chart=chartRef.current;
        const guide=pointerGuide.current;
        if(!chart||!guide)return;
        const bounds=event.currentTarget.getBoundingClientRect();
        const x=event.clientX-bounds.left;
        const y=event.clientY-bounds.top;
        const scale=chart.timeScale();
        const left=chart.priceScale('left').width();
        const bottom=scale.height();
        guide.hidden=x<left||x>=left+scale.width()||y<0||y>=bounds.height-bottom;
        guide.style.left=`${x}px`;
        guide.style.bottom=`${bottom}px`;
      }}
      onPointerLeave={()=>{if(pointerGuide.current)pointerGuide.current.hidden=true}}/>
    {missingLinkedTime!==undefined&&link?.options.crosshair&&<span className="linked-no-candle" role="status">No candle at {timestamp.format(missingLinkedTime*1000)}</span>}
    <div ref={pointerGuide} className="chart-pointer-guide" aria-hidden="true" hidden/>
    {drawMode&&<div className="drawing-prompt">{drawingPrompt??'Click the chart to place a horizontal level'}</div>}
    {(fullscreen||comparison)&&<div className="chart-data-legend" aria-label="Chart data">
      <div className="chart-legend-meta">
        {timeframe&&<strong>{timeframe}</strong>}
        {displayTime!==undefined&&<span>{timestamp.format(displayTime*1000)} · {zone}</span>}
        {comparison&&!comparison.fixedPlacement&&<Select label="Comparison placement" value={comparison.placement}
          options={['Overlay','New pane']} onChange={value=>comparison.onPlacementChange(value as ComparisonPlacement)}/>}
      </div>
      {legendRow(symbol,primaryBar,precision)}
      {comparison&&legendRow(comparison.symbol,comparisonBar,comparison.precision,true)}
      {comparison&&smtEvents&&<span className="chart-legend-meta">
        {smtEvents.filter(event=>event.status==="SMT_CONFIRMED").length} confirmed · {smtEvents.filter(event=>event.status==="SMT_CANDIDATE").length} candidates · {smtEvents.filter(event=>event.status==="SMT_INVALIDATED").length} invalidated
      </span>}
      {hoveredSmt?.map(event=><div key={event.id} className="chart-legend-meta">
        {event.status} · {event.direction} · {event.leader==="primary"?symbol:comparisonSymbol} broke its prior {event.direction==="bearish"?"high":"low"}
        <span>Pivot {at(event.primary.current.time)}</span>
        <span>{event.confirmed_at===null?"Awaiting confirmation":`Confirmed ${at(event.confirmed_at)}`}</span>
        <span>{symbol} {event.primary.previous.price.toFixed(precision)} → {event.primary.current.price.toFixed(precision)}</span>
        <span>{comparisonSymbol} {event.comparison.previous.price.toFixed(comparisonPrecision)} → {event.comparison.current.price.toFixed(comparisonPrecision)}</span>
        {event.timing&&<span>Both pairs’ pivots confirmed {at(event.timing.all_pivots_confirmed_at)} · UTC match window closed {at(event.timing.matching_closed_at)} · {event.timing.required_shared_confirmation_bars} additional shared bars required</span>}
        {event.transitions.filter(entry=>entry.status==="SMT_INVALIDATED").map(entry=><span key={entry.status}>Invalidated {at(entry.time)}</span>)}
      </div>)}
    </div>}
    <div className="chart-viewport-actions">
      <button type="button" className="snapshot-btn" aria-label="Show recent candles" title="Show recent candles"
        onClick={()=>showRecentCandles(candles)}><SkipForward size={15}/></button>
      {showSnapshot&&<button type="button" className="snapshot-btn" title="Download chart PNG" onClick={snapshot}><Camera size={15}/> Snapshot</button>}
      {onSaveSnapshot&&<button type="button" className="snapshot-btn" disabled={savingSnapshot} onClick={()=>{
        const canvas=chartRef.current?.takeScreenshot();
        if(!canvas)return;
        const png=canvas.toDataURL('image/png').split(',')[1];
        if(!png)return;
        setSavingSnapshot(true);setSnapshotStatus('');
        void onSaveSnapshot(png).then(()=>setSnapshotStatus('Chart capture saved')).catch(error=>setSnapshotStatus(error.message))
          .finally(()=>setSavingSnapshot(false));
      }}>Save chart evidence</button>}
      {snapshotStatus&&<span role="status">{snapshotStatus}</span>}
      <button type="button" className="snapshot-btn" aria-label={fullscreen?"Exit fullscreen":"Fullscreen chart"}
        title={fullscreen?"Exit fullscreen (Esc)":"Fullscreen chart"} onClick={()=>void toggleFullscreen()}>
        {fullscreen?<Minimize size={15}/>:<Maximize size={15}/>}
      </button>
      {fullscreenError&&<span role="status">{fullscreenError}</span>}
    </div>
  </div>;
}

export function TextField({label,value,onChange,placeholder='',type='text'}:{label:string;value:string;onChange:(v:string)=>void;placeholder?:string;type?:string}){
 return <label className="field"><span>{label}</span><input type={type} value={value} placeholder={placeholder} onChange={e=>onChange(e.target.value)}/></label>
}
export function TextArea({label,value,onChange,placeholder='',rows=3}:{label:string;value:string;onChange:(v:string)=>void;placeholder?:string;rows?:number}){
 return <label className="field"><span>{label}</span><textarea value={value} placeholder={placeholder} rows={rows} onChange={e=>onChange(e.target.value)}/></label>
}
export function Success({children}:{children:React.ReactNode}){return <p className="success-row"><Check size={16}/>{children}</p>}
export function useAsync<T>(fetcher:()=>Promise<T>, deps:unknown[]){const [data,setData]=useState<T|null>(null);const [error,setError]=useState('');const [loading,setLoading]=useState(true);
  useEffect(()=>{let active=true;setLoading(true);fetcher().then(d=>{if(active){setData(d);setError('')}}).catch(e=>{if(active)setError(e.message)}).finally(()=>{if(active)setLoading(false)});return()=>{active=false};},deps);return {data,error,loading,reload:(next:T)=>setData(next)};
}
