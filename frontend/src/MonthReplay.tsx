import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {RecordedPackageReview} from './RecordedPackageReview';
import {Badge, Card, Notice, SectionTitle} from './components';

type Event={missing_from_ms?:number;closure_at_ms?:number;kind:string;id:string;quote:{time_ms:number;ordinal:number};price_units?:number};
type Step={index:number;next_index:number;completed:boolean;through:number;quote_count:number;events:Event[];
  coverage:{start:number;status:string;gaps:number[][];count:number}[]};
type Manifest={policy_current:boolean;sessions:string[];session:string;binding:{month:string;hours:{start:number;status:string;pin:unknown}[]};
  checkpoint:Step|null;open_gap_intervals:number};
const utc=(sec:number)=>new Date(sec*1000).toISOString();
export function MonthReplay(){
  const [month,setMonth]=useState('2025-11'),[manifest,setManifest]=useState<Manifest>();
  const [step,setStep]=useState<Step|null>(null),[page,setPage]=useState<Step[]>([]);
  const [openGaps,setOpenGaps]=useState(0);
  const [busy,setBusy]=useState(false),[message,setMessage]=useState('');
  const [checked,setChecked]=useState<{id:string;quote:unknown}>();
  const generation=useRef(0),paused=useRef(false);
  useEffect(()=>()=>{generation.current++;paused.current=true},[]);
  const base='/month-replay/'+month;
  async function inspect(saved?:string){
    const token=++generation.current;
    setBusy(true);setMessage('');setChecked(undefined);setManifest(undefined);setStep(null);setPage([]);
    try{
      const result=await api.get<Manifest>(base+(saved?'?session='+saved:''));
      const first=await api.get<Step[]>(base+'/'+result.session+'/steps');
      if(token===generation.current){setManifest(result);setStep(result.checkpoint);setPage(first);setOpenGaps(result.open_gap_intervals)}
    }catch(e){if(token===generation.current)setMessage(String(e))}
    finally{if(token===generation.current)setBusy(false)}
  }
  async function run(){
    if(!manifest)return;
    const token=++generation.current,route=base+'/'+manifest.session;
    let index=step?.next_index??0;
    paused.current=false;setBusy(true);setMessage('');
    try{
      while(!paused.current && token===generation.current){
        const result=await api.post<Step>(route+'/advance?index='+index,{});
        if(token!==generation.current)return;
        setStep(result);index=result.next_index;
        setOpenGaps(count=>count+result.coverage.reduce((sum,batch)=>sum+batch.gaps.length,0));
        if(result.completed)break;
      }
      const first=await api.get<Step[]>(route+'/steps');
      if(token===generation.current)setPage(first);
    }catch(e){if(token===generation.current)setMessage(String(e))}
    finally{if(token===generation.current)setBusy(false)}
  }
  async function receipts(after:number){
    if(!manifest)return;
    const token=++generation.current;
    setBusy(true);
    try{const next=await api.get<Step[]>(base+'/'+manifest.session+'/steps?after='+after);
      if(token===generation.current)setPage(next)}
    catch(e){if(token===generation.current)setMessage(String(e))}
    finally{if(token===generation.current)setBusy(false)}
  }
  async function verify(e:Event){
    if(!manifest)return;
    const token=generation.current;
    try{
      const quote=await api.get(base+'/'+manifest.session+'/quote?time_ms='+e.quote.time_ms+'&ordinal='+e.quote.ordinal);
      if(token===generation.current)setChecked({id:e.id,quote});
    }catch(err){if(token===generation.current)setMessage(String(err))}
  }
  return <Card><section aria-label="Monthly EURUSD replay">
    <SectionTitle eyebrow="MONTHLY ARCHIVE REPLAY" title="Monthly EURUSD replay" action={manifest&&<Badge tone={manifest.policy_current?'green':'amber'}>{manifest.policy_current?'CURRENT POLICY':'HISTORICAL POLICY'}</Badge>}/>
    <p className="muted">First 10-point BID rise from the daily anchor · one long entry per UTC day · 10-pip stop and 20-pip target.
      BUY at the next observed ASK, exit on BID. One normalized unit, zero modeled fees and delivery delay.</p>
    <div className="research-controls">
      <label>Archive month (UTC)<input type="month" disabled={busy} value={month} onChange={e=>{setMonth(e.target.value);setManifest(undefined);setStep(null);setPage([])}}/></label>
      <button className="btn btn-secondary" disabled={busy||!month} onClick={()=>inspect()}>Inspect monthly coverage</button>
    </div>
    {message&&<Notice tone="red">{message}</Notice>}
    {manifest&&<><div className="evidence-grid">
      <div><dt>Registered hours</dt><dd className="mono">{manifest.binding.hours.filter(h=>h.pin).length}</dd></div>
      <div><dt>Missing open hours</dt><dd className="mono">{manifest.binding.hours.filter(h=>h.status==='MISSING_OPEN').length}</dd></div>
      <div><dt>Scheduled closures</dt><dd className="mono">{manifest.binding.hours.filter(h=>h.status==='SCHEDULED_CLOSURE').length}</dd></div>
    </div>
    {manifest.binding.hours.some(h=>h.status==='MISSING_OPEN')&&<Notice tone="amber">Open-session evidence is incomplete. Outcomes cover only observed prices. An open trade that encounters a gap remains unresolved.</Notice>}
    <div className="research-controls">
      {manifest.sessions.length>0&&<label>Pinned monthly session<select disabled={busy} value={manifest.session} onChange={e=>inspect(e.target.value)}>{[...new Set([manifest.session,...manifest.sessions])].map(id=><option key={id}>{id}</option>)}</select></label>}
      <button className="btn btn-primary" disabled={busy||!manifest.policy_current||step?.completed} onClick={run}>{step?'Resume monthly replay':'Run monthly strategy'}</button>
      {busy&&<button className="small-tool" onClick={()=>{paused.current=true}}>Pause after batch</button>}
    </div>
    {!manifest.policy_current&&<Notice tone="blue">Historical policy, read-only. Inspect monthly coverage again to start a current-policy replay.</Notice>}
    <details><summary>Hourly source pins and coverage</summary><pre>{JSON.stringify(manifest.binding,null,2)}</pre></details>
    {step&&<p role="status">{step.completed?'Replay ledger complete':'Replay committed'} through <span className="mono">{utc(step.through)}</span> · <span className="mono">{step.next_index}/{manifest.binding.hours.length*6}</span> ten-minute intervals · <span className="mono">{step.quote_count}</span> verified observations</p>}
    {step&&openGaps>0&&<Notice tone="amber">{openGaps} recorded open-session gap intervals through the committed frontier, including intervals within registered hours.</Notice>}
    <p className="muted small">Month-level performance metrics are unavailable. Evidence packages report price change for verified completed trades.</p>
    {page.flatMap(s=>s.events).map(e=><details key={e.id}><summary>{e.kind} · {utc((e.missing_from_ms??e.closure_at_ms??e.quote.time_ms)/1000)}</summary><pre>{JSON.stringify(e,null,2)}</pre><button className="small-tool" onClick={()=>verify(e)}>Verify source quote</button>
      {checked?.id===e.id&&<output className="success-row">Verified archive observation {JSON.stringify(checked.quote)}</output>}</details>)}
    {page.length>0&&<details><summary>Verified batch coverage and unresolved intervals</summary><pre>{JSON.stringify(page.map(s=>s.coverage),null,2)}</pre></details>}
    {step&&<div className="research-controls"><button className="small-tool" disabled={busy} onClick={()=>receipts(-1)}>First receipt page</button>
    <button className="small-tool" disabled={busy||!page.length||page.at(-1)!.next_index>=step.next_index} onClick={()=>receipts(page.at(-1)!.index)}>Next receipt page</button></div>}
    {step&&<RecordedPackageReview key={manifest.session} route={base+'/'+manifest.session} initialCutoff={step.through*1000-1}/>}
    </>}</section></Card>;
}
