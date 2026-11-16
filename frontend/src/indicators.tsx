import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { Candle, CandleResponse, ChartSource, Timeframe } from "./types";
import { useWorkspacePreference } from "./useWorkspacePreference";

type Window = { timezone: string; start: string; end: string };
type Schedule = {kind: "continuous" | "forex_weekly" | "unknown"; timezone?: string;
  sunday_open?: string; friday_close?: string};
type Structure = {algorithm_version?: 1 | 2; market_schedule?: Schedule; left: number; right: number; price_source: "wick" | "close";
  ties: "strict" | "earliest"; tolerance_bps: number; break_bps: number; wick_candidates: boolean};
type Collection = "pivots" | "breaks" | "pools" | "sweeps" | "current_pools" | "formations" | "revisions" | "current_fvgs";
type FvgRules = {min_price:number;min_bps:number;min_ticks:number;tick_size?:number|null;tick_size_reference?:string|null;
  atr_multiple:number;atr_period:number;displacement:"none"|"directional_body_bps_v1";min_body_bps:number;
  mitigation:"wick"|"close";max_age_bars:number;market_schedule:Schedule;history_bars?:2000|10000;history_start?:number|null};
type FvgVisibility={bullish:boolean;bearish:boolean;active:boolean;partial:boolean;full:boolean;invalidated:boolean;expired:boolean};
const fvgVisibility:FvgVisibility={bullish:true,bearish:true,active:true,partial:true,full:true,invalidated:true,expired:true};
export type FvgEvent = {id:string;fvg_id:string;identity_policy_version?:number;direction:"BULLISH"|"BEARISH";
  state:"ACTIVE"|"PARTIALLY_MITIGATED"|"FULLY_MITIGATED"|"INVALIDATED"|"EXPIRED";
  lower:number;upper:number;remaining_lower:number;remaining_upper:number;mitigation_pct:number;
  formation_ohlc?:number[][];
  a_time:number;b_time:number;c_time:number;observation_time:number;formation_confirmation_time:number;
  confirmation_time:number;available_at:number;reason:string;warnings:string[]};
type Liquidity = {equality_bps:number; penetration_bps:number; reclaim_bps:number;
  min_touches:number; max_touches:number; min_separation_bars:number; max_age_bars:number};
type LiquidityVisibility = {zones:boolean; sweeps:boolean; candidates:boolean; terminal:boolean};
const liquidityVisibility: LiquidityVisibility={zones:true,sweeps:true,candidates:true,terminal:true};
export type LiquidityEvent = {
  id:string; pool_id:string; pool_revision_id?:string; state:string; lower:number; upper:number; liquidity_side:"BSL"|"SSL";
  activated_at:number|null; activation_observation_time?:number|null; observation_time:number; confirmation_time:number; available_at:number;
  status:string; constituents?:{id:string; price:number; time:number; available_at:number}[];
  related_structure_ids?:string[]; ohlc?:{open:number; high:number; low:number; close:number};
};
type PageInfo = {total: number; returned: number; start: number; end: number; next_cursor: string | null};
type OlderPage = {key: string; collection: Collection; metadata: PageInfo; events: (StructureEvent | LiquidityEvent | FvgEvent)[]};
export type CurrentLiquidityPool = Omit<LiquidityEvent,"id"> & {revision_id:string; expiry_in_bars:number};
type StructureVisibility = {swings: boolean; breaks: boolean; candidates: boolean};
export type StructureEvent = {
  id: string; type: string; side: "HIGH" | "LOW"; status: "confirmed" | "provisional" | "invalidated";
  available_at: number; confirmation_time: number | null; confirmation_bar_time?: number;
  price?: number; classification?: string; direction?: "BULLISH" | "BEARISH";
  break_time?: number; break_price?: number; level_time?: number; level_price?: number;
};
const structureVisibility: StructureVisibility = {swings: true, breaks: true, candidates: true};

type Definition = { id: string; label: string; period: number | null; timeframes: Timeframe[];
  parameters: { session?: Window; schedule?: Schedule["kind"][]; structure?: Structure; liquidity?: Liquidity; fvg?: FvgRules } };
type Settings = { id: string; period?: string; session?: Window; schedules?: Record<string, Schedule>; structure?: Structure; visibility?: StructureVisibility; liquidity?: Liquidity; liquidityVisibility?: LiquidityVisibility; fvg?: FvgRules; fvgVisibility?: FvgVisibility; tickSize?: Record<string,{size:number;reference:string}> };
type Point = { time: number; value: number; pivot_time?: number; status: "confirmed" | "provisional" };
export type IndicatorResult = {
  id: string;
  render: "overlay" | "pane" | "markers";
  warmup_bars: number | null;
  unit: "price" | "percent";
  warnings: string[];
  pivots?: StructureEvent[];
  breaks?: StructureEvent[];
  structure_state?: string;
  event_page?: {snapshot: string; pivots: PageInfo; breaks: PageInfo};
  provenance?: {context_authority?: string};
  continuity?: {schedule_authority: string; counts: Record<string, number>};
  visibility?: StructureVisibility;
  liquidityVisibility?: LiquidityVisibility;
  fvgVisibility?: FvgVisibility;
  formations?: FvgEvent[];
  revisions?: FvgEvent[];
  fvg_page?: {formations:PageInfo; revisions:PageInfo};
  current_fvgs?: PageInfo & {truncated:boolean;items:FvgEvent[]};
  pools?: LiquidityEvent[];
  sweeps?: LiquidityEvent[];
  liquidity_page?: {snapshot:string; pools:PageInfo; sweeps:PageInfo};
  current_pools?: PageInfo & {snapshot:string; truncated:boolean; items:CurrentLiquidityPool[]};
  coverage?: {status: string; confirmation_status: "confirmed" | "provisional" | "unavailable"};
  series: Record<string, Point[]>;
};

