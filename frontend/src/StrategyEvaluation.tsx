import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {Choice} from './StrategyRules';
import type {RecordVersion} from './strategyTypes';
import type {CandleRange,ChartSource,SymbolName,Timeframe} from './types';

const endpoint='/strategy-evaluations/v1';
type Source = {symbol:SymbolName;timeframe:Timeframe;source:ChartSource;feed_id:string;price_basis:string;delay_seconds?:number};
type Selection = {id:string;fingerprint:string;start:number;end:number;authority:string;availability_policy:string;series:Source[]};
type Evidence = {event_id:string;calculation_snapshot:string;source:(string|null)[];participants?:Record<string,(string|null)[]>};
type RuleResult = {id:string;phase:string;observation:string;outcome:string;reason:string;family:string|null;
  observed_at:number|null;available_at:number|null;selected_at:number|null;verified_at?:number|null;
  evidence_state?:string;evidence_reference:Evidence|null};
type Decision = {decision_id:string;attempt_id:string|null;state:string;reason:string;direction:string;cutoff:number;
  context_activated_at:number|null;setup_activated_at:number|null;trigger_activated_at:number|null;persisted?:false;
  invalidation:string;expiry:string;rules:RuleResult[];transitions:[number,string,string][];
  phase_results:Record<string,string>;
  attempt_bindings?:{rule:string;alias:string;family:string;evidence_state:string;activated_at:number;selected_at?:number;validity:string|null}[];
  activation_proofs?:(RuleResult & {phase_activated_at:number})[];
  temporal:{before:string;after:string;relation:string;outcome:string;reason:string}[];
  supporting_events:Record<string,unknown>;bound_crt?:{evidence:{family:string};activated_at:number}|null;
  evaluation:{evaluator_version:number;strategy_version_id:string;configuration_fingerprint:string;purpose:string;warmup_start:number;evaluation_start:number};
  coverage:{series:string;status:string;expected:number;observed:number;missing:number}[];
  entry_intent?:{status:string;role:string;direction:string;symbol:string;entry:{kind:string;price?:{value:number}};
    price_basis:string;strictly_after:number}|null};
type FrozenSelection = Omit<Selection,"series"|"fingerprint"> & {series:Record<string,Source>};
type Run = {run_id:string;decision:Decision;selection:FrozenSelection};
type SavedRun = {run_id:string;cutoff:number;state:string};
export type EvidenceChartLocation = {symbol:SymbolName;timeframe:Timeframe;source:ChartSource;
  range:CandleRange;evidencePath:string;timestamp:number};

const epoch=(value:string)=>Math.floor(Date.parse(value+'Z')/1000);
const fieldDate=(stamp:number)=>new Date(stamp*1000).toISOString().slice(0,19);
const utc=(stamp:number|null)=>stamp===null?'—':new Date(stamp*1000).toISOString().replace('T',' ').replace('.000Z',' UTC');
const title=(value:string)=>value.toLowerCase().replaceAll('_',' ');

