import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { Candle, CandleResponse, ChartSource, Timeframe } from "./types";
import { useWorkspacePreference } from "./useWorkspacePreference";

type Window = { timezone: string; start: string; end: string };
type Schedule = {kind: "continuous" | "forex_weekly" | "unknown"; timezone?: string;
  sunday_open?: string; friday_close?: string};
type Structure = {algorithm_version?: 1 | 2; market_schedule?: Schedule; left: number; right: number; price_source: "wick" | "close";
  ties: "strict" | "earliest"; tolerance_bps: number; break_bps: number; wick_candidates: boolean; history_bars?:2000|10000;history_start?:number|null};
type Collection = "pivots" | "breaks" | "pools" | "sweeps" | "current_pools" | "formations" | "revisions" | "current_fvgs" | "ob_formations" | "ob_revisions" | "current_obs" | "crt_formations" | "crt_sweeps" | "crt_revisions" | "current_crt";
type FvgRules = {min_price:number;min_bps:number;min_ticks:number;tick_size?:number|null;tick_size_reference?:string|null;
  atr_multiple:number;atr_period:number;displacement:"none"|"directional_body_bps_v1";min_body_bps:number;
  mitigation:"wick"|"close";max_age_bars:number;market_schedule:Schedule;history_bars?:2000|10000;history_start?:number|null};
type FvgVisibility={bullish:boolean;bearish:boolean;active:boolean;partial:boolean;full:boolean;invalidated:boolean;expired:boolean};
const fvgVisibility:FvgVisibility={bullish:true,bearish:true,active:true,partial:true,full:true,invalidated:true,expired:true};
export type FvgEvent = {id:string;fvg_id:string;identity_policy_version?:number;direction:"BULLISH"|"BEARISH";
  state:"ACTIVE"|"PARTIALLY_MITIGATED"|"FULLY_MITIGATED"|"INVALIDATED"|"EXPIRED"|"UNCERTAIN";
  lower:number;upper:number;remaining_lower:number;remaining_upper:number;mitigation_pct:number;
  formation_ohlc?:number[][];
  a_time:number;b_time:number;c_time:number;observation_time:number;formation_confirmation_time:number;
  confirmation_time:number;available_at:number;reason:string;warnings:string[];timing?:EventTiming};
type TimingPolicy = "LEGACY_RESEARCH" | "STRICT_CAUSAL";
type EventTiming = {strategy_eligibility:string;eligibility_reason:string;observation_start:number;
  observation_end:number;actual_availability:number;dependency_available_at:number};
function timingLabel(timing?:EventTiming) {
  if(!timing)return "Research only";
  const utc=(stamp:number)=>new Date(stamp*1000).toISOString();
  return [timing.eligibility_reason.replaceAll("_"," "),`Observation ${utc(timing.observation_start)} – ${utc(timing.observation_end)}`,
    `Available ${utc(timing.actual_availability)}`,timing.dependency_available_at
      ?`Reference available ${utc(timing.dependency_available_at)}`:null].filter(Boolean).join(" · ");
}
type ContextPolicy = "shared" | "independent";
type ContextConfiguration = {policy:ContextPolicy;status:string;failures:Record<string,string|{status:string;reason:string|null}>};
type CrtRules = {reference_frame:"H1"|"H4"|"D1"|null;reference_mode:"aggregated"|"native";
  daily_timezone:string;daily_boundary:string;market_schedule:Schedule;reference_start:number|null;
  history_bars:2000|10000;history_start:number|null;max_ranges:number;max_age:number;candidate_age:number;
  min_penetration:number;min_reclaim:number;invalidate:"sweep_extreme"|"reference_boundary"|"none";include_context:boolean};
const crtVisibility={ranges:true,midpoint:true,high:true,low:true,expansion:true,ambiguous:true,terminal:true};
type CrtVisibility=typeof crtVisibility;
export type CrtEvent={id:string;range_id:string;state:string;reason:string;reference_frame:string;reference_mode:string;
  timing?:EventTiming;reference_start:number;reference_end:number;reference_available_at:number;reference_ohlc:number[];constituents:number;
  high:number;low:number;midpoint:number;observation_time:number;confirmation_time:number;available_at:number;
  sweep_side?:"HIGH"|"LOW";swept_boundary?:number;sweep_extreme?:number;penetration?:number;reclaim_close?:number|null;
  context:Record<string,{id:string;witness_id:string;timeframe:string;available_at:number}>};
type ObRules = {lookback:number;candidate_age:number;min_body:number;min_break:number;min_displacement:number;
  geometry:"wick"|"body";allow_choch:boolean;include_context:boolean;mitigation:"wick"|"close";
  min_penetration_pct:number;max_age_bars:number};
