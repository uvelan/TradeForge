import {useEffect, useState} from 'react';
import {ReplayControls, visibleAt} from './ReplayControls';
import {api} from './api';
import {CandleChart, Card} from './components';
import type {Candle} from './types';

type Choice = {trade:string; version:number; phase:string; symbol:string};
type Lane = {symbol:string; timeframe:string; price_basis:string; feed_id:string; bars:Candle[]};
type Receipt = {fill_id:string; request:{kind:string;quantity_eur:number}; side:string; scale:number; fee_units:number;
  quote:{time_ms:number}; delivery_delay_ms:number; event:{price_units:number}; realized_usd_units:number};
type Review = {phase:string; prompt_hash:string; assessment?:string; prompt:{plan:Record<string,string>;
  cutoff:number|null; lanes:Lane[]; plan_version_id:string;signal_reference:{status:string;event_key:string}|null}; revealed?:{kind:string;reason?:string;pnl_usd?:string;result_r?:string;notes?:string;signal_history?:{status:string;event_time:number}[];receipts?:Receipt[]};
  practice?:{objective:string;due:string;classification:string}; completed?:boolean};
const base='/learning/reviews';
const utc=(ms:number)=>new Date(ms).toISOString();

function ReviewChart({lanes,cutoff}:{lanes:Lane[];cutoff:number|null}){
  const [index,setIndex]=useState<number>();
  const primary=lanes[0]?.bars??[];
  const cursor=index??primary.length-1;
  const edge=index===undefined ? cutoff : primary[cursor]?.available_at;
  return <Card><h2>Chart at decision</h2>{cutoff!==null&&<p>Evidence cutoff {utc(cutoff*1000)}</p>}
    {!lanes.length&&<p>No pinned chart available for this decision.</p>}
    {primary.length>1&&<ReplayControls candles={primary} step={0} index={cursor} onSeek={setIndex}/>}
    {lanes.map(l=><section key={l.symbol}><h3>{l.symbol} · {l.timeframe} · {l.price_basis}</h3><p>{l.feed_id}</p>
      <CandleChart candles={edge==null?l.bars:visibleAt(l.bars,edge,0)} symbol={l.symbol} timeframe={l.timeframe}/></section>)}</Card>;
}

export default function DecisionReview(){
  const query=new URLSearchParams(location.search);
  const trade=query.get('trade'), version=query.get('v');
  const path=trade && version ? base+'/'+encodeURIComponent(trade)+'/'+encodeURIComponent(version) : '';
  const [choices,setChoices]=useState<Choice[]>([]), [review,setReview]=useState<Review>();
  const [assessment,setAssessment]=useState(''), [objective,setObjective]=useState(''), [due,setDue]=useState('');
  const [classification,setClassification]=useState('RULE_FOLLOWED'), [busy,setBusy]=useState(false), [error,setError]=useState('');
  useEffect(()=>{
    let current=true;
    const read=path ? api.get<Review>(path).then(r=>{if(current)setReview(r)}) :
      api.get<Choice[]>(base).then(c=>{if(current)setChoices(c)});
    read.catch(e=>{if(current)setError(e.message)});
    return ()=>{current=false};
  },[path]);
  async function submit(step:string, body:unknown={}){
    setBusy(true);setError('');
    try {setReview(await api.post<Review>(path+'/'+step,body))}
    catch(e){setError((e as Error).message)} finally{setBusy(false)}
  }
  return <main style={{maxWidth:1050,margin:'auto',padding:24}}>
    <nav><a href="/">Workspace</a> / <a href="?practice">Decision reviews</a></nav>
    <h1>{trade ? 'Review '+trade+' · v'+version : 'Decision reviews'}</h1>
    {error&&<p role="alert">{error}</p>}
    {!trade&&<Card><h2>Choose a decision</h2>{choices.map(c=><p key={c.trade+c.version}><a href={'?practice&trade='+encodeURIComponent(c.trade)+'&v='+c.version}>{c.symbol} · {c.trade} · v{c.version}</a> · {c.phase}</p>)}{!choices.length&&!error&&'No completed decisions available.'}</Card>}
    {review&&<>
      <Card><h2>Original plan</h2><dl>{Object.entries(review.prompt.plan).map(([k,v])=><div key={k}><dt>{k.replaceAll('_',' ')}</dt><dd>{v}</dd></div>)}</dl></Card>
      {review.prompt.signal_reference&&<p>Original signal revision · {review.prompt.signal_reference.status}</p>}
      <ReviewChart lanes={review.prompt.lanes} cutoff={review.prompt.cutoff}/>
      <Card><h2>Independent assessment</h2>{review.assessment?<p>{review.assessment}</p>:<><label>What would you do, and why?<textarea value={assessment} onChange={e=>setAssessment(e.target.value)}/></label>
        <button disabled={busy||!assessment.trim()} onClick={()=>submit('assess',{assessment,prompt_hash:review.prompt_hash})}>Save assessment</button></>}
        {review.assessment&&!review.revealed&&<button disabled={busy} onClick={()=>submit('reveal')}>Reveal original outcome</button>}</Card>
      {review.revealed&&<Card><h2>Original outcome</h2><p>{review.revealed.kind} {review.revealed.reason}</p>
      {review.revealed.kind==='TRADE_CLOSED'&&<p>Operator-recorded result {review.revealed.pnl_usd} USD · {review.revealed.result_r} R · {review.revealed.notes}</p>}
      {review.revealed.signal_history?.map(s=><p key={s.status}>{s.status} · {utc(s.event_time*1000)}</p>)}
      {review.revealed.receipts?.map(r=><p key={r.fill_id}><a href={'/research/api/paper/plans/'+encodeURIComponent(trade!)+'/fills/'+r.fill_id}>{r.request.kind} receipt</a> · {r.request.quantity_eur} EUR at {r.event.price_units/r.scale} {r.side} · {utc(r.quote.time_ms)} · fees {r.fee_units/r.scale} USD · realized {r.realized_usd_units/r.scale} USD · modeled delivery delay {r.delivery_delay_ms} ms</p>)}
      {review.practice?<><p>{review.practice.classification} · {review.practice.objective} · due {review.practice.due} UTC · {review.phase}</p>
      {!review.completed&&<button disabled={busy} onClick={()=>submit('complete')}>Mark drill completed</button>}</>:
      <><label>Process assessment<select value={classification} onChange={e=>setClassification(e.target.value)}><option value="RULE_FOLLOWED">Rule followed</option><option value="PROCESS_MISTAKE">Process mistake</option></select></label>
      <label>Practice objective<input value={objective} onChange={e=>setObjective(e.target.value)}/></label>
      <label>Follow-up date (UTC)<input type="date" value={due} onChange={e=>setDue(e.target.value)}/></label>
      <button disabled={busy||!objective.trim()||!due} onClick={()=>submit('practice',{classification,objective,due})}>Schedule drill</button></>}</Card>}
    </>}
  </main>
}
