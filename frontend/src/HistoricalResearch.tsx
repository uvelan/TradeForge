import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import type {EvidenceChartLocation} from './StrategyEvaluation';
import type {Listed} from './strategyTypes';

const endpoint='/historical-strategy-runs/v1';
const utc=(value:number)=>new Date(value*1000).toISOString();
const inputTime=(value:number)=>utc(value).slice(0,19);
const epoch=(value:string)=>Date.parse(value+'Z')/1000;
const human=(value:string)=>value.toLowerCase().replaceAll('_',' ');
type Source={id:string;fingerprint:string;start:number;end:number};
type Summary={run_id:string;state:string;frontier:number|null};
type Run={run_id:string;identity:{historical_version:number;evaluator_version:number;method:string;evaluation_start:number;warmup_start:number;cutoff:number;rearm:string;strategy_version_id:string};
  selection:{series:Record<string,{symbol:string;timeframe:string;feed_id:string;delay_seconds:number;price_basis:string}>};
  results_published:boolean;worker:{state:string;frontier:number|null;diagnostic:string|null};attempt_counts:Record<string,number>;qualified_intents:number};
type Decision={id:string;cutoff:number;state:string;attempt_id:string|null};
type Reference={calculation_snapshot:string;event_id:string;source:string[];participants?:{primary:string[]}};
type Proof={state?:string;type?:string;timing:{observation_start:number};evidence_reference:Reference};
type Check={id:string;outcome:string;verified_at?:number|null};
type Snapshot={decision_id:string;rules:Check[];activation_outcomes?:(Check & {phase_activated_at:number})[];reason:string;supporting_events:Record<string,Proof>;entry_intent:{id:string;direction:string;symbol:string;status:string}|null};