export function StrategyEvaluation({record,openChart}:{record:RecordVersion;openChart?:(location:EvidenceChartLocation)=>void}){
  const [catalog,setCatalog]=useState<Selection[]>([]);
  const [selectionId,setSelection]=useState('');
  const [warmup,setWarmup]=useState('');
  const [start,setStart]=useState('');
  const [cutoff,setCutoff]=useState('');
  const [run,setRun]=useState<Run>();
  const [decision,setDecision]=useState<Decision>();
  const [saved,setSaved]=useState<SavedRun[]>([]);
  const [trace,setTrace]=useState<unknown>();
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const generation=useRef(0);
  const selection=catalog.find(item=>item.id===selectionId);
  const frozen=run?.selection;
  const legacy=!!run&&run.decision.evaluation.evaluator_version!==5;
  function choose(item:Selection){
    setSelection(item.id);setWarmup(fieldDate(item.start));
    setStart(fieldDate(Math.min(item.start+7200,item.end)));setCutoff(fieldDate(item.end));
  }
  const savedPath=endpoint+'/runs?strategy_version_id='+encodeURIComponent(record.version_id);
  useEffect(()=>{
    const gen=++generation.current;
    Promise.all([api.get<{selections:Selection[]}>(endpoint+'/selections'),api.get<SavedRun[]>(savedPath)])
      .then(([result,rows])=>{if(gen===generation.current){setCatalog(result.selections);setSaved(rows);if(result.selections[0])choose(result.selections[0]);}})
      .catch((issue:Error)=>{if(gen===generation.current)setError(issue.message);});
    return()=>{generation.current++;};
  },[savedPath]);

  async function execute(operation:(valid:()=>boolean)=>Promise<void>){
    const gen=++generation.current;
    const valid=()=>gen===generation.current;
    setBusy(true);setError('');setTrace(undefined);
    try{await operation(valid);}
    catch(issue){if(valid())setError(issue instanceof Error?issue.message:'Evaluation failed');}
    finally{if(valid())setBusy(false);}
  }
  function adopt(next:Run){setRun(next);setDecision(next.decision);}
  function load(runId:string){
    void execute(async valid=>{const data=await api.get<Run>(endpoint+'/'+runId);
      if(valid()){adopt(data);setCutoff(fieldDate(data.decision.cutoff));}});
  }
  function create(){
    if(!selection)return;
    void execute(async valid=>{
      const result=await api.post<Run>(endpoint,{evaluator_version:5,purpose:'HISTORICAL_SIMULATION',
        strategy_id:record.strategy_id,strategy_version_id:record.version_id,configuration_fingerprint:record.configuration_fingerprint,
        selection_id:selection.id,selection_fingerprint:selection.fingerprint,warmup_start:epoch(warmup),
        evaluation_start:epoch(start),cutoff:epoch(cutoff)});
      if(valid()){adopt(result);setCutoff(fieldDate(result.decision.cutoff));}
      const rows=await api.get<SavedRun[]>(savedPath);if(valid())setSaved(rows);
    });
  }
  function evaluate(operation:'advance'|'reconstruct'){
    if(!run||legacy)return;
    void execute(async valid=>{
      const result=await api.post<Decision>(endpoint+'/'+run.run_id+'/'+operation,{cutoff:epoch(cutoff),
        ...(operation==='advance'?{expected_decision_id:run.decision.decision_id}:{})});
      if(valid()){setDecision(result);if(operation==='advance')setRun({...run,decision:result});}
    });
  }
  function evidencePath(item:RuleResult){
    const ref=item.evidence_reference!;
    return endpoint+'/'+run!.run_id+'/decisions/'+decision!.cutoff+'/evidence/'+
      encodeURIComponent(ref.calculation_snapshot)+'/'+encodeURIComponent(ref.event_id);
  }
  function inspect(item:RuleResult){
    if(decision?.persisted===false&&item.evidence_reference){
      const ref=item.evidence_reference;
      setTrace(decision.supporting_events[ref.calculation_snapshot+":"+ref.event_id]);
      return;
    }
    void execute(async valid=>{const value=await api.get<unknown>(evidencePath(item));if(valid())setTrace(value);});
  }
  function chart(item:RuleResult){
    const ref=item.evidence_reference;
    const identity=ref?.participants?.primary??ref?.source;
    if(!identity||!openChart||item.observed_at===null||!frozen||!decision)return;
    const [source,symbol,frame]=identity;
    if((source!=='DUKASCOPY_SAVED'&&source!=='MT5_SAVED')||
       (symbol!=='EURUSD'&&symbol!=='GBPUSD')||(frame!=='M1'&&frame!=='M5'))return;
    const stride=frame==='M1'?60:300, observed=Math.floor(item.observed_at/stride)*stride;
    openChart({source,symbol,timeframe:frame,timestamp:observed,
      range:{start:Math.max(frozen.start,observed-15*stride),end:Math.min(decision.cutoff,observed+17*stride)},
      evidencePath:evidencePath(item)+'/chart'});
  }
  return <section className="card strategy-block" aria-label="Strategy evaluation">
    <div className="strategy-heading"><h2>Strategy evaluation</h2><span>Strategy revision {record.version} · Evaluator v{decision?.evaluation.evaluator_version??5} · Historical simulation</span></div>
    <p className="muted">Recorded MT5 readiness is blocked. This evaluation ends at entry intent.</p>
    {legacy&&<p role="status">Saved legacy evaluation. Start a new v5 evaluation to recalculate.</p>}
    {saved.length>0&&<div className="strategy-actions">
      <Choice label="Saved evaluation" value="" options={saved.map(item=>item.run_id)} blank="Choose saved run"
        display={id=>{const item=saved.find(entry=>entry.run_id===id)!;return utc(item.cutoff)+' · '+title(item.state);}}
        onChange={value=>{if(value)load(value);}}/>
    </div>}
    <fieldset className="strategy-edit-fields" disabled={busy||(!run&&!selection)}>
      {!run&&<Choice label="Approved data selection" value={selectionId} options={catalog.map(item=>item.id)}
        onChange={id=>{const next=catalog.find(item=>item.id===id);if(next)choose(next);}}/>}
      <div className="strategy-fields">
        {!run&&<>
          <label>Warm-up starts (UTC)<input type="datetime-local" step="1" value={warmup} onChange={e=>setWarmup(e.target.value)}/></label>
          <label>Evaluation starts (UTC)<input type="datetime-local" step="1" value={start} onChange={e=>setStart(e.target.value)}/></label>
        </>}
        <label>Decision cutoff (UTC)<input type="datetime-local" step="1" value={cutoff} onChange={e=>setCutoff(e.target.value)}/></label>
      </div>
      <div className="strategy-actions">
        {!run?<button type="button" className="btn btn-primary" disabled={!selection||![warmup,start,cutoff].every(v=>Number.isFinite(epoch(v)))}
          onClick={create}>Run evaluation</button>:<>
          <button type="button" className="small-tool" disabled={legacy||!Number.isFinite(epoch(cutoff))||epoch(cutoff)<=run.decision.cutoff}
            onClick={()=>evaluate('advance')}>Advance and save</button>
          <button type="button" className="small-tool" disabled={legacy||!Number.isFinite(epoch(cutoff))} onClick={()=>evaluate('reconstruct')}>Reconstruct cutoff</button>
          <button type="button" className="small-tool" onClick={()=>load(run.run_id)}>Reload saved decision</button>
          <button type="button" className="small-tool" onClick={()=>{setRun(undefined);setDecision(undefined);setTrace(undefined);if(selection)choose(selection);}}>New evaluation</button>
        </>}
      </div>
    </fieldset>
    {error&&<p role="alert" className="error-banner">{error}</p>}
    {busy&&<p role="status">Evaluating frozen evidence…</p>}
    {decision&&<>
      <h3>{title(decision.state)}</h3>
      <p role="status">{utc(decision.cutoff)} · {decision.direction} · {title(decision.reason)}
        {decision.persisted===false?' · Reconstructed view':''}</p>
      <p>Attempt {decision.attempt_id??'pending'} · Context {utc(decision.context_activated_at)} · Setup {utc(decision.setup_activated_at)}
        {' · Trigger '}{utc(decision.trigger_activated_at)}</p>
      {decision.bound_crt&&<p>Bound CRT family <code>{decision.bound_crt.evidence.family}</code></p>}
      {!!decision.attempt_bindings?.length&&<details>
        <summary>Attempt bindings</summary>
        <ul>{decision.attempt_bindings.map(binding=><li key={binding.rule}>
          {binding.alias} · {title(binding.evidence_state)} · Bound {utc(binding.activated_at)}
          {binding.selected_at!==undefined&&binding.selected_at!==binding.activated_at&&<> · Selected {utc(binding.selected_at)}</>}
          {" · "}{binding.validity?title(binding.validity):"Pinned observation"} <code>{binding.family}</code>
        </li>)}</ul>
      </details>}
      {!!decision.activation_proofs?.length&&<details>
        <summary>Original activation evidence</summary>
        <ul>{decision.activation_proofs.map(item=><li key={item.id}>
          {item.id} · {title(item.outcome)} · Phase activated {utc(item.phase_activated_at)}
          {item.evidence_reference&&<button type="button" className="small-tool" disabled={busy} onClick={()=>inspect(item)}>
            Inspect activation {item.id}
          </button>}
        </li>)}</ul>
      </details>}
      <p>Invalidation {title(decision.invalidation)} · Expiry {title(decision.expiry)}</p>
      {Object.entries(decision.phase_results).map(([name,status])=><p key={name}>{name} · {title(status)}</p>)}
      <h3>Market coverage</h3>
      <ul>{decision.coverage.map(lane=><li key={lane.series}>{lane.series} · {lane.observed}/{lane.expected} due bars
        {' · '}{title(lane.status)}{lane.missing?(' · '+lane.missing+' missing'):''}</li>)}</ul>
      {frozen&&<details>
        <summary>Frozen data and modeled delays</summary>
        <p>{frozen.id} · {title(frozen.authority)} · {title(frozen.availability_policy)}</p>
        <p>Warm-up {utc(decision.evaluation.warmup_start)} · Start {utc(decision.evaluation.evaluation_start)} · End {utc(frozen.end)}</p>
        <ul>{Object.entries(frozen.series).map(([key,lane])=><li key={key}>
          {lane.symbol} {lane.timeframe} · {lane.feed_id} · {lane.price_basis} · {lane.delay_seconds??0}s modeled delay
        </li>)}</ul>
      </details>}
      <div className="table-scroll"><table><caption>Rule evidence</caption><thead><tr>
        <th>Rule</th><th>Outcome</th><th>Observation and availability</th><th>Evidence</th>
      </tr></thead><tbody>{decision.rules.map(item=><tr key={item.id}>
        <td>{item.phase} · {item.id}<br/>{title(item.observation)}</td><td>{title(item.outcome)}<br/>{title(item.reason)}</td>
        <td>{utc(item.observed_at)}<br/>{utc(item.available_at)}
          {item.verified_at!=null&&<><br/>Current verification {utc(item.verified_at)}</>}</td>
        <td>{item.family&&<small>{item.family}</small>}{item.evidence_reference&&<>
          <button type="button" className="small-tool" disabled={busy} onClick={()=>inspect(item)}>Inspect {item.id}</button>
          {openChart&&decision.persisted!==false&&<button type="button" className="small-tool" onClick={()=>chart(item)}>Chart {item.id}</button>}
        </>}</td></tr>)}</tbody></table></div>
      {trace!=null&&<pre aria-label="Exact evidence" className="strategy-trace">{JSON.stringify(trace,null,2)}</pre>}
      {decision.temporal.length>0&&<details><summary>Temporal links</summary>
        <ul>{decision.temporal.map(link=><li key={[link.before,link.after,link.relation].join("-")}>
          {link.before} → {link.after} · {title(link.relation)} · {title(link.outcome)} · {title(link.reason)}
        </li>)}</ul></details>}
      <details><summary>Timeline</summary><ol>{decision.transitions.map(([at,state,reason],index)=>
        <li key={at+'-'+index}>{utc(at)} · {title(state)} · {title(reason)}</li>)}</ol></details>
      {decision.entry_intent&&<p><strong>Qualified entry intent</strong> {decision.entry_intent.direction} {decision.entry_intent.symbol}
        {' · '}{decision.entry_intent.entry.kind}{decision.entry_intent.entry.price?' '+decision.entry_intent.entry.price.value:''}
        {' · '}{decision.entry_intent.price_basis} · Awaiting execution validation</p>}
    </>}
  </section>;
}
