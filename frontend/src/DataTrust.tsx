import {useState} from 'react';
import {useTimestampFormat} from './timezone';
import type {CandleResponse,Timeframe} from './types';
import {frameMinutes} from './chartViews';

function CoverageCalendar({coverage}: {coverage:NonNullable<CandleResponse['coverage']>}){
  const first=Math.floor(coverage.start/86400);
  const last=Math.floor((coverage.end-1)/86400);
  const [page,setPage]=useState(0);
  const [selected,setSelected]=useState<number>();
  const start=first+page*31;
  const days=Array.from({length:Math.max(0,Math.min(31,last-start+1))},(_,index)=>start+index);
  const gapsFor=(day:number)=>coverage.gaps.filter(gap=>gap.end>day*86400&&gap.start<(day+1)*86400);
  const iso=(day:number)=>new Date(day*86400*1000).toISOString().slice(0,10);
  const selectedGaps=selected===undefined?[]:gapsFor(selected);
  return <details>
    <summary>UTC coverage calendar</summary>
    <p className="muted">{coverage.truncated?
      'This archive response is truncated. An unmarked day has no reported gaps in the returned data.':
      'Counts reflect the selected archive window. Gaps can include market closures.'}</p>
    <div className="coverage-days">{days.map(day=>{
      const count=gapsFor(day).length;
      return <button key={day} className="small-tool" aria-pressed={selected===day} onClick={()=>setSelected(day)}>
        {iso(day)} · {count?count+' gaps':'0 reported'}</button>;
    })}</div>
    <div className="research-controls">
      <button className="small-tool" disabled={!page} onClick={()=>{setPage(page-1);setSelected(undefined);}}>Earlier dates</button>
      <button className="small-tool" disabled={start+30>=last} onClick={()=>{setPage(page+1);setSelected(undefined);}}>Later dates</button>
    </div>
    {selected!==undefined&&<div>
      <strong>{iso(selected)} UTC</strong>
      {selectedGaps.length?<ul>{selectedGaps.map(gap=><li key={gap.start}>
        {new Date(gap.start*1000).toISOString()} – {new Date(gap.end*1000).toISOString()}
      </li>)}</ul>:<p>No gaps were reported for the requested portion of this day.</p>}
    </div>}
  </details>;
}

export function DataTrust({data,symbol,timeframe,error}: {
  data?:CandleResponse|null; symbol:string; timeframe:Timeframe; error?:string;
}) {
  const format=useTimestampFormat();
  const last=data?.candles.at(data.latest_unconfirmed?-2:-1);
  const coverage=data?.coverage;
  const state=error?.startsWith('UNTRUSTED:')?'Untrusted':error?(data?'Stale':error.includes('Backend unreachable')?'Offline':'Unavailable'):!data?'Unavailable':
    coverage?.truncated||coverage?.gaps.length?'Incomplete':
    data.partitions?.length?'Verified archive files':'Unverified coverage';
  return <details className="data-trust">
    <summary>Data Trust · {state}</summary>
    <dl className="evidence-grid">
      <div><dt>Instrument</dt><dd>{symbol} · {timeframe}</dd></div>
      <div><dt>Source / basis</dt><dd>{data?.source ?? '—'} · {data?.price_basis ?? '—'}</dd></div>
      <div><dt>Feed</dt><dd>{data?.feed_id ?? 'Unavailable'}</dd></div>
      <div><dt>Latest completed candle</dt><dd>{last?format((last.available_at??last.time+frameMinutes[timeframe]*60)*1000):'Unavailable'}</dd></div>
      {coverage&&<div><dt>Archive window</dt><dd>{format(coverage.start*1000)} to {format(coverage.end*1000)} · {coverage.duplicate_bars} duplicate bars{coverage.truncated?' · truncated':''}</dd></div>}
    </dl>
    {data?.source_completeness==='UNVERIFIED'&&!data.origin&&<p>Provider quote completeness is unverified.</p>}
    {data?.origin&&<section aria-label="Source coverage">
      <p>{data.origin.reconstruction==='MATCHES_ARCHIVED_QUOTES'
        ?data.origin.checked_bars+' candle(s) match the OHLC reconstructed from the archived quotes.'
        :data.origin.reconstruction==='CANDLE_ARCHIVE'?'Provider candle archive.':'No linked quote manifest.'} Provider quote completeness is unverified.</p>
      {data.origin.file_observations&&<p>{data.origin.file_observations==='ALL_RECORDS_VERIFIED'
        ?'Every record in the checksum-verified BI5 file matched the stored quote series.'
        :'The original BI5 file is unavailable for quote comparison.'}</p>}
      {coverage&&<p>Observed archive interval (UTC): {new Date(coverage.start*1000).toISOString()} to {new Date(coverage.end*1000).toISOString()} (exclusive)</p>}
      {data.origin.start!=null&&data.origin.end!=null&&<p>Import interval (UTC): {new Date(data.origin.start*1000).toISOString()} to {new Date(data.origin.end*1000).toISOString()} (exclusive). Missing candle slots: {data.origin.missing_slots?.length}.</p>}
      {data.origin.source_sha256&&<p className="evidence-id">Source SHA-256 {data.origin.source_sha256}</p>}
    </section>}
    {error&&<p role="status">{error}</p>}
    {data?.partitions?.map(part=><details key={part.id}><summary>Partition {part.id.slice(0,12)}</summary><p className="evidence-id">{part.id}<br/>{part.sha256}</p></details>)}
    {coverage&&coverage.end>coverage.start&&<CoverageCalendar key={coverage.start+":"+coverage.end} coverage={coverage}/>}
    {coverage?.gaps.map(gap=><details key={gap.start}><summary>Gap {format(gap.start*1000)} to {format(gap.end*1000)}</summary>
      <p>Expected {timeframe} UTC slots. No archived candles were returned for this interval. Cause unknown.</p>
    </details>)}
  </details>;
}