const coverageLabels: Record<string, string> = {
  COMPLETE: "Confirmed previous range",
  UNKNOWN_SCHEDULE: "Unknown trading schedule",
  SOURCE_INTEGRITY_FAILURE: "Source integrity failure",
  SOURCE_MISMATCH: "Source identity mismatch",
  MISSING_OPEN_CANDLES: "Insufficient history: expected market-open candles are missing",
  REFERENCE_ABSENT: "Insufficient history: reference period absent",
  MARKET_CLOSED: "Market closed throughout reference period",
  HISTORY_UNAVAILABLE: "Historical context unavailable",
  HISTORY_LIMIT: "Historical context exceeds read limits",
  UNALIGNED_MARKET_BOUNDARY: "Timeframe crosses a market boundary",
  SCHEDULE_MISMATCH: "Observed bars conflict with the selected schedule",
};

const preference = "tradeforge.workspace.indicators-v1";
const emptyResults: IndicatorResult[] = [];
function stored(): Settings[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(preference) ?? "[]");
    if (Array.isArray(value)) return value.filter((item): item is Settings =>
      Boolean(item && typeof item.id === "string" && item.id.length <= 40)).slice(0, 8);
  } catch { /* Use the primary indicator setting when storage is unavailable. */ }
  return [];
}

