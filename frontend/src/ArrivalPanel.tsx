import {useEffect, useRef, useState} from 'react';
import {api} from './api';
import {Card, SectionTitle} from './components';
import type {CandleResponse, Dataset} from './types';

const endpoint = '/market/arrival';
type Mode = 'LEGACY_RESEARCH' | 'STRICT_RECORDED' | 'HISTORICAL_SIMULATION';
type Calendar = {kind: 'continuous' | 'forex_weekly' | 'unknown'};
type ScopeStatus = {feed_id?:string; price_basis?:string; state?:string; last_candle?:number; last_persisted_ms?:number;
  coverage_start?:number; coverage_end?:number; recorded?:number; missing_slots?:number[]; warning?:string};
type Capture = {state:string; timeframe?:string; scopes:Record<string, ScopeStatus>};
type Feed = {symbol:string; timeframe:string; feed_id:string; price_basis:string; source:string;
  start:number; end:number; partitions:{id:string; sha256:string}[]; calendar:Calendar;
  arrival_manifest?:string; delay_seconds:number};
type Manifest = Omit<Feed,'source'|'arrival_manifest'|'delay_seconds'> & {id:string; provider:string; cutoff:number};
type ModeResult = {status:string; eligible_candles:number; unverified_candles:number; missing_slots:number|null; blocking_reason:string|null};
type Assessment = {symbol:string; status:string; modes:Record<string, ModeResult>};
type Readiness = {primary:Assessment; comparison?:Assessment; joint_modes?:string[]};

function fromManifest(manifest:Manifest):Feed {
  const {id,provider,symbol,timeframe,feed_id,price_basis,start,end,partitions,calendar} = manifest;
  return {symbol,timeframe,feed_id,price_basis,start,end,partitions,calendar,
    source:provider+'_SAVED',arrival_manifest:id,delay_seconds:0};
}

