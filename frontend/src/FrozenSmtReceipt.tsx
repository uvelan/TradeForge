import {useEffect, useState} from 'react';
import {api} from './api';
import type {SignalRecord} from './SignalExplorer';
import type {Timeframe} from './types';

type Feed = SignalRecord['provenance']['primary'];
type Receipt = SignalRecord & {strategy_revision?: {id:string; available_at:number}};
type Page = {items:Receipt[]; next_cursor:string|null};
type Props = {cutoff:number; timeframe:Timeframe; primary:Feed; comparison?:Feed};

function pins(feed:Feed) {
  return feed.partitions?.length ? feed.partitions :
    feed.dataset_id && feed.sha256 ? [{id:feed.dataset_id, sha256:feed.sha256}] : [];
}

function identity(feed:Feed) {
  return JSON.stringify([feed.symbol, feed.source, feed.feed_id, feed.price_basis,
    pins(feed).map(p=>[p.id,p.sha256]).sort(([a],[b])=>a.localeCompare(b))]);
}

function utc(epoch:number) {
  return new Date(epoch*1000).toISOString().replace('T',' ').replace('.000Z',' UTC');
}

export function FrozenSmtReceipt(props:Props) {
  const {primary,comparison,timeframe}=props;
  const scope=JSON.stringify([identity(primary),comparison && identity(comparison),timeframe]);
  return <section className="card" aria-label="Recorded SMT at chart cutoff">
    <h3>Recorded SMT at chart cutoff</h3>
    {!pins(primary).length || (comparison && !pins(comparison).length)
      ? <p>Chart partition checksums are unavailable.</p>
      : <FamilyPicker key={scope} {...props}/>}
  </section>;
}

function FamilyPicker({cutoff,timeframe,primary,comparison}:Props) {
  const [preferred,setPreferred]=useState('');
  const [page,setPage]=useState({cutoff,cursor:''});
  const [response,setResponse]=useState<{url:string; data?:Page; error?:string}>();
  const params=new URLSearchParams({as_of:String(cutoff),timeframe,instrument:primary.symbol,
    source:primary.source,q:pins(primary)[0].id,limit:'100'});
  if(page.cutoff===cutoff && page.cursor) params.set('cursor',page.cursor);
  const url='/indicators/signals?'+params;
  useEffect(()=>{
    let current=true;
    api.get<Page>(url).then(data=>{if(current)setResponse({url,data});})
      .catch(error=>{if(current)setResponse({url,error:String(error)});});
    return ()=>{current=false;};
  },[url]);
  if(response?.url!==url) return <p role="status">Loading recorded revisions…</p>;
  if(!response.data) return <p role="alert">{response.error}</p>;
  const matches=response.data.items.filter(event=>{
    const {provenance}=event;
    if(provenance.authority!=='HISTORICAL_SIMULATION') return false;
    return (['primary','comparison'] as const).some(role=>
      identity(provenance[role])===identity(primary) &&
      (!comparison || identity(provenance[role==='primary'?'comparison':'primary'])===identity(comparison)));
  });
  const selected=matches.find(e=>e.event_key===preferred)??matches[0];
  const next=response.data.next_cursor;
  return <>
    <p>Chart cutoff {utc(cutoff)}</p>
    {selected ? <>
      <label>Recorded SMT family <select value={selected.event_key} onChange={e=>setPreferred(e.target.value)}>
        {matches.map(e=><option key={e.event_key} value={e.event_key}>{e.direction} · {utc(e.primary.current.time)} · {e.event_key.slice(0,12)}</option>)}
      </select></label>
      <Revision receipt={selected} chartPrimary={primary} paired={Boolean(comparison)}/>
    </> : <p>No recorded SMT revision on this page matches the frozen archive pins.</p>}
    {params.has('cursor') && <button onClick={()=>setPage({cutoff,cursor:''})}>First page</button>}
    {next && <button onClick={()=>setPage({cutoff,cursor:next})}>Older families</button>}
  </>;
}

function Revision({receipt,chartPrimary,paired}:{receipt:Receipt;chartPrimary:Feed;paired:boolean}) {
  const first=identity(receipt.provenance.primary)===identity(chartPrimary)?'primary':'comparison';
  const roles=[first, first==='primary'?'comparison':'primary'] as const;
  const revision=receipt.status==='SMT_SOURCE_CORRECTED'?undefined:receipt.strategy_revision;
  const transition=receipt.transitions.at(-1)!;
  return <div data-event-key={receipt.event_key} data-status={receipt.status} data-revision-id={revision?.id}>
    <p><strong>{receipt.status}</strong> · {receipt.direction} · {receipt.source_correction?'Correction observed':'Modeled availability'} {utc(transition.time)}</p>
    {revision && <p>Revision <code>{revision.id}</code></p>}
    {roles.map((role,i)=>{
      const lane=receipt.provenance[role];
      const pivots=receipt[role];
      return <details key={role}><summary>{lane.symbol} · {i===0?'chart primary':paired?'chart comparison':'watch peer'} · {lane.price_basis}</summary>
        <p>Previous pivot {utc(pivots.previous.time)} · {pivots.previous.price}</p>
        <p>Current pivot {utc(pivots.current.time)} · {pivots.current.price}</p>
        <p>{lane.feed_id} · {receipt.provenance.timeframe}</p>
        {pins(lane).map(p=><p key={p.id}>Dataset <code>{p.id}</code> · SHA-256 <code>{p.sha256}</code></p>)}
      </details>;
    })}
  </div>;
}
