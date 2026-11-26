import {useEffect,useRef,useState} from 'react';
import {api} from './api';

type PackageRow={id:string;knowledge_cutoff:string};
type Quote={time_ms:number;ordinal:number;source_sha256:string;partition:{id:string;sha256:string};bid_units:number;ask_units:number};
type Event={id:string;kind:string;quote:Quote};
type Metric={metric_id:string;value:string;unit:string;entry_ref:string;exit_ref:string;available_at_ms:number};
type Contents={manifest:{package_id:string;knowledge_cutoff_utc:string};template:unknown;evidence:{metrics:Record<string,Metric>;events:Event[];unavailable:Record<string,string>}};
type Decision={sequence:number;decision:string;rationale:string};
type Original={package_id:string;comment:string;claims:{metric_id:string;value:string;unit:string}[]};
type LoadedReview={packageId:string;id:string;seq:number;decisions:Decision[];original?:Original};
import type {Review} from './types';

export function RecordedPackageReview({route,initialCutoff}:{route:string;initialCutoff:number}){
  const [selectedCutoff,setCutoff]=useState<string>();
  const cutoff=selectedCutoff??new Date(initialCutoff).toISOString();
  const [history,setHistory]=useState<PackageRow[]>([]),[pkg,setPkg]=useState<Contents>();
  const [input,setInput]=useState(''),[reviews,setReviews]=useState<Review[]>([]);
  const [loaded,setLoaded]=useState<LoadedReview>();
  const [choice,setChoice]=useState('NEEDS_FOLLOW_UP'),[rationale,setRationale]=useState('');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[verified,setVerified]=useState('');
  useEffect(()=>{let active=true;api.get<PackageRow[]>(route+'/packages').then(r=>{if(active)setHistory(r)}).catch(e=>{if(active)setError(String(e))});return()=>{active=false}},[route]);
  // All interactive requests run serially. A changed replay session remounts this pane.
  const working=useRef(false);
  async function work(fn:()=>Promise<unknown>){
    if(working.current)return;
    working.current=true;setBusy(true);setError('');
    try{await fn()}catch(e){setError(String(e))}
    finally{working.current=false;setBusy(false)}
  }
  function clearReview(){setLoaded(undefined);setRationale('');setChoice('NEEDS_FOLLOW_UP')}
  async function chooseReview(packageId:string,id:string,list:Review[]){
    clearReview();
    const row=list.find(r=>r.id===id&&r.package_id===packageId);
    if(!row)throw Error('Review does not belong to this package.');
    const [log,original]=await Promise.all([
      api.get<{current_sequence:number;decisions:Decision[]}>('/ai/reviews/'+id+'/decisions'),
      row.original_integrity==='UNAMBIGUOUS_JSON'?api.get<Original>('/ai/reviews/'+id+'/original'):Promise.resolve(undefined)]);
    if(original&&original.package_id!==packageId)throw Error('Original review does not belong to this package.');
    setLoaded({packageId,id,seq:log.current_sequence,decisions:log.decisions,original});
  }
  async function open(id:string,preferred?:string){
    clearReview();setPkg(undefined);setReviews([]);setVerified('');setInput('');
    const [p,all]=await Promise.all([api.get<Contents>('/ai/packages/'+id+'/recorded'),api.reviews()]);
    if(p.manifest.package_id!==id)throw Error('Evidence differs from the selected package.');
    const matching=all.filter(r=>r.package_id===id);
    setPkg(p);setReviews(matching);setInput(JSON.stringify(p.template,null,2));
    const next=preferred??matching[0]?.id;
    if(next)await chooseReview(id,next,matching);
  }
  async function upload(body:string|File){
    const packageId=pkg?.manifest.package_id;
    const raw=typeof body==='string'?body:await body.text();
    if(!packageId||JSON.parse(raw).package_id!==packageId)throw Error('Select the package cited by this review.');
    const saved=await api.importReview(body);await open(packageId,saved.id);
  }
  const current=loaded&&loaded.packageId===pkg?.manifest.package_id?loaded:undefined;
  const review=current?.id??'',saved=current?.original;
  const selected=reviews.find(r=>r.id===review);
  return <section aria-label="Recorded replay evidence"><h3>Recorded replay evidence</h3>
    <fieldset disabled={busy}><label>Evidence cutoff (UTC ISO timestamp)<input value={cutoff} onChange={e=>setCutoff(e.target.value)}/></label>
    <button onClick={()=>void work(async()=>{const ms=Date.parse(cutoff);
      if(!Number.isFinite(ms)||new Date(ms).toISOString()!==cutoff)throw Error('Use YYYY-MM-DDTHH:mm:ss.sssZ');
      const result=await api.post<{package_id:string}>(route+'/packages?cutoff_ms='+ms,{});
      setHistory(await api.get(route+'/packages'));await open(result.package_id)})}>Export recorded evidence</button>
    <label>Saved evidence package<select value={pkg?.manifest.package_id??''} onChange={e=>void work(()=>open(e.target.value))}>
      <option disabled value="">Select package</option>{history.map(p=><option key={p.id} value={p.id}>{p.knowledge_cutoff} · {p.id}</option>)}</select></label>
    {pkg&&<><p>Simulated execution · recorded BID/ASK · modeled zero delivery delay</p>
    <a href={'/research/api/ai/packages/'+pkg.manifest.package_id+'/download'}>Download evidence ZIP</a>
    {Object.values(pkg.evidence.metrics).map(m=><div id={m.metric_id} key={m.metric_id}><strong>{m.value} {m.unit}</strong> · available {new Date(m.available_at_ms).toISOString()}
      {[m.entry_ref,m.exit_ref].map(ref=>{const e=pkg.evidence.events.find(v=>v.id===ref)!;return <button key={ref} onClick={()=>void work(async()=>{
        const q=await api.get<Quote>(route+'/quote?time_ms='+e.quote.time_ms+'&ordinal='+e.quote.ordinal);
        if(q.time_ms!==e.quote.time_ms||q.ordinal!==e.quote.ordinal||q.source_sha256!==e.quote.source_sha256||q.partition.id!==e.quote.partition.id||q.partition.sha256!==e.quote.partition.sha256||q.bid_units!==e.quote.bid_units||q.ask_units!==e.quote.ask_units)throw Error('Source differs from package');
        setVerified(JSON.stringify(q))})}>Verify {e.kind} quote {e.quote.ordinal}</button>})}</div>)}
    {verified&&<output>{verified}</output>}
    {Object.entries(pkg.evidence.unavailable).map(([k,v])=><p key={k}>{k} · {v}</p>)}
    <details><summary>Frozen manifest, metric IDs and quote records</summary><pre>{JSON.stringify({manifest:pkg.manifest,evidence:pkg.evidence},null,2)}</pre></details>
    <label>Local review JSON<textarea value={input} onChange={e=>setInput(e.target.value)} rows={6}/></label>
    <button onClick={()=>void work(()=>upload(input))}>Import recorded review</button>
    <label>Upload original review file<input type="file" accept=".json" onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void work(()=>upload(file))}}/></label>
    {reviews.length>0&&<><label>Imported review<select value={review} onChange={e=>void work(()=>chooseReview(pkg.manifest.package_id,e.target.value,reviews))}><option disabled value="">Select review</option>{reviews.map(r=><option key={r.id} value={r.id}>{r.assessment} · {r.id}</option>)}</select></label>
    {selected?.original_integrity==='DUPLICATE_JSON_KEY'&&<><p role="alert">Ambiguous historical original · duplicate JSON key {selected.duplicate_key}</p>
    <button onClick={()=>setInput(JSON.stringify({...pkg.template as object,corrects_review_id:review},null,2))}>Prepare linked correction</button></>}
    {selected?.corrects_review_id&&<p>Corrects original {selected.corrects_review_id}</p>}
    {saved&&<div><p>{saved.comment}</p>{saved.claims.map((claim,i)=><p key={i}><a href={'#'+claim.metric_id} onClick={e=>{e.preventDefault();document.getElementById(claim.metric_id)?.scrollIntoView()}}>Trace claim {claim.value} {claim.unit}</a></p>)}</div>}
    {current&&<><a href={'/research/api/ai/reviews/'+review+'/original'}>Original review bytes</a>
    {current.decisions.map(d=><p key={d.sequence}>{d.sequence} · {d.decision} · {d.rationale}</p>)}
    <label>Operator decision<select value={choice} onChange={e=>setChoice(e.target.value)}>{['ACCEPTED','REJECTED','NEEDS_FOLLOW_UP'].map(s=><option key={s}>{s}</option>)}</select></label>
    <label>Decision rationale<input value={rationale} onChange={e=>setRationale(e.target.value)}/></label>
    <button disabled={!review||!rationale.trim()} onClick={()=>void work(async()=>{await api.post('/ai/reviews/'+review+'/decisions',{decision:choice,rationale,expected_sequence:current.seq});await chooseReview(pkg.manifest.package_id,review,reviews)})}>Record operator decision</button></>}</>}</>}
    </fieldset>{error&&<p role="alert">{error}</p>}</section>;
}