const obVisibility={...fvgVisibility,touched:true};
type ObVisibility=typeof obVisibility;
export type ObEvent = {id:string;ob_id:string;direction:FvgEvent["direction"];state:FvgEvent["state"]|"TOUCHED";
  candidate_time:number;candidate_available_at:number;candidate_ohlc:number[];geometry:string;lower:number;upper:number;
  remaining_lower:number;remaining_upper:number;mitigation_pct:number;break_type:string;break_time:number;bos_id:string;
  broken_pivot_id:string;bos_confirmation_time:number;bos_available_at:number;first_visibility_time:number;
  observation_time:number;confirmation_time:number;available_at:number;reason:string;timing?:EventTiming;
  structure_version:number;structure_witness_id:string;identity_policy_version:number;
  context:{status:string;fvg:{id:string;available_at:number}|null;liquidity_sweep:{id:string;available_at:number}|null}};
type Liquidity = {equality_bps:number; penetration_bps:number; reclaim_bps:number;
  min_touches:number; max_touches:number; min_separation_bars:number; max_age_bars:number};
type LiquidityVisibility = {zones:boolean; sweeps:boolean; candidates:boolean; terminal:boolean};
const liquidityVisibility: LiquidityVisibility={zones:true,sweeps:true,candidates:true,terminal:true};
export type LiquidityEvent = {
  id:string; pool_id:string; pool_revision_id?:string; state:string; lower:number; upper:number; liquidity_side:"BSL"|"SSL";
  activated_at:number|null; activation_observation_time?:number|null; observation_time:number; confirmation_time:number; available_at:number;
  status:string; timing?:EventTiming; constituents?:{id:string; price:number; time:number; available_at:number}[];
  related_structure_ids?:string[]; ohlc?:{open:number; high:number; low:number; close:number};
};
type PageInfo = {total: number; returned: number; start: number; end: number; next_cursor: string | null};
type OlderPage = {key: string; collection: Collection; metadata: PageInfo; events: (StructureEvent | LiquidityEvent | FvgEvent | ObEvent | CrtEvent)[]};
export type CurrentLiquidityPool = Omit<LiquidityEvent,"id"> & {revision_id:string; expiry_in_bars:number};
type StructureVisibility = {swings: boolean; breaks: boolean; candidates: boolean};
export type StructureEvent = {
  id: string; type: string; side: "HIGH" | "LOW"; status: "confirmed" | "provisional" | "invalidated";
  timing?:EventTiming; available_at: number; confirmation_time: number | null; confirmation_bar_time?: number;
  price?: number; classification?: string; direction?: "BULLISH" | "BEARISH";
  break_time?: number; break_price?: number; level_time?: number; level_price?: number;
};
const structureVisibility: StructureVisibility = {swings: true, breaks: true, candidates: true};

type Definition = { id: string; label: string; period: number | null; timeframes: Timeframe[];
  parameters: { session?: Window; schedule?: Schedule["kind"][]; structure?: Structure; liquidity?: Liquidity; fvg?: FvgRules; ob?: ObRules; crt?:CrtRules;crt_frames?:Partial<Record<Timeframe,NonNullable<CrtRules['reference_frame']>[]>> } };
type Settings = { id: string; period?: string; session?: Window; schedules?: Record<string, Schedule>; structure?: Structure; visibility?: StructureVisibility; liquidity?: Liquidity; liquidityVisibility?: LiquidityVisibility; fvg?: FvgRules; fvgVisibility?: FvgVisibility; ob?: ObRules; obVisibility?:ObVisibility; crt?:CrtRules;crtVisibility?:CrtVisibility; contextPolicy?:ContextPolicy; tickSize?: Record<string,{size:number;reference:string}> };
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
  obVisibility?:ObVisibility;
  crtVisibility?:CrtVisibility;
  crt_formations?:CrtEvent[];
  crt_sweeps?:CrtEvent[];
  crt_revisions?:CrtEvent[];
  current_crt?:PageInfo & {items:CrtEvent[]};
  crt_page?:{formations:PageInfo;sweeps:PageInfo;revisions:PageInfo};
  quality?:Record<string,number>;
  reference_frame?:string;
  context_configuration?:ContextConfiguration;
  ob_formations?:ObEvent[];
  ob_revisions?:ObEvent[];
  current_obs?:PageInfo & {items:ObEvent[]};
  ob_page?:{formations:PageInfo;revisions:PageInfo};
  rejections?:Record<string,number>;
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
function stored(key: string): Settings[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) ?? "[]");
    if (Array.isArray(value)) return value.filter((item): item is Settings =>
      Boolean(item && typeof item.id === "string" && item.id.length <= 40)).slice(0, 8);
  } catch { /* Use the primary indicator setting when storage is unavailable. */ }
  return [];
}