export function HistoricalResearch({openChart}:{openChart:(location:EvidenceChartLocation)=>void}){
  const [available,setAvailable]=useState<Listed[]>([]);
  const [sources,setSources]=useState<Source[]>([]);
  const [version,setVersion]=useState('');
  const [source,setSource]=useState('');
  const [warmup,setWarmup]=useState('');
  const [start,setStart]=useState('');
  const [end,setEnd]=useState('');
  const [rearm,setRearm]=useState('SINGLE_ATTEMPT');
  const [alias,setAlias]=useState('');
  const [maxAttempts,setMaxAttempts]=useState(8);
  const [parts,setParts]=useState('');
  const [saved,setSaved]=useState<Summary[]>([]);
  const [runId,setRunId]=useState('');
  const [runRevision,setRunRevision]=useState(0);
  const [run,setRun]=useState<Run>();
  const [decisions,setDecisions]=useState<Decision[]>([]);
  const [trace,setTrace]=useState<{runId:string;cutoff:number;decisionId:string;warmupStart:number;data:Snapshot}>();
  const inspection = useRef(0);
  function chooseRun(id:string){
    inspection.current++;
    setTrace(undefined);
    setRunId(id);
  }
  const [notice,setNotice]=useState('');
  const [problem,setProblem]=useState('');
  const [busy,setBusy]=useState(false);
  const selected=available.find(item=>item.version_id===version);

  useEffect(()=>{
    let active=true;
    Promise.all([api.get<Listed[]>('/strategies/v1'),api.get<{approved:Source[]}>(endpoint+'/selections')])
      .then(([records,data])=>{
        if(!active)return;
        const ready=records.filter(item=>item.status==='READY_FOR_EVALUATION'&&item.purpose==='HISTORICAL_SIMULATION'&&item.schema_version===2);
        setAvailable(ready);setVersion(ready[0]?.version_id??'');setSources(data.approved);
        if(data.approved[0])pickSource(data.approved[0].id,data.approved);
      }).catch(error=>{if(active)setProblem(error.message);});
    return()=>{active=false;};
  },[]);
  useEffect(()=>{
    if(!version){setSaved([]);return;}
    let active=true;
    api.get<Summary[]>(endpoint+'?strategy_version_id='+encodeURIComponent(version))
      .then(data=>{if(active)setSaved(data);}).catch(error=>{if(active)setProblem(error.message);});
    return()=>{active=false;};
  },[version,runId]);
  useEffect(()=>{
    setRun(undefined);setDecisions([]);setTrace(undefined);
    if(!runId)return;
    let active=true;
    let timer:ReturnType<typeof setTimeout>|undefined;
    async function load(){
      try{
        const result=await api.get<Run>(endpoint+'/'+runId);
        const entries=result.results_published?await api.get<Decision[]>(endpoint+'/'+runId+'/decisions'):[];
        if(!active)return;
        setRun(result);setDecisions(entries);
        if(['QUEUED','RUNNING','CANCELLING'].includes(result.worker.state))timer=setTimeout(load,900);
      }catch(error){if(active)setProblem(error instanceof Error?error.message:String(error));}
    }
    void load();
    return()=>{active=false;if(timer)clearTimeout(timer);};
  },[runId,runRevision]);

  function pickSource(id:string, updated=sources){
    setSource(id);
    const found=updated.find(item=>item.id===id);
    if(found){setWarmup(inputTime(found.start));setStart(inputTime(found.start+7200));setEnd(inputTime(found.end));}
  }
  async function action(work:()=>Promise<void>){
    setBusy(true);setProblem('');setNotice('');
    try{await work();}catch(err){setProblem(err instanceof Error?err.message:String(err));}finally{setBusy(false);}
  }
  const valid=selected&&source&&[warmup,start,end].every(value=>Number.isFinite(epoch(value)))&&
    (rearm==='SINGLE_ATTEMPT'||alias.trim())&&(source!=='WORKSPACE'||parts.trim());
  function submit(validate:boolean){
    if(!selected||!valid)return;
    const found=sources.find(item=>item.id===source);
    void action(async()=>{
      const result=await api.post<{run_id:string;identity?:{method:string}}>(endpoint+(validate?'/validate':''),{
        evaluator_version:6,purpose:'HISTORICAL_SIMULATION',strategy_id:selected.strategy_id,
        strategy_version_id:version,configuration_fingerprint:selected.configuration_fingerprint,
        selection_id:source,selection_fingerprint:found?.fingerprint??'0'.repeat(64),warmup_start:epoch(warmup),
        evaluation_start:epoch(start),cutoff:epoch(end),rearm,context_alias:rearm==='NEW_CONTEXT_FAMILY'?alias.trim():null,
        max_attempts:rearm==='NEW_CONTEXT_FAMILY'?maxAttempts:1,
        partitions:source==='WORKSPACE'?parts.trim().split(/[\s,]+/):[],
      });
      if(validate)setNotice('Verified source coverage · '+human(result.identity?.method??''));
      else chooseRun(result.run_id);
    });
  }
  function inspect(item:Decision){
    if(!run||run.run_id!==runId)return;
    const origin={runId,cutoff:item.cutoff,decisionId:item.id,warmupStart:run.identity.warmup_start};
    const request=++inspection.current;
    setTrace(undefined);
    void api.get<Snapshot>(endpoint+'/'+origin.runId+'/decisions/'+origin.cutoff).then(data=>{
      if(inspection.current===request&&data.decision_id===origin.decisionId)setTrace({...origin,data});
    }).catch(error=>{
      if(inspection.current===request)setProblem(error instanceof Error?error.message:String(error));
    });
  }

  function showChart(proof:Proof, accepted:NonNullable<typeof trace>){
    const {runId,cutoff,warmupStart}=accepted;
    const ref=proof.evidence_reference;
    const [provider,symbol,timeframe]=ref.participants?.primary??ref.source;
    if((provider!=='DUKASCOPY_SAVED'&&provider!=='MT5_SAVED')||
        (symbol!=='EURUSD'&&symbol!=='GBPUSD')||(timeframe!=='M1'&&timeframe!=='M5'))return;
    const step=timeframe==='M1'?60:300;
    const anchor=Math.floor(proof.timing.observation_start/step)*step;
    openChart({symbol,timeframe,source:provider,timestamp:anchor,range:{start:Math.max(warmupStart,anchor-15*step),
      end:Math.min(cutoff,anchor+17*step)},evidencePath:endpoint+'/'+runId+'/decisions/'+cutoff+'/evidence/'+
      encodeURIComponent(ref.calculation_snapshot)+'/'+encodeURIComponent(ref.event_id)+'/chart'});
  }

  return <section className="card strategy-block" aria-label="Historical strategy evaluation">
    <div className="strategy-heading"><h2>Historical strategy evaluation</h2></div>
    <p className="muted">Frozen market data · Mechanical opportunities and qualified entry intents</p>
    {!available.length&&<p>Save a ready historical strategy in Strategy Builder to begin.</p>}
    <fieldset className="strategy-edit-fields" disabled={busy||!available.length}>
      <div className="strategy-fields">
        <label>Strategy version<select value={version} onChange={event=>{setVersion(event.target.value);chooseRun('');}}>
          {available.map(item=><option key={item.version_id} value={item.version_id}>{item.name} · revision {item.version}</option>)}
        </select></label>
        <label>Frozen source<select value={source} onChange={event=>pickSource(event.target.value)}>
          {sources.map(item=><option key={item.id} value={item.id}>{item.id}</option>)}
          <option value="WORKSPACE">Verified workspace partitions</option>
        </select></label>
        {source==='WORKSPACE'&&<label>Workspace partition IDs<textarea value={parts} onChange={event=>setParts(event.target.value)}/></label>}
        <label>Warm-up start (UTC)<input type="datetime-local" step="1" value={warmup} onChange={event=>setWarmup(event.target.value)}/></label>
        <label>Evaluation start (UTC)<input type="datetime-local" step="1" value={start} onChange={event=>setStart(event.target.value)}/></label>
        <label>Evaluation end (UTC)<input type="datetime-local" step="1" value={end} onChange={event=>setEnd(event.target.value)}/></label>
        <label>Rearm policy<select value={rearm} onChange={event=>setRearm(event.target.value)}>
          <option value="SINGLE_ATTEMPT">Single attempt</option><option value="NEW_CONTEXT_FAMILY">New context family</option>
        </select></label>
        {rearm==='NEW_CONTEXT_FAMILY'&&<>
          <label>Required context alias<input value={alias} onChange={event=>setAlias(event.target.value)}/></label>
          <label>Maximum attempts<input type="number" min={1} max={32} value={maxAttempts} onChange={event=>setMaxAttempts(+event.target.value)}/></label>
        </>}
      </div>
      <div className="strategy-actions">
        <button className="small-tool" disabled={!valid} onClick={()=>submit(true)}>Validate historical inputs</button>
        <button className="btn btn-primary" disabled={!valid} onClick={()=>submit(false)}>Queue historical evaluation</button>
      </div>
    </fieldset>
    {saved.length>0&&<label>Saved historical run<select value={runId} onChange={event=>chooseRun(event.target.value)}>
      <option value="">Choose a run</option>
      {saved.map(item=><option key={item.run_id} value={item.run_id}>{item.frontier===null?'Awaiting frontier':utc(item.frontier)} · {human(item.state)}</option>)}
    </select></label>}
    {notice&&<p role="status">{notice}</p>}
    {problem&&<p role="alert" className="error-banner">{problem}</p>}
    {run&&<>
      <h3>{human(run.worker.state)}</h3>
      <p role="status">{human(run.identity.method)} · {human(run.identity.rearm)} · Processed through {run.worker.frontier===null?'—':utc(run.worker.frontier)}</p>
      <p>Window {utc(run.identity.evaluation_start)} to {utc(run.identity.cutoff)} · Warm-up from {utc(run.identity.warmup_start)}</p>
      <p>{Object.values(run.selection.series).map(lane=>lane.symbol+' '+lane.timeframe+' '+lane.feed_id+' '+lane.price_basis+' · '+lane.delay_seconds+'s modeled delay').join(' | ')}</p>
      {run.worker.diagnostic&&<p role="alert">{run.worker.diagnostic}</p>}
      <p>Qualified intents {run.qualified_intents} · {Object.entries(run.attempt_counts).map(([state,count])=>human(state)+' '+count).join(' · ')}</p>
      <div className="strategy-actions">
        {['QUEUED','RUNNING'].includes(run.worker.state)&&<button className="small-tool" disabled={busy} onClick={()=>void action(async()=>{
          setRun(await api.post<Run>(endpoint+'/'+runId+'/cancel',{}));
        })}>Cancel run</button>}
        {run.identity.historical_version===2&&run.identity.evaluator_version===6&&['FAILED','CANCELLED'].includes(run.worker.state)&&<button className="small-tool" disabled={busy} onClick={()=>void action(async()=>{
          const next=await api.post<Run>(endpoint+'/'+runId+'/resume',{});
          setRun(next);
          setRunRevision(revision=>revision+1);
        })}>Resume run</button>}
      </div>
      <ul>{decisions.map(item=><li key={item.id}>{utc(item.cutoff)} · {human(item.state)}{' '}
        <button className="small-tool" disabled={busy} onClick={()=>inspect(item)}>Inspect historical decision</button></li>)}</ul>
      {trace?.runId===runId&&<div>
        <p>{human(trace.data.reason)}{trace.data.entry_intent&&' · '+trace.data.entry_intent.direction+' '+trace.data.entry_intent.symbol+' · '+human(trace.data.entry_intent.status)}</p>
        {!!trace.data.activation_outcomes?.length&&<ul>{trace.data.activation_outcomes.map(activation=>{
          const latest=trace.data.rules.find(rule=>rule.id===activation.id);
          return <li key={activation.id}>{activation.id} · At activation {human(activation.outcome)} {utc(activation.phase_activated_at)}
            {latest&&<> · Latest {human(latest.outcome)}{latest.verified_at!=null&&' '+utc(latest.verified_at)}</>}
          </li>;
        })}</ul>}
        {Object.values(trace.data.supporting_events).map(proof=><button key={proof.evidence_reference.event_id}
          className="small-tool" onClick={()=>showChart(proof,trace)}>Chart {human(proof.state??proof.type??'evidence')}</button>)}
      </div>}
    </>}
  </section>;
}
