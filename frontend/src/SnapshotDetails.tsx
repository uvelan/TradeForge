import {useEffect,useState} from 'react';
import {api} from './api';
import type {SnapshotLane} from './snapshotLanes';

import type {DecisionReplay,SymbolName,Timeframe,ChartSource} from './types';
export type DecisionLocation={symbol:SymbolName;timeframe:Timeframe;source:ChartSource;decision:DecisionReplay};
type ReturnAction={onReturn?:(location:DecisionLocation)=>void};
type Metadata={symbol:SymbolName;source:ChartSource;timeframe:Timeframe;decision_replay?:DecisionReplay;cutoff:number|null;
  chart_context?:"FROZEN"|"RETROSPECTIVE"; signal_reference?:unknown; signal_transition_at?:number; chart_evidence_through?:number;
  comparison_gap?:{symbol:string;timeframe:string;status:"NO_VISIBLE_OBSERVATIONS"|"SOURCE_ERROR";error?:string};
  lanes?:Array<Omit<SnapshotLane,'bars'>&{last_bar:{time:number;close:number};bar_times:number[]}>};

function MetadataView({id,onReturn}:{id:string}&ReturnAction){
  const [data,setData]=useState<Metadata>();
  const [error,setError]=useState('');
  useEffect(()=>{
    let active=true;
    api.get<Metadata>('/chart-snapshots/'+id).then(value=>{if(active)setData(value);})
      .catch(e=>{if(active)setError(e.message);});
    return ()=>{active=false;};
  },[id]);
  if(error)return <p role="alert">{error}</p>;
  if(!data)return <p role="status">Loading capture details…</p>;
  return <div><p>{data.source} · {data.symbol} · {data.timeframe}
    </p>
    {data.decision_replay&&onReturn&&<button className="small-tool" onClick={()=>onReturn({
      symbol:data.symbol,timeframe:data.timeframe,source:data.source,decision:data.decision_replay!
    })}>Return to decision candle</button>}
    {data.chart_context==='RETROSPECTIVE'&&<p>Retrospective signal chart</p>}
    {data.cutoff!=null&&<p>{data.signal_reference&&!data.chart_context?'Legacy linked timestamp (cutoff semantics unverified)':'Frozen chart cutoff'}: {new Date(data.cutoff*1000).toISOString()}</p>}
    {data.signal_transition_at!=null&&<p>Signal transition: {new Date(data.signal_transition_at*1000).toISOString()}</p>}
    {data.cutoff!=null&&data.signal_transition_at!=null&&data.signal_transition_at>data.cutoff&&
      <p role="alert">This signal revision occurred after the saved chart cutoff. It was unavailable in the frozen view.</p>}
    {data.chart_evidence_through!=null&&<p>Latest plotted candle availability: {new Date(data.chart_evidence_through*1000).toISOString()}</p>}
    {data.comparison_gap&&<p>{data.comparison_gap.symbol} · {data.comparison_gap.status==='SOURCE_ERROR'?
      'Comparison source unavailable · '+data.comparison_gap.error:'No comparison candles available at this cutoff'}</p>}
    {data.lanes?data.lanes.map(lane=><section key={lane.role}>
      <strong>{lane.role} · {lane.symbol} · {lane.timeframe}</strong>
      <p>{lane.feed_id} · {lane.price_basis} · {lane.bar_times.length} bars · Last close {lane.last_bar.close}</p>
      {lane.partitions.map(pin=><p className="evidence-id" key={pin.id}>Dataset {pin.id}<br/>SHA-256 {pin.sha256}</p>)}
    </section>):<p>This capture has no recorded comparison-lane references. Its historical peer cannot be verified from the image metadata.</p>}</div>;
}
export function SnapshotDetails({id,onReturn}:{id:string}&ReturnAction){
  const [open,setOpen]=useState(false);
  return <details onToggle={e=>setOpen(e.currentTarget.open)}><summary>Chart capture details</summary>
    {open&&<MetadataView key={id} id={id} onReturn={onReturn}/>}</details>;
}
