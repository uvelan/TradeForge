import {useEffect,useState} from 'react';
import {api} from './api';
import type {Plan} from './types';

type Receipt={
  fill_id:string;request:{version:number;sequence:number;kind:string;quantity_eur:number;decision_ms:number};
  event:{order:{id:string};position_units:number;price_units:number;cash_units:number};
  quote:{time_ms:number;source_order:number;source_sha256:string};feed_id:string;side:string;
  realized_usd_units:number;fee_units:number;planned:{entry:string;stop:string;target:string};
};
const dollars=(n:number)=>(n/100000).toFixed(5);
const time=(ms:number)=>new Date(ms).toISOString();

export function PlanPaper({trade,version,plan,active=false}:{trade:string;version:number;plan?:Plan;active?:boolean}){
  const path='/paper/plans/'+trade;
  const [events,setEvents]=useState<Receipt[]>([]);
  const [error,setError]=useState(''),[busy,setBusy]=useState(true);
  const [quantity,setQuantity]=useState('1000'),[at,setAt]=useState('');
  const [cutoff,setCutoff]=useState<number>(),[eligible,setEligible]=useState(false);
  useEffect(()=>{
    let current=true;
    setBusy(true);
    Promise.all([api.get<{events:Receipt[]}>(path),plan?.snapshot_id?
      api.get<{cutoff:number|null;source:string;lanes?:{feed_id:string;price_basis:string}[]}>('/chart-snapshots/'+plan.snapshot_id):null])
      .then(([result,meta])=>{
        if(!current)return;
        setEvents(result.events);
        setEligible(meta?.source==='DUKASCOPY_SAVED'&&meta?.cutoff!=null&&meta?.lanes?.[0]?.feed_id==='dukascopy-bi5-v2'&&meta.lanes[0].price_basis==='BID');
        const t=meta?.cutoff==null?undefined:meta.cutoff*1000;
        setCutoff(t);
        const last=result.events.at(-1)?.quote.time_ms??t;
        if(last!=null)setAt(time(last).slice(0,-1));
      }).catch(e=>{if(current)setError(e.message);}).finally(()=>{if(current)setBusy(false);});
    return ()=>{current=false;};
  },[path,plan?.snapshot_id]);
  const first=events[0],last=events.at(-1);
  const correctRevision=!first||first.request.version===version;
  const equity=Number(plan?.equity_usd);
  const validOpening=equity>0&&equity<=10_000_000;
  const plannedRisk=plan?(Number(plan.entry)-Number(plan.stop))*Number(quantity):0;
  const overPlannedRisk=Boolean(plan&&!first&&plannedRisk>equity*Number(plan.risk_percent)/100);
  const usable=correctRevision&&(last?last.event.position_units>0:active&&eligible&&validOpening&&plan?.symbol==='EURUSD'&&plan.direction==='BUY');
  async function action(kind:'ENTRY'|'REDUCE'|'CLOSE'){
    setBusy(true);setError('');
    try{
      const row=await api.post<Receipt>(path+'/actions',{version,sequence:events.length+1,kind,
        quantity_eur:kind==='CLOSE'?last?.event.position_units:Number(quantity),decision_ms:Date.parse(at+'Z')});
      setEvents(prev=>prev.some(e=>e.fill_id===row.fill_id)?prev:[...prev,row]);
      setAt(time(row.quote.time_ms).slice(0,-1));
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  return <section aria-label="Paper trade review"><h3>Paper trade review</h3>
    {plan&&<p>Per-plan USD cash account. Long EURUSD, whole EUR units, market orders. Buys use ASK and sales use BID.
      Delivery delay, commission and additional slippage are modeled at zero. Manual reductions and close. No margin, automatic lot sizing or currency conversion. Opening cash up to USD 10 million. Submitting confirms your written entry conditions. Opening cash USD {plan.equity_usd}; planned risk budget {plan.risk_percent}%.</p>}
    {first&&!correctRevision&&<p>Submitted under v{first.request.version}. Select that revision to manage or review the position.</p>}
    {correctRevision&&first&&<><p>Frozen plan v{first.request.version} · Entry {first.planned.entry} · Stop {first.planned.stop} · Target {first.planned.target}</p>
      <table><thead><tr>{['Action','Quantity (EUR)','Executable price','Fill time (UTC)','Cash (USD)','Open EUR','Realized USD'].map(h=><th key={h}>{h}</th>)}</tr></thead>
      <tbody>{events.map(e=><tr key={e.fill_id}><td>{e.request.kind}</td><td>{e.request.quantity_eur}</td><td>{e.side} {dollars(e.event.price_units)}</td><td>{time(e.quote.time_ms)}</td><td>{dollars(e.event.cash_units)}</td><td>{e.event.position_units}</td><td>{dollars(e.realized_usd_units)}</td></tr>)}</tbody></table>
      <p>Modeled fees: USD 0.00000 · Source {first.feed_id}</p>
      <details><summary>Order IDs, quote ordinals and archive pins</summary><pre>{JSON.stringify(events,null,2)}</pre></details></>}
    {!busy&&!first&&<p>No paper orders for this plan.</p>}
    {plan&&correctRevision&&!first&&!usable&&!busy&&<p>Entry requires an active long EURUSD plan, supported opening cash, and a frozen chart linked to recorded BI5 BID evidence.</p>}
    {plan&&usable&&<div className="research-controls">
      <label>Decision time (UTC)<input type="datetime-local" step="0.001" value={at} onChange={e=>setAt(e.target.value)}/></label>
      <label>Quantity (EUR)<input type="number" min="1" step="1" value={quantity} onChange={e=>setQuantity(e.target.value)}/></label>
      {overPlannedRisk&&<p role="alert">Requested quantity exceeds planned USD risk at the declared entry.</p>}
      <button disabled={overPlannedRisk||busy||!at||!Number.isSafeInteger(Number(quantity))||Number(quantity)<1} onClick={()=>void action(first?'REDUCE':'ENTRY')}>{first?'Reduce paper position':'Submit paper buy'}</button>
      {first&&<button disabled={busy||!at} onClick={()=>void action('CLOSE')}>Close remaining paper position</button>}
      {cutoff!=null&&<small>Chart cutoff {time(cutoff)}</small>}
    </div>}
    {error&&<p role="alert">{error}</p>}
  </section>;
}

export function JournalPaper({trade,version}:{trade:string;version:number}){
  const [open,setOpen]=useState(false);
  return <details onToggle={e=>setOpen(e.currentTarget.open)}><summary>Planned versus paper execution</summary>
    {open&&<PlanPaper trade={trade} version={version}/>}</details>;
}
