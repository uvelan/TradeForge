import {useState} from 'react';
import {api} from './api';
import {Badge, Card, Notice, SectionTitle, Spinner} from './components';
import type {CandleRange,CoverageSelection} from './types';

type Interval = [number,number];
type Pin = {id:string;sha256:string;feed:string;source_sha256?:string};
type Lane = {feed:string|null;alternatives?:Lane[];count:number;missing:Interval[];pins:Pin[];error:string|null};
type Hour = {start:number;end:number;status:string;expected:number;eur:Lane;gbp:Lane;paired:number;
  paired_ranges:Interval[];chart_selections:CoverageSelection[];pair_error:string|null;quotes:{count:number;pins:Pin[];registered:boolean;gaps:Interval[];error:string|null}};
type Report = {hours:Hour[];start:number;end:number;max_gap_ms:number};

export const HOURLY_COVERAGE_TONES = {
  'Healthy': 'green',
  'Conflicting evidence': 'red',
  'Scheduled closure': 'neutral',
  'Partial': 'amber',
  'Missing open time': 'amber',
} as const;
export type HourlyCoverageStatus = keyof typeof HOURLY_COVERAGE_TONES;
type CoverageTone = (typeof HOURLY_COVERAGE_TONES)[HourlyCoverageStatus];

export function hourlyCoverageTone(status: string): CoverageTone {
  if (Object.hasOwn(HOURLY_COVERAGE_TONES, status)) {
    return HOURLY_COVERAGE_TONES[status as HourlyCoverageStatus];
  }
  return 'amber';
}
const iso = (seconds:number)=>new Date(seconds*1000).toISOString();
const dateOf = (seconds:number)=>iso(seconds).slice(0,10);
const utcClock = (seconds:number)=>iso(seconds).slice(11,16);
const istClock = (seconds:number)=>utcClock(seconds+19800);
const today = Math.floor(Date.now()/86400000)*86400;
function pins(items:Pin[]){
  return items.map(p=><li key={p.id}>{p.feed} · partition <code>{p.id}</code> · SHA-256 <code>{p.sha256}</code>
    {p.source_sha256&&<> · original <code>{p.source_sha256}</code></>}</li>);
}
function intervals(items:Interval[],ms=false){
  return items.map(([a,b])=><li key={a}>{new Date(ms?a:a*1000).toISOString()} – {new Date(ms?b:b*1000).toISOString()}</li>);
}

