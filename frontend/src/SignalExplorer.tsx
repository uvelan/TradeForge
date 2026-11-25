import {useEffect,useState} from 'react';
import {WorkspacePanels} from './WorkspacePanels';
import {api} from './api';
import {CandleChart,Notice} from './components';
import {frameMinutes} from './chartViews';
import {ReplayControls,visibleAt} from './ReplayControls';
import {type SmtEvent,type SmtRules,useSmt} from './smt';
import {useTimestampFormat,useTimezone} from './timezone';
import type {Candle,ReplaySession,Timeframe} from './types';

export type SignalStatus = SmtEvent['status'] | 'SMT_SOURCE_CORRECTED';
type SignalFeed = {symbol:string; source:string; feed_id:string; price_basis:string;
  dataset_id?:string; sha256?:string; partitions?:{id:string;sha256:string}[]};
export type SignalRecord = Omit<SmtEvent,'status'|'transitions'> & {
  event_key:string; status:SignalStatus; version:number; rules:SmtRules;
  transitions:{status:SignalStatus;time:number}[];
  provenance:{authority:string; timeframe:Timeframe; primary:SignalFeed; comparison:SignalFeed};
};
type Page = {items:SignalRecord[];next_cursor:string|null};
type Capture = {primary:Candle[]; comparison:Candle[]};
const labels:Record<SignalStatus,string> = {
  SMT_CANDIDATE:'Candidate', SMT_CONFIRMED:'Confirmed', SMT_INVALIDATED:'Invalidated',
  SMT_SOURCE_CORRECTED:'Source corrected',
};
export function signalSummary(signal:SignalRecord) {
  const {primary,comparison,timeframe}=signal.provenance;
  return `SMT ${signal.direction} | ${primary.symbol} / ${comparison.symbol} ${timeframe} | ${labels[signal.status]} | ${signal.event_key}`;
}
const datetime = (value:string)=>value?String(Date.parse(value+'Z')/1000):'';
const filters={q:'',instrument:'',direction:'',timeframe:'',source:'',status:'',start:'',end:''};

export function SignalExplorer({selected,onSelect,onPlan,onJournal,onOpenPlan}: {
  selected?:SignalRecord; onSelect:(record:SignalRecord)=>void; onPlan:(record:SignalRecord,snapshot?:string)=>void; onJournal:(record:SignalRecord,snapshot?:string)=>void; onOpenPlan:(id:string,version:number)=>void;
}) {
  const [draft,setDraft]=useState(filters);
  const [query,setQuery]=useState('');
  const [cursor,setCursor]=useState('');
  const [previous,setPrevious]=useState<string[]>([]);
  const [result,setResult]=useState<Page>();
  const [error,setError]=useState('');
  const [reload,setReload]=useState(0);
  const format=useTimestampFormat();
  useEffect(()=>{
    let active=true;
    setResult(undefined);setError('');
    const params=new URLSearchParams(query);
    if(cursor)params.set('cursor',cursor);
    api.get<Page>('/indicators/signals?'+params).then(page=>{if(active)setResult(page);})
      .catch(reason=>{if(active)setError(reason.message);});
    return()=>{active=false;};
  },[query,cursor,reload]);
  function selectFilter(key:keyof typeof filters,label:string,options:[string,string][]) {
    return <label>{label}<select value={draft[key]} onChange={e=>setDraft({...draft,[key]:e.target.value})}>
      <option value="">All</option>{options.map(([value,text])=><option key={value} value={value}>{text}</option>)}
    </select></label>;
  }
  return <section className="signal-page">
    <header><h1>Signal Explorer</h1><p className="muted">Recorded SMT evidence and its latest lifecycle state.</p></header>
    <form className="research-controls" onSubmit={event=>{
      event.preventDefault();
      const params=new URLSearchParams();
      for(const [key,value] of Object.entries(draft))if(value.trim())params.set(key,key==='start'||key==='end'?datetime(value):value.trim());
      setPrevious([]);setCursor('');setQuery(params.toString());setReload(value=>value+1);
    }}>
      <label>Signal, dataset or trade ID<input value={draft.q} maxLength={128} onChange={e=>setDraft({...draft,q:e.target.value})}/></label>
      <label>Instrument<input value={draft.instrument} maxLength={64} onChange={e=>setDraft({...draft,instrument:e.target.value})}/></label>
      {selectFilter('direction','Direction',[['bullish','Bullish'],['bearish','Bearish']])}
      {selectFilter('status','Current state',Object.entries(labels))}
      {selectFilter('timeframe','Timeframe',Object.keys(frameMinutes).map(value=>[value,value]))}
      {selectFilter('source','Source',[['MT5','MT5'],['MT5_SAVED','MT5 archive'],['DUKASCOPY_SAVED','Dukascopy archive']])}
      <label>From UTC<input type="datetime-local" value={draft.start} onChange={e=>setDraft({...draft,start:e.target.value})}/></label>
      <label>Until UTC<input type="datetime-local" value={draft.end} onChange={e=>setDraft({...draft,end:e.target.value})}/></label>
      <button className="btn btn-primary" type="submit">Search signals</button>
    </form>
    <WorkspacePanels inspectorOnly><div className="signal-layout">
      <div className="card signal-results">
        {error?<Notice tone="red">{error}</Notice>:!result?<p role="status">Loading signals…</p>:
          result.items.length===0?<p role="status">No recorded signals match these filters.</p>:
          <ul>{result.items.map(record=><li key={record.event_key}>
            <button className="signal-row" aria-pressed={selected?.event_key===record.event_key} onClick={()=>onSelect(record)}>
              <strong>{record.provenance.primary.symbol} / {record.provenance.comparison.symbol}</strong>
              <span>{record.provenance.timeframe} · {record.direction} · {labels[record.status]}</span>
              <small>{format(record.transitions.at(-1)!.time*1000)}</small>
            </button>
          </li>)}</ul>}
        <div className="research-controls">
          <button className="small-tool" disabled={!previous.length} onClick={()=>{setCursor(previous.at(-1)??'');setPrevious(previous.slice(0,-1));}}>Previous page</button>
          <button className="small-tool" disabled={!result?.next_cursor} onClick={()=>{if(result?.next_cursor){setPrevious([...previous,cursor]);setCursor(result.next_cursor);}}}>Next page</button>
        </div>
      </div>
      {selected&&<EvidenceInspector key={selected.event_key+selected.status} signal={selected} onPlan={onPlan} onJournal={onJournal} onOpenPlan={onOpenPlan}/>}
    </div></WorkspacePanels>
  </section>;
}