export function useChartIndicator(enabled: boolean, candles: Candle[], source: ChartSource, timeframe: Timeframe,
  symbol: string, cutoff: number, feed?: CandleResponse, frozen = false, request?: {indicator?: string; sequence: number}) {
  const [selected, setSelected] = useWorkspacePreference<string>("indicator", "none");
  const [periodText, setPeriod] = useWorkspacePreference<string>("indicator-period", "");
  const [settings, setSettings] = useState(stored);
  const [search, setSearch] = useState("");
  const [candidate, setCandidate] = useState("");
  const [definitions, setDefinitions] = useState<Definition[]>([]);
  const [libraryError, setLibraryError] = useState("");
  const [older, setOlder] = useState<OlderPage>();
  const [pageRequest, setPageRequest] = useState<{key: string; collection: Collection; cursor: string}>();
  const [pageError, setPageError] = useState("");
  const [selectedFvg,setSelectedFvg]=useState<{key:string;id:string}>();
  const [selectedLiquidity,setSelectedLiquidity]=useState<{key:string; id:string}>();
  const [response, setResponse] = useState<{key: string; results?: IndicatorResult[]; error?: string}>();
  useEffect(() => { if (request?.indicator) { setSelected(request.indicator); setPeriod(""); } },
    [request, setSelected, setPeriod]);
  useEffect(() => {
    try { localStorage.setItem(preference, JSON.stringify(settings)); } catch { /* Keep editing in memory. */ }
  }, [settings]);

  const scheduleScope = JSON.stringify([source, symbol, feed?.feed_id, feed?.price_basis]);
  const fvgSelectionScope = JSON.stringify([scheduleScope,timeframe,feed?.dataset_id,frozen]);
  const primary = definitions.find(item => item.id === selected && item.period !== null);
  const active = useMemo(() => {
    const config = primary ? [{id: primary.id, period: periodText}, ...settings.filter(item => item.id !== primary.id)] : settings;
    return config.slice(0, 8).flatMap(item => {
      const definition = definitions.find(row => row.id === item.id && row.timeframes.includes(timeframe));
      if (!definition) return [];
      const period = item.period ? Number(item.period) : definition.period;
      if (period !== null && !(Number.isInteger(period) && period >= 2 && period <= 200)) return [];
      const session = item.session ?? definition.parameters.session;
      if (session && (!session.timezone || !session.start || !session.end || session.start === session.end)) return [];
      const schedule = definition.parameters.schedule ? item.schedules?.[scheduleScope] : undefined;
      return [{id: item.id, ...(period !== null ? {period} : {}), ...(session ? {session} : {}), ...(schedule ? {schedule} : {}), ...(item.structure ? {structure:item.structure.algorithm_version===2?
        {...item.structure,market_schedule:item.schedules?.[scheduleScope]}:item.structure} : {}), ...(item.liquidity ? {liquidity:item.liquidity} : {}),
        ...(definition.parameters.fvg?{fvg:{...definition.parameters.fvg,...item.fvg,
          market_schedule:item.schedules?.[scheduleScope]??{kind:"unknown"},
          ...(item.fvg?.history_bars===10000&&(!source.endsWith("_SAVED")||item.fvg.history_start==null)
            ?{history_bars:2000,history_start:null}:{}),
          tick_size:item.tickSize?.[scheduleScope]?.size&&item.tickSize?.[scheduleScope]?.reference?item.tickSize[scheduleScope].size:undefined,
          tick_size_reference:item.tickSize?.[scheduleScope]?.size&&item.tickSize?.[scheduleScope]?.reference?item.tickSize[scheduleScope].reference:undefined}}:{})}];
    });
  }, [definitions, periodText, primary, scheduleScope, settings, timeframe]);
  const body = useMemo(() => enabled && active.length && (candles.length || active.some(item => item.id.startsWith("previous_")))
    ? JSON.stringify({source, timeframe, symbol, cutoff, frozen, load_reference: true, feed_id: feed?.feed_id,
        dataset_id: feed?.dataset_id, partitions: feed?.partitions, price_basis: feed?.price_basis, indicators: active, candles: candles.slice(-2000)})
    : "", [active, candles, cutoff, enabled, frozen, feed?.dataset_id, feed?.feed_id, feed?.price_basis, feed?.partitions, source, symbol, timeframe]);

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    api.get<Definition[]>("/indicators").then(items => {
      if (active) { setDefinitions(items); setLibraryError(""); }
    }).catch(error => {
      if (active) setLibraryError(error.message);
    });
    return () => { active = false; };
  }, [enabled]);

  useEffect(() => {
    setOlder(undefined);
    setPageRequest(undefined);
    setPageError("");
    if (!body) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const result = await fetch("/research/api/indicators/evaluate", {
          method: "POST", headers: {"Content-Type": "application/json"}, body, signal: controller.signal,
        });
        const data = await result.json();
        if (!result.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Invalid indicator settings");
        if (!controller.signal.aborted) setResponse({key: body, results: data.results});
      } catch (error) {
        if (!controller.signal.aborted) setResponse({key: body, error: (error as Error).message});
      }
    }, 80);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [body]);

  useEffect(() => {
    if (!body || pageRequest?.key !== body) return;
    const controller = new AbortController();
    const {collection, cursor}=pageRequest;
    const input=JSON.parse(body);
    const current=collection==="current_pools"||collection==="current_fvgs";
    const liquid=collection==="current_pools"||collection==="pools"||collection==="sweeps";
    const fvg=collection==="current_fvgs"||collection==="formations"||collection==="revisions";
    const spec=input.indicators.find((item:{id:string})=>item.id===(liquid?"liquidity_sweeps":fvg?"fair_value_gap":"market_structure"));
    const first=liquid?"pool":fvg?"formation":"pivot", second=liquid?"sweep":fvg?"revision":"break";
    const isFirst=collection==="pivots"||collection==="pools"||collection==="formations";
    input.indicators=[{...spec,[liquid?"liquidity_delivery":fvg?"fvg_delivery":"structure_delivery"]:{
      [first+"_limit"]:!current&&isFirst?40:0,[second+"_limit"]:!current&&!isFirst?40:0,
      ...(liquid||fvg?{current_limit:current?40:0}:{}),
      [(current?"current":isFirst?first:second)+"_cursor"]:cursor,
    }}];
    void (async()=>{
      try{
        const response=await fetch("/research/api/indicators/evaluate",{
          method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(input),signal:controller.signal,
        });
        const value=await response.json();
        if(!response.ok)throw Error(typeof value.detail==="string"?value.detail:"Historical event page unavailable");
        const result=value.results[0] as IndicatorResult;
        const metadata=collection==="current_fvgs"?result.current_fvgs:
          collection==="formations"||collection==="revisions"?result.fvg_page?.[collection]:
          collection==="current_pools"?result.current_pools:
          collection==="pools"||collection==="sweeps"?result.liquidity_page?.[collection]:result.event_page?.[collection];
        const events=collection==="current_fvgs"?result.current_fvgs?.items:collection==="current_pools"?result.current_pools?.items.map(pool=>({...pool,id:pool.revision_id})):
          result[collection];
        if(!metadata)throw Error("Historical event page missing");
        if(!controller.signal.aborted)setOlder({key:body,collection,metadata,events:events??[]});
      }catch(error){
        if(!controller.signal.aborted)setPageError((error as Error).message);
      }finally{
        if(!controller.signal.aborted)setPageRequest(undefined);
      }
    })();
    return ()=>controller.abort();
  },[body,pageRequest]);

  const current = response?.key === body ? response : undefined;
  const visibility = settings.find(item => item.id === "market_structure")?.visibility ?? structureVisibility;
  const liquidVisibility=settings.find(item=>item.id==="liquidity_sweeps")?.liquidityVisibility??liquidityVisibility;
  const gapVisibility=settings.find(item=>item.id==="fair_value_gap")?.fvgVisibility??fvgVisibility;
  const data = useMemo(() => current?.results?.map(item => item.id === "market_structure" ?
    {...item, visibility} : item.id==="liquidity_sweeps"?{...item,liquidityVisibility:liquidVisibility}:item.id==="fair_value_gap"?{...item,fvgVisibility:gapVisibility}:item) ?? emptyResults,
    [current, visibility, liquidVisibility, gapVisibility]);
  const warnings = [...new Set(data.filter(row => !row.coverage).flatMap(row => row.warnings))];
  const choices = definitions.filter(item => item.timeframes.includes(timeframe) && item.id !== selected &&
    !settings.some(config => config.id === item.id) && (item.label + " " + item.id).toLowerCase().includes(search.toLowerCase()));
  function update(id: string, patch: Partial<Settings>) {
    setSettings(items => items.map(item => item.id === id ? {...item, ...patch} : item));
  }
  const controls = enabled && <div className="indicator-controls">
    <label>Indicator <select value={primary ? selected : "none"} onChange={event => {
      setSelected(event.target.value); setPeriod("");
    }}>
      <option value="none">None</option>
      {definitions.filter(item => item.period !== null).map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
    </select></label>
    {primary && <label>Period <input type="number" min={2} max={200} step={1}
      value={periodText} placeholder={String(primary.period)} onChange={event => setPeriod(event.target.value)}/></label>}
    <details className="indicator-library">
      <summary>Indicator library</summary>
      <label>Search indicators <input type="search" value={search} onChange={event => {setSearch(event.target.value); setCandidate("");}} /></label>
      <label>Add indicator <select value={candidate} onChange={event => setCandidate(event.target.value)}>
        <option value="">Choose indicator</option>
        {choices.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select></label>
      <button type="button" className="small-tool" disabled={!choices.some(item => item.id === candidate) || settings.length + Number(Boolean(primary)) >= 8}
        onClick={() => { setSettings(items => [...items, {id: candidate}]); setCandidate(""); }}>Enable indicator</button>
      {settings.filter(item => item.id !== selected).map(item => {
        const definition = definitions.find(row => row.id === item.id);
        if (!definition) return null;
        const session = item.session ?? definition.parameters.session;
        const schedule: Schedule = item.schedules?.[scheduleScope] ?? {kind: source === "DEMO" || source === "BINANCE_SPOT" ? "continuous" : "unknown"};
        const updateSchedule = (patch: Partial<Schedule>) => update(item.id,
          {schedules:{...item.schedules,[scheduleScope]:{...schedule,...patch}}});
        return <fieldset key={item.id}>
          <legend>{definition.label}</legend>
          {definition.period !== null && <label>Period <input type="number" min={2} max={200} step={1}
            value={item.period ?? ""} placeholder={String(definition.period)} onChange={event => update(item.id,{period:event.target.value})}/></label>}
          {session && <>
            <label>Timezone <input value={session.timezone} onChange={event => update(item.id, {session:{...session, timezone:event.target.value}})}/></label>
            <label>Start <input type="time" value={session.start} onChange={event => update(item.id, {session:{...session, start:event.target.value}})}/></label>
            <label>End <input type="time" value={session.end} onChange={event => update(item.id, {session:{...session, end:event.target.value}})}/></label>
          </>}
          {definition.parameters.structure && (() => {
            const rules=item.structure ?? definition.parameters.structure;
            const version=rules.algorithm_version??1;
            const market=item.schedules?.[scheduleScope] ?? {kind:"unknown"} as Schedule;
            const setMarket=(patch:Partial<Schedule>)=>update(item.id,
              {schedules:{...item.schedules,[scheduleScope]:{...market,...patch}}});
            const patch=(value: Partial<Structure>)=>update(item.id,{structure:{...rules,...value}});
            const numbers: [keyof Pick<Structure,"left"|"right"|"tolerance_bps"|"break_bps">,string,number,number][]=[
              ["left","Left bars",1,30],["right","Right confirmation bars",1,30],
              ["tolerance_bps","Pivot tolerance (bps)",0,100],["break_bps","Break tolerance (bps)",0,100],
            ];
            const result=data.find(row=>row.id===item.id);
            const recent=[...(result?.pivots??[]),...(result?.breaks??[])]
              .sort((left,right)=>right.available_at-left.available_at).slice(0,6);
            return <>
              <label>Structure algorithm <select value={version}
                onChange={event=>patch({algorithm_version:Number(event.target.value) as 1|2})}>
                <option value={1}>v1 · reset at every gap</option><option value={2}>v2 · calendar continuity</option>
              </select></label>
              {version===2 && <>
                <label>Structure market schedule <select value={market.kind}
                  onChange={event=>setMarket({kind:event.target.value as Schedule["kind"]})}>
                  <option value="unknown">Unknown</option><option value="continuous">Continuous (24/7)</option>
                  <option value="forex_weekly">Configured Sunday–Friday</option>
                </select></label>
                {market.kind==="forex_weekly" && <>
                  <label>Structure timezone <input value={market.timezone??"America/New_York"}
                    onChange={event=>setMarket({timezone:event.target.value})}/></label>
                  <label>Structure Sunday open <input type="time" value={market.sunday_open??"17:00"}
                    onChange={event=>setMarket({sunday_open:event.target.value})}/></label>
                  <label>Structure Friday close <input type="time" value={market.friday_close??"17:00"}
                    onChange={event=>setMarket({friday_close:event.target.value})}/></label>
                </>}
              </>}
              {numbers.map(([key,label,min,max])=><label key={key}>{label}
                <input type="number" min={min} max={max} step={min===1?1:0.1} value={rules[key]}
                  onChange={event=>{const value=event.target.valueAsNumber;
                    if(value>=min&&value<=max)patch({[key]:value});}}/>
              </label>)}
              <label>Pivot price <select value={rules.price_source}
                onChange={event=>patch({price_source:event.target.value as Structure["price_source"]})}>
                <option value="wick">Wick</option><option value="close">Close</option>
              </select></label>
              <label>Pivot ties <select value={rules.ties} onChange={event=>patch({ties:event.target.value as Structure["ties"]})}>
                <option value="strict">Strict</option><option value="earliest">Earliest</option>
              </select></label>
              {!definition.parameters.liquidity && <label><input type="checkbox" checked={rules.wick_candidates}
                onChange={event=>patch({wick_candidates:event.target.checked})}/> Calculate wick candidates</label>}
              {!definition.parameters.liquidity && (["swings","breaks","candidates"] as const).map(key=><label key={key}>
                <input type="checkbox" checked={visibility[key]}
                  onChange={event=>update(item.id,{visibility:{...visibility,[key]:event.target.checked}})}/>
                Show {key}
              </label>)}
              {result?.structure_state && <small role="status">Structure {result.structure_state?.toLowerCase()}</small>}
              {result?.continuity && <small>
                {result.continuity.schedule_authority==="UNKNOWN"?"Trading hours unknown":"Configured trading hours (assumption)"}
                {result.provenance?.context_authority==="CHECKSUM_VERIFIED_ARCHIVE"?" · Archive checksums verified":
                  result.provenance?.context_authority==="CLIENT_SUPPLIED_UNVERIFIED"?" · Source unverified":""}
                {Object.entries(result.continuity.counts).map(([reason,count])=>
                  " · "+reason.toLowerCase().replaceAll("_"," ")+" "+count)}
              </small>}
              {result?.event_page && <>
                {(["pivots","breaks"] as const).map(collection=>{
                  const page=result.event_page![collection];
                  return <button key={collection} type="button" className="small-tool"
                    disabled={Boolean(pageRequest)||!page.next_cursor}
                    onClick={()=>{setPageError("");setPageRequest({key:body,collection,cursor:page.next_cursor!});}}>
                    Older {collection} ({page.total})
                  </button>;
                })}
                {pageError && <small role="alert">{pageError}</small>}
                {older?.key===body && (older.collection==="pivots"||older.collection==="breaks") && <section aria-label="Historical structure evidence">
                  <p>{older.collection} {older.metadata.start+1}–{older.metadata.end} of {older.metadata.total}</p>
                  <ul>{[...older.events as StructureEvent[]].reverse().map(event=><li key={event.id}>
                    {event.classification??event.type.replaceAll("_"," ")} · {event.status} ·{" "}
                    {new Date((event.confirmation_time??event.available_at)*1000).toISOString()} · {event.break_price??event.price}
                    {event.confirmation_time!==null&&event.available_at>event.confirmation_time&&
                      " · Available "+new Date(event.available_at*1000).toISOString()}
                  </li>)}</ul>
                  <button type="button" className="small-tool" disabled={Boolean(pageRequest)||!older.metadata.next_cursor}
                    onClick={()=>setPageRequest({key:body,collection:older.collection,cursor:older.metadata.next_cursor!})}>Next older page</button>
                  <button type="button" className="small-tool" onClick={()=>{setPageRequest(undefined);setOlder(undefined);}}>Close evidence</button>
                </section>}
              </>}
              {recent.length>0 && <ul aria-label="Recent structure events">{recent.map(event=><li key={event.id}>
                {event.type==="PIVOT"?event.classification:event.type.replaceAll("_"," ")} · {event.status} ·{" "}
                {new Date((event.confirmation_time??event.available_at)*1000).toISOString().slice(0,16).replace("T"," ")} UTC
                {event.confirmation_time!==null&&event.available_at>event.confirmation_time&&
                  " · available "+new Date(event.available_at*1000).toISOString().slice(11,19)+" UTC"}
              </li>)}</ul>}
            </>;
          })()}
          {definition.parameters.liquidity && (()=>{
            const rules=item.liquidity??definition.parameters.liquidity;
            const result=data.find(row=>row.id===item.id);
            const history=older?.key===body&&["pools","sweeps","current_pools"].includes(older.collection)?older:undefined;
            const projection=result?.current_pools;
            const crossings=result?.sweeps??[];
            const matchingRevisions=new Set(crossings.map(event=>event.pool_revision_id));
            const recent=[...(result?.pools??[]).filter(event=>!matchingRevisions.has(event.id)),...crossings]
              .sort((a,b)=>b.available_at-a.available_at).slice(0,12);
            const rows=history?(history.collection==="current_pools"?history.events as LiquidityEvent[]:
              (history.events as LiquidityEvent[]).slice().reverse()):
              [...(projection?.items??[]).map(pool=>({...pool,id:pool.revision_id})),...recent];
            const selection=selectedLiquidity?.key===body?rows.find(event=>event.id===selectedLiquidity.id):undefined;
            const parameters: [keyof Liquidity,string,number,number][]=[
              ["equality_bps","Pool equality (bps)",0,100],["penetration_bps","Penetration (bps)",0,100],
              ["reclaim_bps","Reclaim (bps)",0,100],["min_touches","Minimum touches",2,16],
              ["max_touches","Maximum touches",2,32],["min_separation_bars","Touch separation",1,200],
              ["max_age_bars","Maximum age (bars)",2,4000],
            ];
            return <>
              {parameters.map(([key,label,min,max])=><label key={key}>{label}
                <input type="number" min={min} max={max} step={min===0?0.1:1} value={rules[key]}
                  onChange={event=>{const value=event.target.valueAsNumber;
                    if(value>=min&&value<=max)update(item.id,{liquidity:{...rules,[key]:value}});}}/>
              </label>)}
              {(["zones","sweeps","candidates","terminal"] as const).map(key=><label key={key}>
                <input type="checkbox" checked={liquidVisibility[key]}
                  onChange={event=>update(item.id,{liquidityVisibility:{...liquidVisibility,[key]:event.target.checked}})}/>
                Show liquidity {key}
              </label>)}
              {result?.liquidity_page && <section aria-label="Liquidity evidence">
                {projection&&<p role="status">Chart view: {projection.returned} of {projection.total} current pools
                  {projection.truncated&&" · Limited view, confirmed pools nearest price first"}
                  {" · Forming pools are provisional"}</p>}
                {projection?.next_cursor&&<button type="button" className="small-tool" disabled={Boolean(pageRequest)}
                  onClick={()=>{setPageError("");setPageRequest({key:body,collection:"current_pools",cursor:projection.next_cursor!});}}>
                  More current pools
                </button>}
                <p>{result.liquidity_page.pools.total} pool revisions · {result.liquidity_page.sweeps.total} crossings · {result.provenance?.context_authority?.replaceAll("_"," ")}</p>
                {(["pools","sweeps"] as const).map(collection=>{
                  const cursor=result.liquidity_page![collection].next_cursor;
                  return <button type="button" key={collection} className="small-tool" disabled={Boolean(pageRequest)||!cursor}
                    onClick={()=>{setPageError("");if(cursor)setPageRequest({key:body,collection,cursor});}}>Older liquidity {collection}</button>;
                })}
                {pageError && <small role="alert">{pageError}</small>}
                {history && <>
                  <p>{history.collection==="current_pools"?<>Current pool page · {history.metadata.returned} of {history.metadata.total}</>:
                    <>{history.collection} {history.metadata.start+1}–{history.metadata.end} of {history.metadata.total}</>}</p>
                  <button type="button" className="small-tool" disabled={Boolean(pageRequest)||!history.metadata.next_cursor}
                    onClick={()=>{const cursor=history.metadata.next_cursor;if(cursor)setPageRequest({key:body,collection:history.collection,cursor});}}>{history.collection==="current_pools"?"Next current pool page":"Next older liquidity page"}</button>
                  <button type="button" className="small-tool" onClick={()=>{setPageRequest(undefined);setOlder(undefined);}}>Latest liquidity evidence</button>
                </>}
                {[...new Map(rows.map(event=>[event.id,event])).values()].map(event=><button type="button" className="small-tool" key={event.id}
                  onClick={()=>setSelectedLiquidity({key:body,id:event.id})}>
                  {event.liquidity_side} · {event.state.replaceAll("_"," ")} · {new Date(event.available_at*1000).toISOString().slice(11,19)} UTC
                </button>)}
                {selection&&<dl aria-label="Selected liquidity event">
                  <dt>Pool boundaries</dt><dd>{selection.lower} – {selection.upper}</dd>
                  <dt>Activated UTC</dt><dd>{selection.activated_at===null?"Forming":new Date(selection.activated_at*1000).toISOString()}</dd>
                  <dt>Confirmation / availability</dt><dd>{new Date(selection.confirmation_time*1000).toISOString()} / {new Date(selection.available_at*1000).toISOString()}</dd>
                  <dt>Source</dt><dd>{result.provenance?.context_authority}</dd>
                  {selection.constituents && <><dt>Confirmed pivots</dt><dd>{selection.constituents.map(p=>p.price+" @ "+new Date(p.time*1000).toISOString()).join(", ")}</dd></>}
                  {selection.ohlc && <><dt>Crossing OHLC</dt><dd>{Object.values(selection.ohlc).join(" / ")}</dd></>}
                  {selection.related_structure_ids?.length ? <><dt>Related structure</dt><dd>{selection.related_structure_ids.join(", ")}</dd></>:null}
                </dl>}
              </section>}
            </>;
          })()}
          {definition.parameters.fvg && (()=>{
            const rules=item.fvg??definition.parameters.fvg;
            const gap=data.find(row=>row.id===item.id);
            const tick=item.tickSize?.[scheduleScope];
            const schedule=item.schedules?.[scheduleScope]??{kind:"unknown"};
            const change=(value:Partial<FvgRules>)=>update(item.id,{fvg:{...rules,...value}});
            const setSchedule=(value:Partial<Schedule>)=>update(item.id,
              {schedules:{...item.schedules,[scheduleScope]:{...schedule,...value}}});
            const history=older?.key===body&&["current_fvgs","formations","revisions"].includes(older.collection)?older:undefined;
            const zoneRows=history?history.events as FvgEvent[]:[...(gap?.current_fvgs?.items??[]),
              ...(gap?.revisions??[]).slice(-6).reverse(),...(gap?.formations??[]).slice(-6).reverse()];
            const selected=selectedFvg?.key===fvgSelectionScope?zoneRows.find(event=>event.id===selectedFvg.id):undefined;
            const values:[keyof Pick<FvgRules,"min_price"|"min_bps"|"min_ticks"|"atr_multiple"|"atr_period"|"min_body_bps"|"max_age_bars">,string,number,number,number][]=[
              ["min_price","Minimum price gap",0,Number.MAX_VALUE,.00001],["min_bps","Minimum gap (bps)",0,10000,.1],
              ["min_ticks","Minimum ticks",0,10000,1],["atr_multiple","ATR multiple",0,100,.1],
              ["atr_period","ATR period",2,100,1],["max_age_bars","FVG maximum age",1,500,1],
              ...(rules.displacement!=="none"?[["min_body_bps","Middle body (bps)",0,10000,.1] as ["min_body_bps",string,number,number,number]]:[]),
            ];
            const page=(collection:Collection,cursor:string|null|undefined)=>{
              if(cursor){setPageError("");setPageRequest({key:body,collection,cursor});}
            };
            const utc=(time:number)=>new Date(time*1000).toISOString();
            return <>
              {values.map(([key,name,min,max,step])=><label key={key}>{name}<input type="number" min={min} max={max} step={step}
                value={rules[key]} onChange={event=>{const value=event.target.valueAsNumber;if(value>=min&&value<=max)change({[key]:value});}}/></label>)}
              {rules.min_ticks>0&&<>
                <label>Declared tick size <input type="number" min={0} step="any" value={tick?.size||""}
                  onChange={event=>update(item.id,{tickSize:{...item.tickSize,[scheduleScope]:{
                    size:event.target.valueAsNumber||0,reference:tick?.reference??""}}})}/></label>
                <label>Tick source reference <input value={tick?.reference??""} maxLength={120}
                  onChange={event=>update(item.id,{tickSize:{...item.tickSize,[scheduleScope]:{
                    size:tick?.size??0,reference:event.target.value}}})}/></label>
              </>}
              {source.endsWith("_SAVED")&&<label>FVG archive start (UTC) <input type="datetime-local"
                value={rules.history_start!=null?new Date(rules.history_start*1000).toISOString().slice(0,16):""}
                onChange={event=>{const stamp=event.target.valueAsNumber/1000;
                  change(Number.isFinite(stamp)?{history_bars:10000,history_start:stamp}:{history_bars:2000,history_start:null});}}/></label>}
              <label>FVG displacement <select value={rules.displacement} onChange={event=>change({displacement:event.target.value as FvgRules["displacement"]})}>
                <option value="none">None</option><option value="directional_body_bps_v1">Directional body v1</option>
              </select></label>
              <label>FVG mitigation <select value={rules.mitigation} onChange={event=>change({mitigation:event.target.value as FvgRules["mitigation"]})}>
                <option value="wick">Wick</option><option value="close">Close</option>
              </select></label>
              <label>FVG market schedule <select value={schedule.kind} onChange={event=>setSchedule({kind:event.target.value as Schedule["kind"]})}>
                <option value="unknown">Unknown</option><option value="continuous">Continuous</option><option value="forex_weekly">Configured Sunday–Friday</option>
              </select></label>
              {schedule.kind==="forex_weekly"&&<>
                <label>FVG schedule timezone <input value={schedule.timezone??"America/New_York"} onChange={event=>setSchedule({timezone:event.target.value})}/></label>
                <label>FVG Sunday open <input type="time" value={schedule.sunday_open??"17:00"} onChange={event=>setSchedule({sunday_open:event.target.value})}/></label>
                <label>FVG Friday close <input type="time" value={schedule.friday_close??"17:00"} onChange={event=>setSchedule({friday_close:event.target.value})}/></label>
              </>}
              {(Object.keys(fvgVisibility) as (keyof FvgVisibility)[]).map(key=><label key={key}><input type="checkbox" checked={gapVisibility[key]}
                onChange={event=>update(item.id,{fvgVisibility:{...gapVisibility,[key]:event.target.checked}})}/> Show FVG {key}</label>)}
              {gap?.fvg_page && <section aria-label="FVG evidence">
                <p>Current FVGs {gap.current_fvgs?.returned} / {gap.current_fvgs?.total}
                  {gap.current_fvgs?.truncated&&" (chart view limited)"} · Source {gap.provenance?.context_authority}</p>
                <button type="button" className="small-tool" disabled={!gap.current_fvgs?.next_cursor||Boolean(pageRequest)}
                  onClick={()=>page("current_fvgs",gap.current_fvgs?.next_cursor)}>More current FVGs</button>
                {(["formations","revisions"] as const).map(key=><button key={key} type="button" className="small-tool"
                  disabled={!gap.fvg_page?.[key].next_cursor||Boolean(pageRequest)} onClick={()=>page(key,gap.fvg_page?.[key].next_cursor)}>Older FVG {key}</button>)}
                {history&&<><button type="button" className="small-tool" disabled={!history.metadata.next_cursor||Boolean(pageRequest)}
                  onClick={()=>page(history.collection,history.metadata.next_cursor)}>Next FVG page</button>
                  <button type="button" className="small-tool" onClick={()=>{setPageRequest(undefined);setOlder(undefined);}}>Latest FVG evidence</button></>}
                {!!gap.warnings.length&&<p>{gap.warnings.join(" · ")}</p>}
                {pageError&&<small role="alert">{pageError}</small>}
                {[...new Map(zoneRows.map(event=>[event.id,event])).values()].map(event=><button key={event.id} type="button" className="small-tool"
                  onClick={()=>setSelectedFvg({key:fvgSelectionScope,id:event.id})}>{event.direction} · {event.state.replaceAll("_"," ")} · {event.mitigation_pct.toFixed(1)}%</button>)}
                {selected&&<dl aria-label="Selected FVG">
                  <dt>Formation A / B / C</dt><dd>{[selected.a_time,selected.b_time,selected.c_time].map(utc).join(" / ")}</dd>
                  {selected.formation_ohlc?.map((candle,index)=><div key={index}>
                    <dt>{"ABC"[index]} OHLC</dt><dd>{candle.join(" / ")}</dd></div>)}
                  <dt>Price zone</dt><dd>{selected.lower} – {selected.upper}</dd>
                  <dt>Remaining</dt><dd>{selected.remaining_lower} – {selected.remaining_upper}</dd>
                  <dt>Mitigation and reason</dt><dd>{selected.mitigation_pct}% · {selected.reason}</dd>
                  <dt>Formed / observed / confirmed / available</dt><dd>{[selected.formation_confirmation_time,selected.observation_time,selected.confirmation_time,selected.available_at].map(utc).join(" / ")}</dd>
                  <dt>Source authority</dt><dd>{gap.provenance?.context_authority}</dd>
                  <dt>Algorithm</dt><dd>FVG v1 · identity policy {selected.identity_policy_version??1} · {rules.mitigation} · {schedule.kind}</dd>
                  {!!selected.warnings.length&&<><dt>Quality warnings</dt><dd>{selected.warnings.join(", ")}</dd></>}
                </dl>}
              </section>}
            </>;
          })()}
          {definition.parameters.schedule && <>
            <label>Market schedule <select value={schedule.kind}
              onChange={event => updateSchedule({kind:event.target.value as Schedule["kind"]})}>
              <option value="unknown">Unknown</option>
              <option value="continuous">Continuous (24/7)</option>
              <option value="forex_weekly">Configured Sunday–Friday</option>
            </select></label>
            {schedule.kind === "forex_weekly" && <>
              <label>Market timezone <input value={schedule.timezone ?? "America/New_York"}
                onChange={event => updateSchedule({timezone:event.target.value})} /></label>
              <label>Sunday open <input type="time" value={schedule.sunday_open ?? "17:00"}
                onChange={event => updateSchedule({sunday_open:event.target.value})} /></label>
              <label>Friday close <input type="time" value={schedule.friday_close ?? "17:00"}
                onChange={event => updateSchedule({friday_close:event.target.value})} /></label>
            </>}
          </>}
          {!definition.timeframes.includes(timeframe) && <small>Unavailable on {timeframe}</small>}
          <button className="small-tool" type="button" onClick={() => setSettings(items => items.filter(row => row.id !== item.id))}>Disable {definition.label}</button>
        </fieldset>;
      })}
    </details>
    {data.flatMap(row => {
      if (!row.coverage) return [];
      const label = definitions.find(item => item.id === row.id)?.label ?? row.id;
      const status = coverageLabels[row.coverage.status] ?? row.coverage.status;
      return <small key={row.id} role="status">{label} · {status}
        {row.coverage.confirmation_status === "provisional" && " · provisional evidence"}</small>;
    })}
    <small role="status">{libraryError || current?.error || (warnings.length ? warnings.map(value => value.toLowerCase().replaceAll("_", " ")).join(" · ") :
      selected === "swings" ? "Markers appear at the pivot after confirmation" : active.length ? "Closed candles · dashed session levels are developing" : "")}</small>
  </div>;
  return {data, controls};
}
