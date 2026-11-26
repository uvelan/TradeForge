import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {Card} from './components';

type Event={missing_from_ms?:number;closure_at_ms?:number;kind:string;id:string;quote:{time_ms:number;ordinal:number};price_units?:number};
type Step={index:number;next_index:number;completed:boolean;through:number;quote_count:number;events:Event[];
  coverage:{start:number;status:string;gaps:number[][];count:number}[]};
type Manifest={policy_current:boolean;sessions:string[];session:string;binding:{month:string;hours:{start:number;status:string;pin:unknown}[]};checkpoint:Step|null};
const utc=(sec:number)=>new Date(sec*1000).toISOString();
export function MonthReplay(){
  const [month,setMonth]=useState('2025-11'),[manifest,setManifest]=useState<Manifest>();
  const [step,setStep]=useState<Step|null>(null),[page,setPage]=useState<Step[]>([]);
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
      if(token===generation.current){setManifest(result);setStep(result.checkpoint);setPage(first)}
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
  return <Card><section aria-label="Monthly EURUSD replay"><h2>Monthly EURUSD replay</h2>
    <p>First 10-point BID rise from the daily anchor · one long entry per UTC day · 10-pip stop and 20-pip target.
      BUY at the next observed ASK, exit on BID. One normalized unit, zero modeled fees and delivery delay.</p>
    <label>Archive month (UTC)<input type="month" disabled={busy} value={month} onChange={e=>{setMonth(e.target.value);setManifest(undefined);setStep(null);setPage([])}}/></label>
    <button disabled={busy||!month} onClick={()=>inspect()}>Inspect monthly coverage</button>
    {message&&<p role="alert">{message}</p>}
    {manifest&&<><p>{manifest.binding.hours.filter(h=>h.pin).length} registered hours · {manifest.binding.hours.filter(h=>h.status==='MISSING_OPEN').length} missing open hours · {manifest.binding.hours.filter(h=>h.status==='SCHEDULED_CLOSURE').length} scheduled closure hours</p>
    {manifest.binding.hours.some(h=>h.status==='MISSING_OPEN')&&<p role="note">Open-session evidence is incomplete. Outcomes cover only observed prices. An open trade that encounters a gap remains unresolved.</p>}
    {manifest.sessions.length>0&&<label>Pinned monthly session<select disabled={busy} value={manifest.session} onChange={e=>inspect(e.target.value)}>{[...new Set([manifest.session,...manifest.sessions])].map(id=><option key={id}>{id}</option>)}</select></label>}
    {!manifest.policy_current&&<p role="note">Historical policy, read-only. Inspect monthly coverage again to start a current-policy replay.</p>}
    <details><summary>Hourly source pins and coverage</summary><pre>{JSON.stringify(manifest.binding,null,2)}</pre></details>
    <button disabled={busy||!manifest.policy_current||step?.completed} onClick={run}>{step?'Resume monthly replay':'Run monthly strategy'}</button>
    {busy&&<button onClick={()=>{paused.current=true}}>Pause after batch</button>}
    {step&&<p>{step.completed?'Completed':'Committed'} through {utc(step.through)} · {step.next_index}/{manifest.binding.hours.length*6} ten-minute intervals · {step.quote_count} verified observations</p>}
    {page.flatMap(s=>s.events).map(e=><details key={e.id}><summary>{e.kind} · {utc((e.missing_from_ms??e.closure_at_ms??e.quote.time_ms)/1000)}</summary><pre>{JSON.stringify(e,null,2)}</pre><button onClick={()=>verify(e)}>Verify source quote</button>
      {checked?.id===e.id&&<output>Verified archive observation {JSON.stringify(checked.quote)}</output>}</details>)}
    {page.length>0&&<details><summary>Verified batch coverage and unresolved intervals</summary><pre>{JSON.stringify(page.map(s=>s.coverage),null,2)}</pre></details>}
    {step&&<><button disabled={busy} onClick={()=>receipts(-1)}>First receipt page</button>
    <button disabled={busy||!page.length||page.at(-1)!.next_index>=step.next_index} onClick={()=>receipts(page.at(-1)!.index)}>Next receipt page</button></>}
    </>}</section></Card>;
}
