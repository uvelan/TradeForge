import {useEffect,useRef,useState} from 'react';
import {api} from './api';

type Amount={numerator:number;denominator:number};
type Receipt={
  session_id:string;cutoff_ms:number;observed_quotes:number;
  binding:{start:number;end:number};layout:{id:string;sha256:string}[];
  positions:{intent_id:string;status:string;closed:Amount;remaining:Amount;
    entry:{fill:null|{side:string;price:string;time_ms:number}};exit:null|{reason:string;price:string;time_ms:number}}[];
};
const stamp=(ms:number)=>new Date(ms).toISOString().slice(0,23);
const quantity=(q:Amount)=>q.numerator+'/'+q.denominator;
export function RecordedReplay({runId,start,end}:{runId:string;start:number;end:number}){
  const route='/historical-strategy-runs/v1/'+runId+'/recorded-replay';
  const [receipt,setReceipt]=useState<Receipt|null>(null);
  const [through,setThrough]=useState(stamp(end*1000));
  const [busy,setBusy]=useState(true);
  const [error,setError]=useState('');
  const [options,setOptions]=useState<{session_id:string;cutoff_ms:number;start:number;end:number}[]>([]);
  const [selection,setSelection]=useState({session:'',time:''});
  const [revision,setRevision]=useState(0);
  const sequence=useRef(0);
  const preference='tradeforge.recordedReplay.'+runId;
  useEffect(()=>{
    const token=++sequence.current;
    setBusy(true);
    async function load(){
      const list=await api.get<typeof options>(route.replace('/recorded-replay','/recorded-sessions'));
      const remembered=selection.session||localStorage.getItem(preference);
      const id=list.some(item=>item.session_id===remembered)?remembered:null;
      const query=new URLSearchParams();
      if(id)query.set('session_id',id);
      if(id&&selection.time)query.set('cutoff_ms',selection.time);
      const value=await api.get<Receipt|null>(route+'?'+query);
      if(sequence.current===token){setOptions(list);setReceipt(value);setError('');}
    }
    load().catch(e=>{if(sequence.current===token)setError(String(e));})
      .finally(()=>{if(sequence.current===token)setBusy(false);});
    return ()=>{sequence.current++;};
  },[route,selection,revision,preference]);
  const cutoff=Date.parse(through+'Z');
  async function advance(){
    setBusy(true); setError('');
    try{
      const token=sequence.current;
      const value=await api.post<Receipt>(route,{start:receipt?.binding.start??start,end:receipt?.binding.end??end,
        cutoff_ms:cutoff,resume:receipt?.session_id??null,partitions:receipt?.layout??[]});
      if(sequence.current===token){
        localStorage.setItem(preference,value.session_id);
        setSelection({session:value.session_id,time:''});setRevision(v=>v+1);
      }
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  return <section className="card strategy-block" aria-label="Recorded quote replay">
    <h3>Recorded quote replay</h3>
    <p className="muted">Local Dukascopy bid/ask · zero modeled delivery delay · normalized position quantities</p>
    <div className="research-controls">
    {options.length>0&&receipt&&<>
      <label>Recorded session and layout<select value={receipt.session_id} disabled={busy} onChange={e=>{
        localStorage.setItem(preference,e.target.value);setSelection({session:e.target.value,time:''});
      }}>{[...new Map(options.map(o=>[o.session_id,o])).values()].map(o=><option key={o.session_id} value={o.session_id}>
        {o.session_id} · {stamp(o.start*1000)} to {stamp(o.end*1000)}</option>)}</select></label>
      <label>Inspect saved checkpoint<select disabled={busy} value={selection.time} onChange={e=>setSelection({session:receipt.session_id,time:e.target.value})}>
        <option value="">Furthest committed</option>{options.filter(o=>o.session_id===receipt.session_id).map(o=>
          <option key={o.cutoff_ms} value={o.cutoff_ms}>{stamp(o.cutoff_ms)} UTC</option>)}</select></label>
    </>}
    <label>Replay through (UTC)<input type="datetime-local" step="0.001" value={through}
      onChange={e=>setThrough(e.target.value)}/></label>
    <button className="small-tool" disabled={busy||!Number.isFinite(cutoff)||cutoff<(receipt?.cutoff_ms??start*1000)
      ||cutoff>(receipt?.binding.end??end)*1000} onClick={()=>void advance()}>
      {receipt?'Resume recorded replay':'Save replay checkpoint'}</button>
    </div>
    {error&&<p role="alert">{error}</p>}
    {receipt&&<><p>Saved through {new Date(receipt.cutoff_ms).toISOString()} · {receipt.observed_quotes} quotes · {receipt.positions.length} qualified intents through cutoff</p>
      {receipt.positions.map(p=><p key={p.intent_id}>{p.status} · Closed {quantity(p.closed)} · Remaining {quantity(p.remaining)}
        {p.entry.fill&&' · Entry '+p.entry.fill.side+' '+p.entry.fill.price}
        {p.exit&&' · '+p.exit.reason+' '+p.exit.price+' at '+new Date(p.exit.time_ms).toISOString()}</p>)}
      <details><summary>Decision, quote and partition evidence</summary><pre>{JSON.stringify(receipt,null,2)}</pre></details></>}
  </section>;
}
