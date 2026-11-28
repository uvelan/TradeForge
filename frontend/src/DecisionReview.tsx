import {useEffect, useState} from 'react';
import {TrendingUp} from 'lucide-react';
import {ReplayControls, visibleAt} from './ReplayControls';
import {api} from './api';
import {Badge, CandleChart, Card, Empty, Notice, SectionTitle} from './components';
import type {Candle} from './types';
import './style.css';
import './workspace.css';

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
  return <Card><SectionTitle eyebrow="FROZEN DECISION CONTEXT" title="Chart at decision" action={cutoff!==null&&<Badge tone="blue">Cutoff {utc(cutoff*1000)}</Badge>}/>
    {!lanes.length&&<Empty title="No pinned chart available" body="This decision does not include frozen market lanes."/>}
    {primary.length>1&&<ReplayControls candles={primary} step={0} index={cursor} onSeek={setIndex}/>}
    <div className="two-column">{lanes.map(l=><section key={l.symbol} className="card"><h3>{l.symbol} · {l.timeframe} · {l.price_basis}</h3><p className="muted small">{l.feed_id}</p>
      <CandleChart candles={edge==null?l.bars:visibleAt(l.bars,edge,0)} symbol={l.symbol} timeframe={l.timeframe}/></section>)}</div></Card>;
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
  return <div className="main">
    <header className="topbar">
      <div className="topbar-start">
        <a href="/" className="brand" style={{padding:0,border:0,minHeight:'auto'}}>
          <span className="brand-mark"><TrendingUp size={18} strokeWidth={2.5}/></span>
          <span><strong>Trade<span>Forge</span></strong></span>
        </a>
        <nav className="breadcrumb" aria-label="Decision review breadcrumb">
          <a href="/" className="text-button">Workspace</a>
          <span>/</span>
          <a href="?practice" className="text-button">Decision reviews</a>
          {trade&&<><span>/</span><strong>{trade}</strong></>}
        </nav>
      </div>
      <div className="topbar-end"><Badge tone="amber">BLIND REVIEW DRILL</Badge></div>
    </header>
    <main className="page-content" style={{maxWidth:1240}}>
      <div className="page-heading">
        <div>
          <span className="eyebrow">PRACTICE / BLIND DECISION REVIEW</span>
          <h1>{trade ? 'Review '+trade+' · v'+version : 'Decision reviews'}</h1>
          <p>Assess the frozen historical plan and chart before revealing the recorded outcome.</p>
        </div>
      </div>
      {error&&<Notice tone="red">{error}</Notice>}
      {!trade&&<Card><SectionTitle eyebrow="PRACTICE QUEUE" title="Choose a decision" action={<Badge tone="neutral">{choices.length} available</Badge>}/>
        {choices.length?<div className="review-list">{choices.map(c=><div className="review-item" key={c.trade+c.version}>
          <div><a className="text-button" href={'?practice&trade='+encodeURIComponent(c.trade)+'&v='+c.version}>{c.symbol} · {c.trade} · v{c.version}</a></div>
          <Badge tone="blue">{c.phase}</Badge>
        </div>)}</div>:!error&&<Empty title="No completed decisions available" body="Close or decline a planned trade in Trade Planner to unlock blind decision practice drills."/>}
      </Card>}
      {review&&<div style={{display:'grid',gap:20}}>
        <Card><SectionTitle eyebrow="FROZEN METADATA" title="Original plan"/>
          <dl className="evidence-grid">{Object.entries(review.prompt.plan).map(([k,v])=><div key={k}><dt>{k.replaceAll('_',' ')}</dt><dd>{v}</dd></div>)}</dl>
          {review.prompt.signal_reference&&<p className="muted">Original signal revision · <Badge tone="blue">{review.prompt.signal_reference.status}</Badge></p>}
        </Card>
        <ReviewChart lanes={review.prompt.lanes} cutoff={review.prompt.cutoff}/>
        <div className="two-column">
          <Card><SectionTitle eyebrow="BLIND HYPOTHESIS" title="Independent assessment"/>
            {review.assessment?<p>{review.assessment}</p>:<div className="field">
              <label className="field"><span>What would you do, and why?</span><textarea rows={5} value={assessment} onChange={e=>setAssessment(e.target.value)}/></label>
              <button className="btn btn-primary" disabled={busy||!assessment.trim()} onClick={()=>submit('assess',{assessment,prompt_hash:review.prompt_hash})}>Save assessment</button>
            </div>}
            {review.assessment&&!review.revealed&&<button className="btn btn-secondary" disabled={busy} onClick={()=>submit('reveal')}>Reveal original outcome</button>}
          </Card>
          {review.revealed&&<Card><SectionTitle eyebrow="REVEALED AUDIT" title="Original outcome"/>
            <p><Badge tone="green">{review.revealed.kind}</Badge> {review.revealed.reason}</p>
            {review.revealed.kind==='TRADE_CLOSED'&&<p className="mono">Operator-recorded result {review.revealed.pnl_usd} USD · {review.revealed.result_r} R · {review.revealed.notes}</p>}
            {review.revealed.signal_history?.map(s=><p className="mono" key={s.status}>{s.status} · {utc(s.event_time*1000)}</p>)}
            {review.revealed.receipts?.map(r=><p className="small" key={r.fill_id}><a className="text-button" href={'/research/api/paper/plans/'+encodeURIComponent(trade!)+'/fills/'+r.fill_id}>{r.request.kind} receipt</a> · {r.request.quantity_eur} EUR at {r.event.price_units/r.scale} {r.side} · {utc(r.quote.time_ms)} · fees {r.fee_units/r.scale} USD · realized {r.realized_usd_units/r.scale} USD · modeled delivery delay {r.delivery_delay_ms} ms</p>)}
            <div className="form-subtitle">Practice drill</div>
            {review.practice?<div style={{display:'grid',gap:10}}>
              <p>{review.practice.classification} · {review.practice.objective} · due {review.practice.due} UTC · <Badge tone="blue">{review.phase}</Badge></p>
              {!review.completed&&<button className="btn btn-primary" disabled={busy} onClick={()=>submit('complete')}>Mark drill completed</button>}
            </div>:
            <div style={{display:'grid',gap:10}}>
              <label className="field"><span>Process assessment</span><div className="select-wrap"><select value={classification} onChange={e=>setClassification(e.target.value)}><option value="RULE_FOLLOWED">Rule followed</option><option value="PROCESS_MISTAKE">Process mistake</option></select></div></label>
              <label className="field"><span>Practice objective</span><input value={objective} onChange={e=>setObjective(e.target.value)}/></label>
              <label className="field"><span>Follow-up date (UTC)</span><input type="date" value={due} onChange={e=>setDue(e.target.value)}/></label>
              <button className="btn btn-primary" disabled={busy||!objective.trim()||!due} onClick={()=>submit('practice',{classification,objective,due})}>Schedule drill</button>
            </div>}
          </Card>}
        </div>
      </div>}
    </main>
  </div>;
}