export function EvidenceInspector({signal,onPlan,onJournal,onOpenPlan}: {signal:SignalRecord;onPlan:(record:SignalRecord,snapshot?:string)=>void;onJournal:(record:SignalRecord,snapshot?:string)=>void;onOpenPlan:(id:string,version:number)=>void}) {
  const format=useTimestampFormat();
  const {zone}=useTimezone();
  const at=(time:number|null|undefined)=>time==null?'—':format(time*1000);
  const {timeframe,primary,comparison,authority}=signal.provenance;
  const step=frameMinutes[timeframe]*60;
  const [capture,setCapture]=useState<Capture>();
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false);
  const [index,seek]=useState<number>();
  const [frozenReplay,setFrozenReplay]=useState<ReplaySession>();
  const [showLines,setShowLines]=useState(true);
  const [snapshot,setSnapshot]=useState<string>();
  const [captures,setCaptures]=useState<{id:string;created_at:string}[]>([]);
  useEffect(()=>{
    let active=true;
    api.get<{id:string;created_at:string}[]>('/chart-snapshots?'+new URLSearchParams({event_key:signal.event_key,status:signal.status}))
      .then(items=>{if(active)setCaptures(items);}).catch(()=>{});
    return ()=>{active=false;};
  },[signal.event_key,signal.status]);
  const [copied,setCopied]=useState(false);
  const [focus,setFocus]=useState<{epoch:number;sequence:number}>();
  const [related,setRelated]=useState<{trade_id:string;version:number;strategy:string;evidence_status:string}[]>();
  const [relatedError,setRelatedError]=useState('');
  const relatedKey=signal.event_key;
  function openRelated(){
    setRelatedError('');
    api.get<NonNullable<typeof related>>('/indicators/signals/'+relatedKey+'/plans').then(setRelated)
      .catch(error=>setRelatedError(error.message));
  }
  const replay=index!==undefined&&Boolean(capture);
  const bar=replay?capture?.primary[index!]:undefined;
  const cutoff=bar?(bar.available_at??bar.time+step):undefined;
  const left=capture?(cutoff===undefined?capture.primary:visibleAt(capture.primary,cutoff,step)):[];
  const right=capture?(cutoff===undefined?capture.comparison:visibleAt(capture.comparison,cutoff,step)):undefined;
  const calculated=useSmt(left,right,timeframe,replay,signal.rules,undefined,undefined,true,0,step);
  const recorded = signal.status==='SMT_SOURCE_CORRECTED'?undefined:
    {...signal,status:signal.status,transitions:signal.transitions.filter(t=>t.status!=='SMT_SOURCE_CORRECTED') as SmtEvent['transitions'],
      confirmation_bar_time:signal.confirmed_at==null?null:signal.confirmed_at-step};
  const events=!showLines?[]:replay?(calculated.events?.filter(event=>event.id===signal.id&&event.confirmed_at!==null)??[]):recorded&&recorded.confirmed_at!==null?[recorded]:[];
  async function open() {
    if(capture||loading)return;
    setLoading(true);setError('');
    try {
      const data=await api.get<Capture>('/indicators/signals/'+signal.event_key+'/candles?status='+signal.status);
      if(!data.primary.length||!data.comparison.length)throw new Error('Recorded candles are unavailable');
      setCapture(data);
    }catch(reason){setError((reason as Error).message);}finally{setLoading(false);}
  }
  async function replayEvidence(){
    setLoading(true);setError('');
    try{
      const session=await api.post<ReplaySession>('/replay/sessions',{
        symbol:primary.symbol,timeframe,source:primary.source,
        signal_reference:{event_key:signal.event_key,status:signal.status},
      });
      const peer=session.views.comparison;
      if(!peer||'error' in peer)throw new Error('Original comparison capture unavailable');
      setFrozenReplay(session);setCapture({primary:session.market.candles,comparison:peer.candles});
      setFocus(undefined);seek(session.checkpoint?.position??0);
    }catch(reason){setError((reason as Error).message);}
    finally{setLoading(false);}
  }
  const move=(epoch:number)=>setFocus(previous=>({epoch,sequence:(previous?.sequence??0)+1}));
  const moveLabel=(epoch:number)=>capture?<button className="text-button" onClick={()=>move(epoch)}>{at(epoch)}</button>:at(epoch);
  const eligible=['EURUSD','GBPUSD','USDJPY','XAUUSD'].includes(primary.symbol);
  return <aside className="card signal-evidence" aria-label="Evidence Inspector">
    <header><h2>Evidence Inspector</h2><p>{signalSummary(signal).split(' | ').slice(0,3).join(' · ')}</p></header>
    <div className="research-controls">
      <button className="small-tool" disabled={loading||Boolean(capture)} onClick={()=>void open()}>{loading?'Loading chart…':capture?'Recorded chart loaded':'Open recorded chart'}</button>
      {!replay&&<button className="small-tool" onClick={()=>onJournal(signal,snapshot)}>Record lesson</button>}
      {eligible&&!replay&&<button className="small-tool" onClick={()=>onPlan(signal,snapshot)}>Create plan draft</button>}
      {!replay&&<button className="small-tool" onClick={()=>void navigator.clipboard.writeText(
        [signalSummary(signal), JSON.stringify(signal,null,2)].join('\n')
      ).then(()=>setCopied(true)).catch(()=>setError('Clipboard unavailable'))}>{copied?'Copied':'Copy evidence'}</button>}
    </div>
    {error&&<Notice tone="red">{error}</Notice>}
    {!replay&&<><dl className="evidence-grid">
      <div><dt>Signal ID</dt><dd className="evidence-id">{signal.event_key}</dd></div>
      <div><dt>Leading instrument</dt><dd>{(signal.leader==='primary'?primary:comparison).symbol}</dd></div>
      <div><dt>Confirmed at ({zone})</dt><dd>{signal.confirmed_at==null?'—':capture?<button className="text-button" onClick={()=>move(signal.confirmed_at!-step)}>{at(signal.confirmed_at)}</button>:at(signal.confirmed_at)}</dd></div>
      <div><dt>Matching window closed</dt><dd>{at(signal.transitions[0]?.time)}</dd></div>
      <div><dt>Invalidated / corrected</dt><dd>{at(signal.transitions.find(t=>t.status==='SMT_INVALIDATED'||t.status==='SMT_SOURCE_CORRECTED')?.time)}</dd></div>
      <div><dt>Allowed / actual offsets</dt><dd>{signal.rules.alignment_bars} / {Math.abs(signal.primary.previous.time-signal.comparison.previous.time)/step}, {Math.abs(signal.primary.current.time-signal.comparison.current.time)/step} bars</dd></div>
      <div><dt>Authority / rule version</dt><dd>{authority.replaceAll('_',' ')} · v{signal.version}</dd></div>
    </dl>
    <div className="evidence-pairs">{(['primary','comparison'] as const).map(side=>{
      const market=signal.provenance[side];const pivots=signal[side];
      const relationship=(pivots.current.price>pivots.previous.price?'Higher ':'Lower ')+(signal.direction==='bearish'?'high':'low');
      return <section key={side}><h3>{market.symbol} · {relationship}</h3>
        <p>{market.source} · {market.feed_id} · {market.price_basis}</p>
        {(['previous','current'] as const).map(point=><p key={point}>
          {point==='previous'?'Previous':'Current'} {pivots[point].price} · {moveLabel(pivots[point].time)} · pivot confirmed {at(pivots[point].confirmed_at)}
        </p>)}
        {(market.partitions??(market.dataset_id?[{id:market.dataset_id,sha256:market.sha256}]:[])).map(part=><details key={part.id}><summary>Dataset {part.id.slice(0,12)}</summary><p className="evidence-id">{part.id}<br/>{part.sha256}</p></details>)}
      </section>;
    })}</div></>}
    {!replay&&captures.length>0&&<label className="field">Chart capture evidence<select value={snapshot??''}
      onChange={event=>setSnapshot(event.target.value||undefined)}>
      <option value="">Choose a capture to attach</option>
      {captures.map(item=><option key={item.id} value={item.id}>{format(item.created_at)} · {item.id.slice(0,12)}</option>)}
    </select></label>}
    {!replay&&snapshot&&<p><a href={`/research/api/chart-snapshots/${snapshot}/image`} target="_blank" rel="noreferrer">Open saved chart capture</a></p>}
    {!replay&&<details onToggle={event=>{if(event.currentTarget.open)openRelated();}}>
      <summary>Related plans</summary>
      {relatedError?<p role="alert">{relatedError}</p>:!related?<p>Loading plan links</p>:!related.length?<p>No plans are linked to this record.</p>:
        related.map(item=><p key={item.trade_id+item.version}><button className="text-button" onClick={()=>onOpenPlan(item.trade_id,item.version)}>
          {item.trade_id} · v{item.version} · {item.strategy}</button> · {item.evidence_status}</p>)}
    </details>}
    {capture&&<div className="evidence-chart">
      <div className="research-controls">
        {!replay&&<button className="small-tool" disabled={loading} onClick={()=>void replayEvidence()}>Replay recorded evidence</button>}
        <label><input type="checkbox" checked={showLines} onChange={e=>setShowLines(e.target.checked)}/> SMT lines</label>
        {replay&&<span role="status">{events.length?labels[events[0].status]:'No visible confirmation at this cutoff'}</span>}
      </div>
      <CandleChart onSaveSnapshot={replay?undefined:async png=>{
        const saved=await api.saveSnapshot(png,primary.symbol,timeframe,primary.source,signal.transitions.at(-1)?.time,
          {event_key:signal.event_key,status:signal.status});
        setSnapshot(saved.id);
        setCaptures(items=>[{id:saved.id,created_at:new Date().toISOString()},...items.filter(item=>item.id!==saved.id)]);
      }} candles={left} symbol={primary.symbol} timeframe={timeframe} focus={focus} height={360}
        comparison={{symbol:comparison.symbol,candles:right??[],placement:'New pane',precision:comparison.symbol.includes('JPY')?3:5,onPlacementChange:()=>{},fixedPlacement:true,smt:events}}/>
      {replay&&<ReplayControls candles={capture.primary} index={index!} onSeek={seek} onExit={()=>seek(undefined)} step={step} initialBookmarks={frozenReplay?.checkpoint?.bookmarks}
        onCheckpoint={frozenReplay?async(position,bookmarks)=>{
          await api.saveCheckpoint(frozenReplay.id,position,bookmarks);
        }:undefined}
        jumps={[{label:'Primary pivot close',time:signal.primary.current.time+step},
          ...(signal.confirmed_at===null?[]:[{label:'Signal confirmation',time:signal.confirmed_at}])]}/>}
    </div>}
  </aside>;
}