export function ForexCoverage({openReplay,imported}:{openReplay:(range:CandleRange)=>void;imported:()=>void}){
  const [start,setStart]=useState(dateOf(today-86400)),[end,setEnd]=useState(dateOf(today));
  const [report,setReport]=useState<Report>(),[day,setDay]=useState(''),[hour,setHour]=useState<number>();
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[receipt,setReceipt]=useState('');
  const [file,setFile]=useState<File>(),[symbol,setSymbol]=useState<'EURUSD'|'GBPUSD'>('EURUSD');
  const selected=report?.hours.find(h=>h.start===hour);
  async function load(fresh:boolean){
    const next=await api.get<Report>('/forex/coverage?'+new URLSearchParams({start,end}));
    setReport(next);
    if(fresh){setDay(start);setHour(undefined);setFile(undefined);}
  }
  async function act(fn:()=>Promise<void>){
    setBusy(true);setError('');setReceipt('');
    try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  async function recover(remote:boolean,batch=false){
    if(!selected)return;
    const d=dateOf(selected.start),h=utcClock(selected.start).slice(0,2);
    if(remote){
      const pending=batch?report!.hours.filter(row=>row.start>=selected.start&&dateOf(row.start)===day&&row.expected&&!row.quotes.registered).slice(0,4):[selected];
      const messages:string[]=[];
      for(const row of pending){
        const res=await api.post<{status:string;attempts?:{http:number|null;curl_exit:number;validation_error?:string}[]}>('/forex/retry?hour='+row.start,{});
        const last=res.attempts?.at(-1);
        messages.push('EURUSD '+day+' '+utcClock(row.start)+' UTC · '+res.status.replaceAll('_',' ')+(last?' (HTTP '+(last.http??'unavailable')+', curl '+last.curl_exit+(last.validation_error?', '+last.validation_error:'')+')':''));
        setReceipt(messages.join(' · '));
        if(!['COMPLETE','Already registered','Imported cached hour'].includes(res.status))break;
      }
    }else if(file){
      if(file.name.toLowerCase().endsWith('.csv')){
        await api.importCoverageCsv(symbol,d,file);
        setReceipt(symbol+' · '+d+' · BID candle file registered');
      }else{
        const res=await api.importCoverageHour(symbol,selected.start,file);
        setReceipt(symbol+' · '+d+' '+h+':00 UTC · '+res.tick_dataset.rows+' recorded quotes registered');
      }
    }
    imported();await load(false);
  }
  const days=report?[...new Set(report.hours.map(h=>dateOf(h.start)))]:[];
  const totals=report?.hours.reduce((a,h)=>[a[0]+h.eur.count,a[1]+h.gbp.count,a[2]+h.paired,a[3]+h.quotes.count],[0,0,0,0]);
  const update=()=>{setReport(undefined);setHour(undefined);setError('');setReceipt('');};
  return <Card><section className="forex-coverage" aria-label="Forex history">
    <SectionTitle eyebrow="DUKASCOPY COVERAGE" title="Forex history"/>
    <p className="muted">Dukascopy · M5 BID chart candles and recorded EURUSD bid/ask ticks. Select up to 31 completed UTC days.</p>
    <form onSubmit={e=>{e.preventDefault();void act(()=>load(true));}}><fieldset disabled={busy} className="coverage-fields">
      <label>Start date (UTC)<input type="date" required value={start} onChange={e=>{setStart(e.target.value);update();}}/></label>
      <label>Exclusive end date (UTC)<input type="date" required value={end} max={dateOf(today)} onChange={e=>{setEnd(e.target.value);update();}}/></label>
      <button className="btn btn-primary">Inspect coverage</button></fieldset></form>
    {busy&&<div className="loading-zone" role="status"><Spinner/><span>Checking archive evidence…</span></div>}{error&&<Notice tone="red">{error}</Notice>}{receipt&&<p className="success-row" role="status">{receipt}</p>}
    {report&&totals&&<><p className="muted small">{start} 00:00 to {end} 00:00 UTC, end exclusive (05:30 IST on each date).</p>
      <dl className="evidence-grid">
        <div><dt>EURUSD candles</dt><dd className="mono">{totals[0]}</dd></div>
        <div><dt>GBPUSD candles</dt><dd className="mono">{totals[1]}</dd></div>
        <div><dt>Paired UTC opens</dt><dd className="mono">{totals[2]}</dd></div>
        <div><dt>EURUSD quotes</dt><dd className="mono">{totals[3]}</dd></div>
      </dl>
      <div className="research-controls"><label>UTC day <select value={day} disabled={busy} onChange={e=>{setDay(e.target.value);setHour(undefined);setReceipt('');}}>{days.map(d=><option key={d}>{d}</option>)}</select></label></div>
      <div className="table-scroll"><table><thead><tr><th>Hour</th><th>IST start</th><th>Open slots</th><th>EURUSD</th><th>GBPUSD</th><th>Paired</th><th>Bid/ask ticks</th><th>Coverage</th></tr></thead>
        <tbody>{report.hours.filter(h=>dateOf(h.start)===day).map(h=><tr key={h.start}><td><button type="button" className="small-tool" aria-expanded={h.start===hour} disabled={busy}
          onClick={()=>{setHour(h.start);setFile(undefined);setReceipt('');setError('');}}>{utcClock(h.start)} UTC</button></td>
          <td>{istClock(h.start)}{dateOf(h.start+19800)!==day?' (+1d)':''}</td><td>{h.expected}</td><td>{h.eur.count}</td><td>{h.gbp.count}</td><td>{h.paired}</td><td>{h.quotes.count}</td><td><Badge tone={hourlyCoverageTone(h.status)}>{h.status}</Badge></td></tr>)}</tbody></table></div></>}
    {selected&&<section aria-label="Selected hour evidence"><h3>{iso(selected.start).slice(0,16).replace('T',' ')} UTC · {selected.status}</h3>
      {selected.pair_error&&<p>{selected.pair_error}</p>}
      {([['EURUSD',selected.eur],['GBPUSD',selected.gbp]] as const).map(([s,l])=><details key={s}><summary>{s} M5 BID · {l.count} candles · {l.missing.length} missing intervals</summary>
        {l.feed&&<p>Chart feed · {l.feed}</p>}{l.error&&<p>{l.error}</p>}<ul>{intervals(l.missing)}{pins(l.pins)}</ul>
        {l.alternatives?.map(alt=><div key={alt.feed}><p>Alternative · {alt.feed} · {alt.count} candles</p>{alt.error&&<p>{alt.error}</p>}<ul>{pins(alt.pins)}</ul></div>)}</details>)}
      <details><summary>EURUSD bid/ask · {selected.quotes.count} observations · {selected.quotes.gaps.length} unresolved intervals</summary>
        <p>Verified BI5 ticks, modeled zero delivery delay. Intervals use actual timestamp runs, including isolated observations. A fill still requires an eligible order and supporting quotes. Recorded session replay needs bracketing quotes at most {report!.max_gap_ms/1000} seconds apart. Source-scoped entry checks also require the original import and its winning quote. Candle exports supply no executable quote sequence. Intervals below exclude their end.</p>
        {selected.quotes.error&&<p>{selected.quotes.error}</p>}<ul>{intervals(selected.quotes.gaps,true)}{pins(selected.quotes.pins)}</ul></details>
      {selected.paired>0&&!selected.pair_error&&!selected.paired_ranges.length&&<p>Paired candles arrived after this replay interval closed.</p>}
      {selected.paired_ranges.map(([a,b],index)=><button className="btn btn-secondary" key={a} onClick={()=>openReplay({start:a,end:b,coverage_selection:selected.chart_selections[index]})}>Open paired replay · {utcClock(a)}–{utcClock(b)} UTC</button>)}
      {selected.expected>0&&<fieldset className="coverage-fields" disabled={busy}><legend>Recover this interval</legend>
        <label>Import instrument<select value={symbol} onChange={e=>setSymbol(e.target.value as typeof symbol)}><option>EURUSD</option><option>GBPUSD</option></select></label>
        <p>For an hourly BI5 file, verify the instrument and UTC date against your download. Its filename identifies only the hour.</p>
        <label>Local BID M1 CSV (day) or BI5 (hour)<input key={selected.start} type="file" accept=".csv,.bi5" onChange={e=>setFile(e.target.files?.[0])}/></label>
        <button className="btn btn-secondary" disabled={!file} onClick={()=>void act(()=>recover(false))}>Import local file</button>
        {!selected.quotes.registered&&<button className="btn btn-secondary" onClick={()=>void act(()=>recover(true))}>Retry missing EURUSD hour</button>}
        {!selected.quotes.registered&&<button className="btn btn-secondary" onClick={()=>void act(()=>recover(true,true))}>Resume up to four missing hours today</button>}</fieldset>}
    </section>}
  </section></Card>;
}