export function useChartIndicator(enabled: boolean, candles: Candle[], source: ChartSource, timeframe: Timeframe,
  symbol: string, cutoff: number, feed?: CandleResponse, frozen = false, request?: {indicator?: string; sequence: number}, pane = "") {
  const scope = pane ? pane+"-" : "";
  const libraryKey = pane ? preference+"-"+pane : preference;
  const [timingPreference,setTimingPreference]=useWorkspacePreference<TimingPolicy>(scope+"analytical-timing","LEGACY_RESEARCH");
  const timingPolicy:TimingPolicy=timingPreference==="STRICT_CAUSAL"?"STRICT_CAUSAL":"LEGACY_RESEARCH";
  const [selected, setSelected] = useWorkspacePreference<string>(scope+"indicator", "none");
  const [periodText, setPeriod] = useWorkspacePreference<string>(scope+"indicator-period", "");
  const [settings, setSettings] = useState(()=>stored(libraryKey));
  const [search, setSearch] = useState("");
  const [candidate, setCandidate] = useState("");
  const [definitions, setDefinitions] = useState<Definition[]>([]);
  const [libraryError, setLibraryError] = useState("");
  const [older, setOlder] = useState<OlderPage>();
  const [pageRequest, setPageRequest] = useState<{key: string; collection: Collection; cursor: string}>();
  const [pageError, setPageError] = useState("");
  const [selectedCrt,setSelectedCrt]=useState<{key:string;id:string}>();
  const [selectedOb,setSelectedOb]=useState<{key:string;id:string}>();
  const [selectedFvg,setSelectedFvg]=useState<{key:string;id:string}>();
  const [selectedLiquidity,setSelectedLiquidity]=useState<{key:string; id:string}>();
  const [response, setResponse] = useState<{key: string; results?: IndicatorResult[]; error?: string}>();
  useEffect(() => { if (request?.indicator) { setSelected(request.indicator); setPeriod(""); } },
    [request, setSelected, setPeriod]);
  useEffect(() => {
    try { localStorage.setItem(libraryKey, JSON.stringify(settings)); } catch { /* Keep editing in memory. */ }
  }, [settings,libraryKey]);

  const scheduleScope = JSON.stringify([source, symbol, feed?.feed_id, feed?.price_basis]);
  const fvgSelectionScope = JSON.stringify([scheduleScope,timeframe,feed?.dataset_id,frozen,timingPolicy]);
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
      const structure=item.structure??(definition.parameters.ob?definition.parameters.structure:undefined);
      const structureInput=structure?{...structure,
        ...(structure.algorithm_version===2?{market_schedule:item.schedules?.[scheduleScope]}:{}),
        ...(definition.parameters.ob&&!source.endsWith("_SAVED")?{history_bars:2000 as const,history_start:null}:{}),
      }:undefined;
      const crt=definition.parameters.crt?{...definition.parameters.crt,...item.crt}:undefined;
      if(crt){
        const frames=definition.parameters.crt_frames?.[timeframe]??[];
        if(crt.reference_frame&&!frames.includes(crt.reference_frame))crt.reference_frame=null;
        crt.market_schedule=item.schedules?.[scheduleScope]??{kind:"unknown"};
        if(!source.endsWith("_SAVED")||crt.history_start===null){
          crt.history_start=null;
          crt.history_bars=2000;
          crt.reference_mode="aggregated";
        }
      }
      const ob=definition.parameters.ob?{...definition.parameters.ob,...item.ob}:undefined;
      const context=crt?.include_context||ob?.include_context?{policy:item.contextPolicy??"independent"}:undefined;
      return [{id: item.id,...(crt?{crt}:{}),...(ob?{ob}:{}),...(context?{context}:{}), ...(period !== null ? {period} : {}), ...(session ? {session} : {}),
        ...(schedule ? {schedule} : {}), ...(structureInput?{structure:structureInput}:{}),
        ...(item.liquidity ? {liquidity:item.liquidity} : {}),
        ...(definition.parameters.fvg?{fvg:{...definition.parameters.fvg,...item.fvg,
          market_schedule:item.schedules?.[scheduleScope]??{kind:"unknown"},
          ...(item.fvg?.history_bars===10000&&(!source.endsWith("_SAVED")||item.fvg.history_start==null)
            ?{history_bars:2000,history_start:null}:{}),
          tick_size:item.tickSize?.[scheduleScope]?.size&&item.tickSize?.[scheduleScope]?.reference?item.tickSize[scheduleScope].size:undefined,
          tick_size_reference:item.tickSize?.[scheduleScope]?.size&&item.tickSize?.[scheduleScope]?.reference?item.tickSize[scheduleScope].reference:undefined}}:{})}];
    });
  }, [definitions, periodText, primary, scheduleScope, settings, source, timeframe]);
  const body = useMemo(() => enabled && active.length && (candles.length || active.some(item => item.id.startsWith("previous_")))
    ? JSON.stringify({source, timeframe, symbol, cutoff, frozen, timing_policy:timingPolicy, load_reference: true, feed_id: feed?.feed_id,
        dataset_id: feed?.dataset_id, partitions: feed?.partitions, price_basis: feed?.price_basis, indicators: active, candles: candles.slice(-2000)})
    : "", [active, candles, cutoff, enabled, frozen, feed?.dataset_id, feed?.feed_id, feed?.price_basis, feed?.partitions, source, symbol, timeframe,timingPolicy]);

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
    const crt=collection==="current_crt"||collection.startsWith("crt_");
    const crtKind=collection==="current_crt"?"current":collection.slice(4,-1);
    const current=collection==="current_pools"||collection==="current_fvgs"||collection==="current_obs";
    const liquid=collection==="current_pools"||collection==="pools"||collection==="sweeps";
    const ob=collection==="current_obs"||collection==="ob_formations"||collection==="ob_revisions";
    const fvg=collection==="current_fvgs"||collection==="formations"||collection==="revisions";
    const spec=input.indicators.find((item:{id:string})=>item.id===(liquid?"liquidity_sweeps":fvg?"fair_value_gap":ob?"order_blocks":crt?"candle_range_theory":"market_structure"));
    const first=liquid?"pool":fvg||ob?"formation":"pivot", second=liquid?"sweep":fvg||ob?"revision":"break";
    const isFirst=collection==="pivots"||collection==="pools"||collection==="formations"||collection==="ob_formations";
    const compatibleContext=spec?.context?.policy==="shared"
      ?input.indicators.filter((item:{id:string})=>item.id!==spec.id):[];
    input.indicators=crt?[{...spec,crt_delivery:{
      formation_limit:0,sweep_limit:0,revision_limit:0,current_limit:0,
      [crtKind+"_limit"]:crtKind==="current"?8:24,[crtKind+"_cursor"]:cursor,
    }}]:[{...spec,[liquid?"liquidity_delivery":fvg?"fvg_delivery":ob?"ob_delivery":"structure_delivery"]:{
      [first+"_limit"]:!current&&isFirst?40:0,[second+"_limit"]:!current&&!isFirst?40:0,
      ...(liquid||fvg||ob?{current_limit:current?40:0}:{}),
      [(current?"current":isFirst?first:second)+"_cursor"]:cursor,
    }}];
    input.indicators.push(...compatibleContext);
    void (async()=>{
      try{
        const response=await fetch("/research/api/indicators/evaluate",{
          method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(input),signal:controller.signal,
        });
        const value=await response.json();
        if(!response.ok)throw Error(typeof value.detail==="string"?value.detail:"Historical event page unavailable");
        const result=value.results[0] as IndicatorResult;
        const metadata=collection==="current_crt"?result.current_crt:
          collection==="crt_formations"?result.crt_page?.formations:collection==="crt_sweeps"?result.crt_page?.sweeps:
          collection==="crt_revisions"?result.crt_page?.revisions:collection==="current_obs"?result.current_obs:
          collection==="ob_formations"?result.ob_page?.formations:collection==="ob_revisions"?result.ob_page?.revisions:
          collection==="current_fvgs"?result.current_fvgs:
          collection==="formations"||collection==="revisions"?result.fvg_page?.[collection]:
          collection==="current_pools"?result.current_pools:
          collection==="pools"||collection==="sweeps"?result.liquidity_page?.[collection]:result.event_page?.[collection];
        const events=collection==="current_crt"?result.current_crt?.items:collection==="current_obs"?result.current_obs?.items:collection==="current_fvgs"?result.current_fvgs?.items:collection==="current_pools"?result.current_pools?.items.map(pool=>({...pool,id:pool.revision_id})):
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
  const rangeVisibility=settings.find(item=>item.id==="candle_range_theory")?.crtVisibility??crtVisibility;
  const blockVisibility=settings.find(item=>item.id==="order_blocks")?.obVisibility??obVisibility;
  const gapVisibility=settings.find(item=>item.id==="fair_value_gap")?.fvgVisibility??fvgVisibility;
  const data = useMemo(() => current?.results?.map(item => item.id === "market_structure" ?
    {...item, visibility} : item.id==="liquidity_sweeps"?{...item,liquidityVisibility:liquidVisibility}:item.id==="fair_value_gap"?{...item,fvgVisibility:gapVisibility}:item.id==="order_blocks"?{...item,obVisibility:blockVisibility}:item.id==="candle_range_theory"?{...item,crtVisibility:rangeVisibility}:item) ?? emptyResults,
    [current, visibility, liquidVisibility, gapVisibility,blockVisibility,rangeVisibility]);
  const warnings = [...new Set(data.filter(row => !row.coverage).flatMap(row => row.warnings))];
  const choices = definitions.filter(item => item.timeframes.includes(timeframe) && item.id !== selected &&
    !settings.some(config => config.id === item.id) && (item.label + " " + item.id).toLowerCase().includes(search.toLowerCase()));
  function contextSettings(item:Settings,result:IndicatorResult|undefined,label:string) {
    return <>
      <label>{label} context policy <select value={item.contextPolicy??"independent"}
        onChange={e=>update(item.id,{contextPolicy:e.target.value as ContextPolicy})}>
        <option value="independent">Independent documented rules</option>
        <option value="shared">Share selected indicator settings</option>
      </select></label>
      {result?.context_configuration&&<details>
        <summary>{label} effective context · {result.context_configuration.status.replaceAll("_"," ").toLowerCase()}</summary>
        <pre style={{maxHeight:"14rem",overflow:"auto"}}>{JSON.stringify(result.context_configuration,null,2)}</pre>
      </details>}
    </>;
  }
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
      <label>Evidence timing policy <select value={timingPolicy}
        onChange={event=>setTimingPreference(event.target.value as TimingPolicy)}>
        <option value="LEGACY_RESEARCH">Legacy research</option>
        <option value="STRICT_CAUSAL">Strict causal</option>
      </select></label>
      <p role="status">{timingPolicy==="LEGACY_RESEARCH"?"Research-only evidence":
        "Strict causal evidence · eligibility is evaluated per observation"}</p>
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
              {definition.parameters.ob&&source.endsWith("_SAVED")&&<label>OB archive start (UTC) <input type="datetime-local"
                value={rules.history_start!=null?new Date(rules.history_start*1000).toISOString().slice(0,16):""}
                onChange={event=>{const start=event.target.valueAsNumber/1000;patch(Number.isFinite(start)?
                  {history_bars:10000,history_start:start}:{history_bars:2000,history_start:null});}}/></label>}
              <label>Pivot price <select value={rules.price_source}
                onChange={event=>patch({price_source:event.target.value as Structure["price_source"]})}>
                <option value="wick">Wick</option><option value="close">Close</option>
              </select></label>
              <label>Pivot ties <select value={rules.ties} onChange={event=>patch({ties:event.target.value as Structure["ties"]})}>
                <option value="strict">Strict</option><option value="earliest">Earliest</option>
              </select></label>
              {!definition.parameters.liquidity && <label><input type="checkbox" checked={rules.wick_candidates}
                onChange={event=>patch({wick_candidates:event.target.checked})}/> Calculate wick candidates</label>}
              {!definition.parameters.liquidity && !definition.parameters.ob && (["swings","breaks","candidates"] as const).map(key=><label key={key}>
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
                    {event.timing&&" · "+timingLabel(event.timing)}
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
                  <dt>Evidence eligibility</dt><dd>{timingLabel(selection.timing)}</dd>
                  <dt>Confirmation / availability</dt><dd>{new Date(selection.confirmation_time*1000).toISOString()} / {new Date(selection.available_at*1000).toISOString()}</dd>
                  <dt>Source</dt><dd>{result.provenance?.context_authority}</dd>
                  {selection.constituents && <><dt>Confirmed pivots</dt><dd>{selection.constituents.map(p=>p.price+" @ "+new Date(p.time*1000).toISOString()).join(", ")}</dd></>}
                  {selection.ohlc && <><dt>Crossing OHLC</dt><dd>{Object.values(selection.ohlc).join(" / ")}</dd></>}
                  {selection.related_structure_ids?.length ? <><dt>Related structure</dt><dd>{selection.related_structure_ids.join(", ")}</dd></>:null}
                </dl>}
              </section>}
            </>;
          })()}
          {definition.parameters.crt && (() => {
            const rules=item.crt??definition.parameters.crt;
            const frames=definition.parameters.crt_frames?.[timeframe]??[];
            const result=data.find(row=>row.id===item.id);
            const schedule=item.schedules?.[scheduleScope]??{kind:"unknown"};
            const patch=(value:Partial<CrtRules>)=>update(item.id,{crt:{...rules,...value}});
            const setSchedule=(value:Partial<Schedule>)=>update(item.id,
              {schedules:{...item.schedules,[scheduleScope]:{...schedule,...value}}});
            const history=older?.key===body&&(older.collection.startsWith("crt_")||older.collection==="current_crt")?older:undefined;
            const rows=history?history.events as CrtEvent[]:[...(result?.current_crt?.items??[]),
              ...(result?.crt_sweeps??[]).slice(-6).reverse(),...(result?.crt_revisions??[]).slice(-6).reverse(),
              ...(result?.crt_formations??[]).slice(-6).reverse()];
            const event=selectedCrt?.key===fvgSelectionScope?rows.find(row=>row.id===selectedCrt.id):undefined;
            const utc=(stamp:number)=>new Date(stamp*1000).toISOString();
            const page=(collection:Collection,cursor:string|null|undefined)=>{
              if(cursor){setPageError("");setPageRequest({key:body,collection,cursor});}
            };
            const nums:[keyof Pick<CrtRules,"max_ranges"|"max_age"|"candidate_age"|"min_penetration"|"min_reclaim">,
              string,number,number,boolean][]=[
              ["max_ranges","CRT concurrent ranges",1,8,true],["max_age","CRT maximum reference age",1,500,true],
              ["candidate_age","CRT maximum candidate age",1,40,true],["min_penetration","CRT minimum penetration",0,Infinity,false],
              ["min_reclaim","CRT minimum reclaim",0,Infinity,false],
            ];
            return <>
              <label>CRT reference timeframe <select value={rules.reference_frame&&frames.includes(rules.reference_frame)?rules.reference_frame:""}
                onChange={e=>patch({reference_frame:(e.target.value||null) as CrtRules["reference_frame"]})}>
                <option value="">Default ({frames[0]})</option>{frames.map(frame=><option key={frame}>{frame}</option>)}</select></label>
              {source.endsWith("_SAVED")&&<>
                <label>CRT archive start (UTC) <input type="datetime-local" value={rules.history_start===null?"":utc(rules.history_start).slice(0,16)}
                  onChange={e=>{const stamp=e.target.valueAsNumber/1000;patch(Number.isFinite(stamp)?
                    {history_start:stamp,history_bars:10000}:{history_start:null,history_bars:2000,reference_mode:"aggregated"});}}/></label>
                {rules.history_start!==null&&<label>CRT reference source <select value={rules.reference_mode}
                  onChange={e=>patch({reference_mode:e.target.value as CrtRules["reference_mode"]})}>
                  <option value="aggregated">Aggregate observations</option><option value="native">Match provider-native reference</option></select></label>}
              </>}
              <label>CRT fixed reference start (UTC) <input type="datetime-local" value={rules.reference_start===null?"":utc(rules.reference_start).slice(0,16)}
                onChange={e=>{const stamp=e.target.valueAsNumber/1000;patch({reference_start:Number.isFinite(stamp)?stamp:null});}}/></label>
              {(rules.reference_frame??frames[0])==="D1"&&<>
                <label>CRT daily timezone <input value={rules.daily_timezone} onChange={e=>patch({daily_timezone:e.target.value})}/></label>
                <label>CRT daily boundary <input type="time" value={rules.daily_boundary} onChange={e=>patch({daily_boundary:e.target.value})}/></label>
              </>}
              <label>CRT market schedule <select value={schedule.kind} onChange={e=>setSchedule({kind:e.target.value as Schedule["kind"]})}>
                <option value="unknown">Unknown</option><option value="continuous">Continuous</option><option value="forex_weekly">Configured Sunday–Friday</option>
              </select></label>
              {schedule.kind==="forex_weekly"&&<>
                <label>CRT schedule timezone <input value={schedule.timezone??"America/New_York"} onChange={e=>setSchedule({timezone:e.target.value})}/></label>
                <label>CRT Sunday open <input type="time" value={schedule.sunday_open??"17:00"} onChange={e=>setSchedule({sunday_open:e.target.value})}/></label>
                <label>CRT Friday close <input type="time" value={schedule.friday_close??"17:00"} onChange={e=>setSchedule({friday_close:e.target.value})}/></label>
              </>}
              {nums.map(([key,label,min,max,integer])=><label key={key}>{label}<input type="number" min={min}
                max={Number.isFinite(max)?max:undefined} step={integer?1:"any"} value={rules[key]}
                onChange={e=>{const value=e.target.valueAsNumber;if(value>=min&&value<=max&&(!integer||Number.isInteger(value)))patch({[key]:value});}}/></label>)}
              <label>CRT close invalidation <select value={rules.invalidate} onChange={e=>patch({invalidate:e.target.value as CrtRules["invalidate"]})}>
                <option value="sweep_extreme">Beyond sweep extreme</option><option value="reference_boundary">Beyond reference boundary</option>
                <option value="none">Disabled</option></select></label>
              <label><input type="checkbox" checked={rules.include_context} onChange={e=>patch({include_context:e.target.checked})}/> Include CRT context</label>
              {rules.include_context&&contextSettings(item,result,"CRT")}
              {(Object.keys(crtVisibility) as (keyof CrtVisibility)[]).map(key=><label key={key}><input type="checkbox"
                checked={rangeVisibility[key]} onChange={e=>update(item.id,{crtVisibility:{...rangeVisibility,[key]:e.target.checked}})}/> Show CRT {key}</label>)}
              {result?.crt_page&&<section aria-label="CRT evidence">
                <p>{result.reference_frame} → {timeframe} · Current ranges {result.current_crt?.returned} / {result.current_crt?.total} · {result.provenance?.context_authority}</p>
                {!!result.warnings.length&&<p>{result.warnings.join(" · ")}</p>}
                {(["formations","sweeps","revisions"] as const).map(key=><button key={key} type="button" className="small-tool"
                  disabled={!result.crt_page?.[key].next_cursor||Boolean(pageRequest)}
                  onClick={()=>page(("crt_"+key) as Collection,result.crt_page?.[key].next_cursor)}>Older CRT {key}</button>)}
                {history&&<><button type="button" className="small-tool" disabled={!history.metadata.next_cursor||Boolean(pageRequest)}
                  onClick={()=>page(history.collection,history.metadata.next_cursor)}>Next CRT page</button>
                  <button type="button" className="small-tool" onClick={()=>{setPageRequest(undefined);setOlder(undefined);}}>Latest CRT evidence</button></>}
                {pageError&&<small role="alert">{pageError}</small>}
                {[...new Map(rows.map(row=>[row.id,row])).values()].map(row=><button key={row.id} type="button" className="small-tool"
                  onClick={()=>setSelectedCrt({key:fvgSelectionScope,id:row.id})}>{row.state.replaceAll("_"," ")} · {utc(row.available_at)}</button>)}
                {event&&<dl aria-label="Selected CRT event">
                  <dt>Reference OHLC and inputs</dt><dd>{event.reference_ohlc.join(" / ")} · {event.constituents} candles · {event.reference_mode}</dd>
                  <dt>Reference low / midpoint / high</dt><dd>{event.low} / {event.midpoint} / {event.high}</dd>
                  <dt>Evidence eligibility</dt><dd>{timingLabel(event.timing)}</dd>
                  <dt>Origin / closed / available</dt><dd>{[event.reference_start,event.reference_end,event.reference_available_at].map(utc).join(" / ")}</dd>
                  <dt>Observed / confirmed / available</dt><dd>{[event.observation_time,event.confirmation_time,event.available_at].map(utc).join(" / ")}</dd>
                  <dt>State and reason</dt><dd>{event.state.replaceAll("_"," ")} · {event.reason.replaceAll("_"," ")}</dd>
                  {event.swept_boundary!==undefined&&<><dt>Swept boundary / penetration / reclaim close</dt>
                    <dd>{event.swept_boundary} / {event.penetration} / {event.reclaim_close??"unconfirmed"}</dd></>}
                  <dt>Source</dt><dd>{result.provenance?.context_authority}</dd>
                  {rules.include_context&&<><dt>Available context</dt><dd>{Object.keys(event.context).length?
                    Object.entries(event.context).map(([kind,link])=><div key={kind}>{kind} · {link.timeframe} · {utc(link.available_at)} · {link.witness_id}</div>)
                    :"No matching evidence"}</dd></>}
                </dl>}
              </section>}
            </>;
          })()}
          {definition.parameters.ob && (() => {
            const rules=item.ob??definition.parameters.ob;
            const result=data.find(row=>row.id===item.id);
            const history=older?.key===body&&["current_obs","ob_formations","ob_revisions"].includes(older.collection)?older:undefined;
            const rows=history?history.events as ObEvent[]:[...(result?.current_obs?.items??[]),
              ...(result?.ob_revisions??[]).slice(-6).reverse(),...(result?.ob_formations??[]).slice(-6).reverse()];
            const selected=selectedOb?.key===fvgSelectionScope?rows.find(event=>event.id===selectedOb.id):undefined;
            const patch=(value:Partial<ObRules>)=>update(item.id,{ob:{...rules,...value}});
            const numeric:[keyof Pick<ObRules,"lookback"|"candidate_age"|"min_body"|"min_break"|"min_displacement"|
              "min_penetration_pct"|"max_age_bars">,string,number,number,number][]=[
              ["lookback","OB candidate lookback",1,100,1],["candidate_age","OB maximum candidate age",1,100,1],
              ["min_body","OB minimum body",0,Infinity,.00001],["min_break","OB minimum break distance",0,Infinity,.00001],
              ["min_displacement","OB minimum displacement",0,Infinity,.00001],["min_penetration_pct","OB minimum penetration (%)",0,100,.1],
              ["max_age_bars","OB maximum age",1,500,1],
            ];
            const page=(collection:Collection,cursor:string|null|undefined)=>{
              if(cursor){setPageError("");setPageRequest({key:body,collection,cursor});}
            };
            const utc=(time:number)=>new Date(time*1000).toISOString();
            return <>
              {numeric.map(([key,label,min,max,step])=><label key={key}>{label}<input type="number" min={min}
                max={Number.isFinite(max)?max:undefined} step={step} value={rules[key]} onChange={event=>{
                  const value=event.target.valueAsNumber;
                  if(value>=min&&value<=max&&(step!==1||Number.isInteger(value)))patch({[key]:value});
                }}/></label>)}
              <label>OB geometry <select value={rules.geometry} onChange={event=>patch({geometry:event.target.value as ObRules["geometry"]})}>
                <option value="wick">Wick</option><option value="body">Body</option></select></label>
              <label>OB mitigation <select value={rules.mitigation} onChange={event=>patch({mitigation:event.target.value as ObRules["mitigation"]})}>
                <option value="wick">Wick</option><option value="close">Close</option></select></label>
              <label><input type="checkbox" checked={rules.allow_choch}
                onChange={event=>patch({allow_choch:event.target.checked})}/> Allow confirmed CHOCH</label>
              <label><input type="checkbox" checked={rules.include_context}
                onChange={event=>patch({include_context:event.target.checked})}/> Include FVG and liquidity context</label>
              {rules.include_context&&contextSettings(item,result,"OB")}
              {(Object.keys(obVisibility) as (keyof ObVisibility)[]).map(key=><label key={key}>
                <input type="checkbox" checked={blockVisibility[key]}
                  onChange={event=>update(item.id,{obVisibility:{...blockVisibility,[key]:event.target.checked}})}/> Show OB {key}</label>)}
              {result?.ob_page&&<section aria-label="Order block evidence">
                <p>Current order blocks {result.current_obs?.returned} / {result.current_obs?.total} · {result.provenance?.context_authority}</p>
                {!!result.warnings.length&&<p>{result.warnings.join(" · ")}</p>}
                <button type="button" className="small-tool" disabled={!result.current_obs?.next_cursor||Boolean(pageRequest)}
                  onClick={()=>page("current_obs",result.current_obs?.next_cursor)}>More current order blocks</button>
                <button type="button" className="small-tool" disabled={!result.ob_page.formations.next_cursor||Boolean(pageRequest)}
                  onClick={()=>page("ob_formations",result.ob_page?.formations.next_cursor)}>Older OB formations</button>
                <button type="button" className="small-tool" disabled={!result.ob_page.revisions.next_cursor||Boolean(pageRequest)}
                  onClick={()=>page("ob_revisions",result.ob_page?.revisions.next_cursor)}>Older OB revisions</button>
                {history&&<>
                  <button type="button" className="small-tool" disabled={!history.metadata.next_cursor||Boolean(pageRequest)}
                    onClick={()=>page(history.collection,history.metadata.next_cursor)}>Next OB page</button>
                  <button type="button" className="small-tool" onClick={()=>{setPageRequest(undefined);setOlder(undefined);}}>Latest OB evidence</button>
                </>}
                {pageError&&<small role="alert">{pageError}</small>}
                {!!Object.keys(result.rejections??{}).length&&<p>{Object.entries(result.rejections??{}).map(
                  ([reason,count])=>reason.replaceAll("_"," ").toLowerCase()+" "+count).join(" · ")}</p>}
                {[...new Map(rows.map(event=>[event.id,event])).values()].map(event=><button key={event.id}
                  type="button" className="small-tool" onClick={()=>setSelectedOb({key:fvgSelectionScope,id:event.id})}>
                  {event.direction} · {event.state.replaceAll("_"," ")} · {event.mitigation_pct.toFixed(1)}%</button>)}
                {selected&&<dl aria-label="Selected order block">
                  <dt>Evidence eligibility</dt><dd>{timingLabel(selected.timing)}</dd>
                  <dt>Candidate and availability</dt><dd>{utc(selected.candidate_time)} / {utc(selected.candidate_available_at)}</dd>
                  <dt>Candidate OHLC</dt><dd>{selected.candidate_ohlc.join(" / ")}</dd>
                  <dt>Zone ({selected.geometry})</dt><dd>{selected.lower} – {selected.upper}</dd>
                  <dt>Remaining</dt><dd>{selected.remaining_lower} – {selected.remaining_upper} · {selected.mitigation_pct}%</dd>
                  <dt>{selected.break_type} and broken pivot</dt><dd>{selected.bos_id} / {selected.broken_pivot_id}</dd>
                  <dt>Break, confirmed, first visible</dt><dd>{[selected.break_time,selected.bos_confirmation_time,selected.first_visibility_time].map(utc).join(" / ")}</dd>
                  <dt>Revision observation and availability</dt><dd>{utc(selected.observation_time)} / {utc(selected.available_at)} · {selected.reason}</dd>
                  <dt>Source and versions</dt><dd>{result.provenance?.context_authority} · OB v1 · structure v{selected.structure_version} · identity v{selected.identity_policy_version}</dd>
                  {rules.include_context&&<><dt>Context at formation</dt><dd>{selected.context.status} · FVG {selected.context.fvg?.id??"unavailable"} · Sweep {selected.context.liquidity_sweep?.id??"unavailable"}</dd></>}
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
                  <dt>Evidence eligibility</dt><dd>{timingLabel(selected.timing)}</dd>
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