export function ArrivalPanel({datasets}:{datasets:Dataset[]}) {
  const [capture,setCapture] = useState<Capture>();
  const [manifests,setManifests] = useState<Manifest[]>([]);
  const [symbols,setSymbols] = useState('EURUSD, GBPUSD');
  const [frame,setFrame] = useState('M5');
  const [schedule,setSchedule] = useState<Calendar['kind']>('forex_weekly');
  const [mode,setMode] = useState<Mode>('LEGACY_RESEARCH');
  const [primary,setPrimary] = useState('');
  const [secondary,setSecondary] = useState('');
  const [delay,setDelay] = useState('0');
  const [secondDelay,setSecondDelay] = useState('0');
  const [cutoff,setCutoff] = useState('');
  const [assessment,setAssessment] = useState<Readiness>();
  const [analysis,setAnalysis] = useState<unknown>();
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const selection = useRef(0);

  useEffect(() => {
    let active = true;
    const status = () => api.get<Capture>(endpoint+'/capture')
      .then(value => {if (active) setCapture(value);})
      .catch(() => {if (active) setCapture(undefined);});
    void status();
    api.get<Manifest[]>(endpoint+'/manifests').then(value => {if (active) setManifests(value);}).catch(() => {});
    const timer = window.setInterval(status,3000);
    return () => {active=false; window.clearInterval(timer);};
  },[]);

  async function run(action:()=>Promise<void>) {
    setError(''); setBusy(true);
    try {await action();}
    catch (reason) {setError(reason instanceof Error ? reason.message : 'Arrival request unavailable');}
    finally {setBusy(false);}
  }

  function selectionChanged(setter:(value:string)=>void,value:string) {
    selection.current++;
    setter(value); setAssessment(undefined); setAnalysis(undefined);
  }

  async function freeze(symbol:string,scope:ScopeStatus) {
    const selected = selection.current;
    const value = await api.post<Manifest>(endpoint+'/manifests',{
      symbol,timeframe:capture?.timeframe,feed_id:scope.feed_id,price_basis:scope.price_basis,
      start:scope.coverage_start,end:scope.coverage_end,
    });
    setManifests(await api.get<Manifest[]>(endpoint+'/manifests'));
    if (selected!==selection.current) return;
    selection.current++;
    setMode('STRICT_RECORDED'); setPrimary(value.id); setSecondary(''); setCutoff(String(value.cutoff));
    setAssessment(undefined); setAnalysis(undefined);
  }

  async function resolveFeed(id:string,seconds:string):Promise<{feed:Feed;cutoff:number}> {
    const manifest = manifests.find(item=>item.id===id);
    if (mode==='STRICT_RECORDED' && manifest) return {feed:fromManifest(manifest),cutoff:manifest.cutoff};
    const dataset = datasets.find(item=>item.id===id);
    const decoded = await api.get<CandleResponse>('/datasets/'+encodeURIComponent(id)+'/coverage');
    if (!dataset?.timeframe || !decoded.coverage || !decoded.feed_id || !decoded.partitions) throw new Error('Archive scope unavailable');
    const {start,end}=decoded.coverage;
    return {cutoff:end+(mode==='HISTORICAL_SIMULATION'?Number(seconds):0),feed:{
      start,end,symbol:dataset.symbol,timeframe:dataset.timeframe,source:decoded.source,
      feed_id:decoded.feed_id,price_basis:decoded.price_basis,partitions:decoded.partitions,
      calendar:{kind:schedule},delay_seconds:mode==='HISTORICAL_SIMULATION'?Number(seconds):0,
    }};
  }

  async function examine(evaluate:boolean) {
    const selected = selection.current;
    const first = await resolveFeed(primary,delay);
    const second = secondary ? await resolveFeed(secondary,secondDelay):undefined;
    const asOf = cutoff!=='' ? Number(cutoff) : !second ? first.cutoff :
      mode==='STRICT_RECORDED' ? Math.min(first.cutoff,second.cutoff):Math.max(first.cutoff,second.cutoff);
    const pair={primary:first.feed,comparison:second?.feed,cutoff:asOf};
    const readiness = await api.post<Readiness>(endpoint+'/readiness',pair);
    if (selected!==selection.current) return;
    setAssessment(readiness);
    if (!evaluate) return;
    let result:unknown;
    if (second && mode!=='LEGACY_RESEARCH') {
      result=await api.post('/indicators/smt/strict',{...pair,availability_mode:mode});
    } else {
      const {start,end,...feed}=first.feed;
      result=await api.post('/indicators/evaluate',{
        ...feed,cutoff:asOf,availability_start:start,availability_mode:mode,frozen:true,candles:[],
        indicators:[{id:'market_structure',structure:{algorithm_version:2,market_schedule:feed.calendar,
          history_bars:10000,history_start:start}},{id:'fair_value_gap',fvg:{
          history_bars:10000,history_start:start,market_schedule:feed.calendar}}],
      });
    }
    if (selected===selection.current) setAnalysis(result);
  }

  function download() {
    const content = new Blob([JSON.stringify({availability_mode:mode,assessment,analysis},null,2)],
                             {type:'application/json'});
    const url = URL.createObjectURL(content);
    const link = document.createElement('a');
    link.href=url; link.download='tradeforge-frozen-evidence.json'; link.click();
    window.setTimeout(()=>URL.revokeObjectURL(url),0);
  }

  const options = mode==='STRICT_RECORDED' ? manifests.map(value=>({id:value.id,
    label:`${value.symbol} · ${value.timeframe} · ${new Date(value.end*1000).toISOString()}`})) :
    datasets.filter(value=>value.kind==='candles').map(value=>({id:value.id,
      label:`${value.symbol} · ${value.timeframe} · ${value.id.slice(0,10)}`}));
  const running = capture && !['STOPPED','FAILED'].includes(capture.state);
  const validDelay = [delay,secondDelay].every(value=>value!=='' && Number.isInteger(Number(value)) && Number(value)>=0 && Number(value)<=86400);
  return <Card>
    <SectionTitle title="Recorded candle capture and readiness"/>
    <small role="status">{capture?.state??'Capture status unavailable'}</small>
    <div className="two-column">
      <div>
        <label className="field">MT5 symbols <input disabled={Boolean(running)} value={symbols} onChange={event=>setSymbols(event.target.value)}/></label>
        <label className="field">Capture timeframe <select disabled={Boolean(running)} value={frame} onChange={event=>setFrame(event.target.value)}>
          <option>M1</option><option>M5</option>
        </select></label>
        <label className="field">Market hours <select value={schedule} onChange={event=>selectionChanged(
          value=>setSchedule(value as Calendar['kind']),event.target.value)}>
          <option value="forex_weekly">Forex weekly · New York</option><option value="continuous">Continuous</option>
          <option value="unknown">Unknown</option>
        </select></label>
        {running ? <button type="button" className="btn btn-secondary" disabled={busy}
          onClick={()=>void run(async()=>setCapture(await api.post<Capture>(endpoint+'/capture/stop',{})))}>Stop candle capture</button> :
          <button type="button" className="btn btn-primary" disabled={busy || !symbols.trim() || schedule==='unknown'}
            onClick={()=>void run(async()=>setCapture(await api.post<Capture>(endpoint+'/capture/start',{
              symbols:symbols.split(',').map(value=>value.trim()).filter(Boolean),timeframe:frame,calendar:{kind:schedule},
            })))}>Start read-only candle capture</button>}
        {Object.entries(capture?.scopes??{}).map(([symbol,scope])=><div key={symbol}>
          <strong>{symbol} · {scope.state??'STARTING'}</strong>
          <p>{scope.feed_id && <span>Feed {scope.feed_id} · {scope.price_basis} · </span>}
          {scope.recorded??0} recorded slots · {scope.missing_slots?.length??0} overdue slots {scope.warning && ` · ${scope.warning}`}</p>
          {scope.last_candle!==undefined && scope.last_candle!==null && <p>Last candle {new Date(scope.last_candle*1000).toISOString()}</p>}
          {scope.last_persisted_ms && <p>Last durable write {new Date(scope.last_persisted_ms).toISOString()}</p>}
          {scope.recorded!==undefined && scope.recorded>0 && <button type="button" className="small-tool" disabled={busy}
            onClick={()=>void run(()=>freeze(symbol,scope))}>Freeze {symbol} coverage</button>}
        </div>)}
      </div>
      <div>
        <label className="field">Availability mode <select value={mode} onChange={event=>{
          selection.current++;
          setMode(event.target.value as Mode); setPrimary(''); setSecondary(''); setCutoff('');
          setAssessment(undefined); setAnalysis(undefined);
        }}>
          <option value="LEGACY_RESEARCH">Legacy research</option>
          <option value="STRICT_RECORDED">Strict recorded</option>
          <option value="HISTORICAL_SIMULATION">Historical simulation · assumed availability</option>
        </select></label>
        <label className="field">Primary frozen evidence <select value={primary}
          onChange={event=>selectionChanged(setPrimary,event.target.value)}>
          <option value="">Choose evidence</option>{options.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}
        </select></label>
        {mode!=='LEGACY_RESEARCH' && <label className="field">Comparison frozen evidence <select value={secondary}
          onChange={event=>selectionChanged(setSecondary,event.target.value)}>
          <option value="">No comparison</option>{options.filter(item=>item.id!==primary).map(item=>
            <option key={item.id} value={item.id}>{item.label}</option>)}
        </select></label>}
        {mode==='HISTORICAL_SIMULATION' && <>
          <label className="field">Primary modeled delay (seconds) <input type="number" min={0} max={86400}
            value={delay} onChange={event=>selectionChanged(setDelay,event.target.value)}/></label>
          {secondary && <label className="field">Comparison modeled delay (seconds) <input type="number" min={0} max={86400}
            value={secondDelay} onChange={event=>selectionChanged(setSecondDelay,event.target.value)}/></label>}
        </>}
        <label className="field">As-of cutoff (UTC epoch seconds) <input type="number" min={0} value={cutoff}
          onChange={event=>selectionChanged(setCutoff,event.target.value)} placeholder="Frozen cutoff"/></label>
        <button type="button" className="small-tool" disabled={busy || !primary || !validDelay}
          onClick={()=>void run(()=>examine(false))}>Assess data readiness</button>
        <button type="button" className="small-tool" disabled={busy || !primary || !validDelay}
          onClick={()=>void run(()=>examine(true))}>Evaluate frozen evidence</button>
        {assessment && [assessment.primary,assessment.comparison].filter(Boolean).map(item=>item && <div key={item.symbol}>
          <p><strong>{item.symbol} · {item.status}</strong></p>
          {Object.entries(item.modes).map(([key,result])=><small key={key}>{key} · {result.eligible_candles} eligible candles · {
            result.missing_slots??'Unknown'} missing slots {result.blocking_reason && ` · ${result.blocking_reason}`}<br/></small>)}
        </div>)}
        {assessment?.joint_modes && <p>Joint availability · {assessment.joint_modes.join(', ') || 'Incompatible'}</p>}
        {analysis!==undefined && <details><summary>Analytical evidence · {mode}</summary>
          <button type="button" className="small-tool" onClick={download}>Download evidence JSON</button>
          <pre>{JSON.stringify(analysis,null,2)}</pre></details>}
      </div>
    </div>
    {error && <small role="alert">{error}</small>}
  </Card>;
}
