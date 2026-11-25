import {useEffect,useState} from 'react';
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
  useEffect(()=>{
    let live=true;
    api.get<Receipt|null>(route).then(value=>{if(live)setReceipt(value);})
      .catch(e=>{if(live)setError(String(e));}).finally(()=>{if(live)setBusy(false);});
    return ()=>{live=false;};
  },[route]);
  const cutoff=Date.parse(through+'Z');
  async function advance(){
    setBusy(true); setError('');
    try{
      setReceipt(await api.post<Receipt>(route,{start:receipt?.binding.start??start,end:receipt?.binding.end??end,
        cutoff_ms:cutoff,resume:receipt?.session_id??null,partitions:receipt?.layout??[]}));
    }catch(e){setError(String(e));}finally{setBusy(false);}
  }
  return <section aria-label="Recorded quote replay">
    <h3>Recorded quote replay</h3>
    <p>Local Dukascopy bid/ask · zero modeled delivery delay · normalized position quantities</p>
    <label>Replay through (UTC)<input type="datetime-local" step="0.001" value={through}
      onChange={e=>setThrough(e.target.value)}/></label>
    <button className="small-tool" disabled={busy||!Number.isFinite(cutoff)||cutoff<(receipt?.cutoff_ms??start*1000)
      ||cutoff>(receipt?.binding.end??end)*1000} onClick={()=>void advance()}>
      {receipt?'Resume recorded replay':'Save replay checkpoint'}</button>
    {error&&<p role="alert">{error}</p>}
    {receipt&&<><p>Saved through {new Date(receipt.cutoff_ms).toISOString()} · {receipt.observed_quotes} quotes · {receipt.positions.length} qualified intents through cutoff</p>
      {receipt.positions.map(p=><p key={p.intent_id}>{p.status} · Closed {quantity(p.closed)} · Remaining {quantity(p.remaining)}
        {p.entry.fill&&' · Entry '+p.entry.fill.side+' '+p.entry.fill.price}
        {p.exit&&' · '+p.exit.reason+' '+p.exit.price+' at '+new Date(p.exit.time_ms).toISOString()}</p>)}
      <details><summary>Decision, quote and partition evidence</summary><pre>{JSON.stringify(receipt,null,2)}</pre></details></>}
  </section>;
}
