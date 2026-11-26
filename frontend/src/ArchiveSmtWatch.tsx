import {useEffect,useState} from 'react';
import {api} from './api';
import type {Dataset} from './types';

type WatchStatus={configuration:null|{primary_symbol:string;comparison_symbol:string;archive?:unknown};
  verified_through?:number;status?:string;error?:string;gap?:{open_time:number;symbols:string[]}};
const utc=(t:number)=>new Date(t*1000).toISOString();

export function ArchiveSmtWatch({datasets}:{datasets:Dataset[]}) {
  const [left,setLeft]=useState(''),[right,setRight]=useState('');
  const [start,setStart]=useState(''),[end,setEnd]=useState('');
  const [status,setStatus]=useState<WatchStatus>(),[error,setError]=useState('');
  useEffect(()=>{
    let active=true;
    const poll=async()=>{
      try{const value=await api.get<WatchStatus>('/indicators/smt/monitor');if(active)setStatus(value);}
      catch(e){if(active)setError(String(e));}
    };
    void poll();const timer=setInterval(poll,5000);
    return ()=>{active=false;clearInterval(timer);};
  },[]);
  const available=datasets.filter(d=>d.provider==='DUKASCOPY'&&d.kind==='candles'&&d.timeframe==='M5'&&d.price_basis==='BID');
  const lane=(symbol:string,value:string,set:(value:string)=>void)=><label>{symbol} M5 BID partition<select required value={value} onChange={e=>set(e.target.value)}>
    <option value="">Choose partition</option>{available.filter(d=>d.symbol===symbol).map(d=><option key={d.id} value={d.id}>{d.id}</option>)}</select></label>;
  return <section className="card"><h2>Historical SMT watch</h2>
    <form className="research-controls" onSubmit={async e=>{
      e.preventDefault();setError('');
      const pin=(id:string)=>({id,sha256:available.find(d=>d.id===id)?.sha256});
      try{
        await api.post('/indicators/smt/monitor/archive',{primary:pin(left),comparison:pin(right),start:Date.parse(start+'Z')/1000,end:Date.parse(end+'Z')/1000});
        setStatus(await api.get<WatchStatus>('/indicators/smt/monitor'));
      }catch(reason){setError(String(reason));}
    }}>{lane('EURUSD',left,setLeft)}{lane('GBPUSD',right,setRight)}
    <label>Start UTC<input required type="datetime-local" step="300" value={start} onChange={e=>setStart(e.target.value)}/></label>
    <label>End UTC<input required type="datetime-local" step="300" value={end} onChange={e=>setEnd(e.target.value)}/></label>
    <button type="submit">Watch exact M5 pairs</button></form>
    <p>Up to 60 bars in one FX session. Both feeds use modeled candle-close availability. Starting a watch replaces the active monitor.</p>
    {status?.configuration&&<p role="status">{status.configuration.primary_symbol} / {status.configuration.comparison_symbol} · {status.status??'Watching'} · Verified through {status.verified_through?utc(status.verified_through):'waiting for paired observations'}
      {status.gap&&' · Missing '+status.gap.symbols.join(', ')+' at '+utc(status.gap.open_time)}</p>}
    {(error||status?.error)&&<p role="alert">{error||status?.error}</p>}
  </section>;
}
