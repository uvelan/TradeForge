import {SnapshotDetails} from './SnapshotDetails';
import {snapshotLane} from './snapshotLanes';
import {useEffect,useRef,useState} from 'react';
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
  source_correction?:{observed_at:number;changes:unknown[]}; watch_published_at?:number; chart_cutoff?:number; event_key:string; status:SignalStatus; version:number; rules:SmtRules;
  strategy_revision?:{id:string;available_at:number};
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
  const selectionRequest=useRef(0);
  useEffect(()=>()=>{selectionRequest.current++;},[]);
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
            <button className="signal-row" aria-pressed={selected?.event_key===record.event_key} onClick={()=>{selectionRequest.current++;onSelect(record);}}>
              <strong>{record.provenance.primary.symbol} / {record.provenance.comparison.symbol}</strong>
              <span>{record.provenance.timeframe} · {record.direction} · {labels[record.status]}</span>
              <small>{format(record.transitions.at(-1)!.time*1000)}</small>
            </button>
            {record.watch_published_at!=null&&record.transitions.map(t=><button className="text-button" key={t.status}
              onClick={()=>{const token=++selectionRequest.current;
                api.get<SignalRecord>('/indicators/signals/'+record.event_key+'?status='+t.status)
                  .then(item=>{if(selectionRequest.current===token)onSelect(item);})
                  .catch(e=>{if(selectionRequest.current===token)setError(e.message);});}}>
              {labels[t.status]} · {format(t.time*1000)}</button>)}
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
  const archiveWatch=signal.chart_cutoff!=null && Boolean(signal.strategy_revision);
  const calculated=useSmt(left,right,timeframe,replay&&!archiveWatch,signal.rules,undefined,undefined,true,0,step);
  const transitions=signal.transitions.filter((t):t is SmtEvent['transitions'][number]=>
    t.status!=='SMT_SOURCE_CORRECTED' && (cutoff===undefined || t.time<=cutoff));
  const latest=transitions.at(-1);
  const confirmation=transitions.find(t=>t.status==='SMT_CONFIRMED');
  const recorded:SmtEvent|undefined=signal.status!=='SMT_SOURCE_CORRECTED' && latest ? {
    id:signal.id, event_key:signal.event_key, direction:signal.direction, leader:signal.leader,
    primary:signal.primary, comparison:signal.comparison, status:latest.status, transitions, timing:signal.timing,
    confirmed_at:confirmation?.time??null, confirmation_bar_time:confirmation?confirmation.time-step:null,
  } : undefined;
  const visibleEvent=replay&&!archiveWatch
    ? calculated.events?.find(event=>event.id===signal.id&&event.confirmed_at!==null)
    : recorded && (archiveWatch || recorded.confirmed_at!==null) ? recorded : undefined;
  const events=showLines&&visibleEvent?[visibleEvent]:[];
  async function open() {
    if(capture||loading)return;
    setLoading(true);setError('');
    try {
      const data=await api.get<Capture>('/indicators/signals/'+signal.event_key+'/candles?status='+signal.status+(primary.source.endsWith('_SAVED') && signal.chart_cutoff==null?'&view=retrospective':''));
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
    <header><h2>Evidence Inspector</h2><p>{replay?`${primary.symbol} / ${comparison.symbol} ${timeframe}`:signalSummary(signal).split(' | ').slice(0,3).join(' · ')}</p></header>
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
      {signal.watch_published_at!=null&&<div><dt>Watch publication</dt><dd>{at(signal.watch_published_at)}</dd></div>}{signal.chart_cutoff!=null&&<div><dt>Frozen chart cutoff</dt><dd>{at(signal.chart_cutoff)}</dd></div>}
      <div><dt>Signal ID</dt><dd className="evidence-id">{signal.event_key}</dd></div>
      {archiveWatch&&signal.strategy_revision&&<><div><dt>Strategy revision ID</dt><dd className="evidence-id">{signal.strategy_revision.id}</dd></div>
        <div><dt>Last contributing candle open</dt><dd>{at(signal.chart_cutoff!-step)}</dd></div>
        <div><dt>Modeled availability</dt><dd>{at(signal.strategy_revision.available_at)}</dd></div></>}
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
    {!replay&&signal.source_correction&&<details><summary>Changed source observations</summary>
      <pre>{JSON.stringify(signal.source_correction.changes,null,2)}</pre></details>}
    {!replay&&captures.length>0&&<label className="field">Chart capture evidence<select value={snapshot??''}
      onChange={event=>setSnapshot(event.target.value||undefined)}>
      <option value="">Choose a capture to attach</option>
      {captures.map(item=><option key={item.id} value={item.id}>{format(item.created_at)} · {item.id.slice(0,12)}</option>)}
    </select></label>}
    {!replay&&snapshot&&<><p><a href={`/research/api/chart-snapshots/${snapshot}/image`} target="_blank" rel="noreferrer">Open saved chart capture</a></p><SnapshotDetails id={snapshot}/></>}
    {!replay&&<details onToggle={event=>{if(event.currentTarget.open)openRelated();}}>
      <summary>Related plans</summary>
      {relatedError?<p role="alert">{relatedError}</p>:!related?<p>Loading plan links</p>:!related.length?<p>No plans are linked to this record.</p>:
        related.map(item=><p key={item.trade_id+item.version}><button className="text-button" onClick={()=>onOpenPlan(item.trade_id,item.version)}>
          {item.trade_id} · v{item.version} · {item.strategy}</button> · {item.evidence_status}</p>)}
    </details>}
    {capture&&<div className="evidence-chart">
      {!replay&&signal.chart_cutoff==null&&primary.source.endsWith('_SAVED')&&<p>Retrospective archive chart. Signal transition: {at(signal.transitions.at(-1)?.time)}.</p>}
      <div className="research-controls">
        {!replay&&<button className="small-tool" disabled={loading} onClick={()=>void replayEvidence()}>Replay recorded evidence</button>}
        <label><input type="checkbox" checked={showLines} onChange={e=>setShowLines(e.target.checked)}/> SMT lines</label>
        {replay&&<span role="status">{visibleEvent?labels[visibleEvent.status]:archiveWatch?'No visible signal at this cutoff':'No visible confirmation at this cutoff'}</span>}
      </div>
      <CandleChart onSaveSnapshot={replay?undefined:async png=>{
        const saved=await api.saveSnapshot(png,primary.symbol,timeframe,primary.source,signal.chart_cutoff,
          {event_key:signal.event_key,status:signal.status},primary.source.endsWith('_SAVED')?[
            snapshotLane('primary',primary.symbol,timeframe,primary,left),
            snapshotLane('comparison',comparison.symbol,timeframe,comparison,right??[]),
          ]:undefined);
        setSnapshot(saved.id);
        setCaptures(items=>[{id:saved.id,created_at:new Date().toISOString()},...items.filter(item=>item.id!==saved.id)]);
      }} candles={left} symbol={primary.symbol} timeframe={timeframe} focus={focus} height={360}
        comparison={{symbol:comparison.symbol,candles:right??[],placement:'New pane',precision:comparison.symbol.includes('JPY')?3:5,onPlacementChange:()=>{},fixedPlacement:true,smt:events}}/>
      {replay&&<ReplayControls candles={capture.primary} index={index!} onSeek={seek} onExit={()=>seek(undefined)} step={step} initialBookmarks={frozenReplay?.checkpoint?.bookmarks}
        onCheckpoint={frozenReplay?async(position,bookmarks)=>{
          await api.saveCheckpoint(frozenReplay.id,position,bookmarks);
        }:undefined}
        jumps={[{label:archiveWatch?'Selected receipt’s primary pivot close':'Primary pivot close',time:signal.primary.current.time+step},
          ...(signal.confirmed_at===null?[]:[{label:archiveWatch?'Selected receipt’s confirmation':'Signal confirmation',time:signal.confirmed_at}])]}/>}
    </div>}
  </aside>;
}
