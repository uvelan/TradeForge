import {useEffect,useMemo,useState} from "react";
import type {Candle,CandleResponse,Timeframe} from "./types";

type Pivot = {time:number; price:number; confirmed_at:number};
type Status = "SMT_CANDIDATE" | "SMT_CONFIRMED" | "SMT_INVALIDATED";
export type SmtEvent = {
  id:string;
  event_key?:string;
  direction: "bearish" | "bullish";
  leader: "primary" | "comparison";
  primary:{previous:Pivot;current:Pivot};
  comparison:{previous:Pivot;current:Pivot};
  confirmed_at: number | null;
  confirmation_bar_time?: number | null;
  status:Status;
  transitions:{status:Status;time:number}[];
  timing?:{
    pivot_right_bars:number;
    all_pivots_confirmed_at:number;
    alignment_window_bars:number;
    matching_closed_at:number;
    required_shared_confirmation_bars:number;
  };
};

export type SmtRules = {period:number;alignment_bars:number;tolerance_bps:number;confirmation_bars:number};

export type SmtCoverage = {
  status:"CONTIGUOUS" | "DEGRADED";
  reason:"PAIRED_HISTORY_GAP" | "PAIRED_TAIL_UNAVAILABLE" | null;
};

export function smtCoverageNotice(coverage?: SmtCoverage): string | undefined {
  if(coverage?.status!=="DEGRADED")return undefined;
  return coverage.reason==="PAIRED_TAIL_UNAVAILABLE"
    ? "SMT coverage degraded · latest paired candles unavailable"
    : "SMT coverage degraded · gap in paired candle history";
}

export function smtFeed(symbol: string, data: CandleResponse | null | undefined) {
  if(!data?.feed_id)return undefined;
  return {symbol,source:data.source,feed_id:data.feed_id,price_basis:data.price_basis,dataset_id:data.dataset_id,
    partitions:data.partitions,archive_range:data.coverage?[data.coverage.start,data.coverage.end]:undefined};
}

export function useSmt(primary: Candle[], comparison: Candle[] | undefined, timeframe: Timeframe, enabled: boolean, rules:SmtRules,
  first:ReturnType<typeof smtFeed>, second:ReturnType<typeof smtFeed>, replay:boolean, refresh:number, step:number) {
  const live=!replay&&first?.source==="MT5"&&second?.source==="MT5";
  const scope=JSON.stringify([first,second,timeframe,rules,enabled,replay]);
  const alertAfter=useMemo(()=>Math.floor(Date.now()/1000),[scope]);
  const latestPrimary=primary.at(-1);
  const latestComparison=comparison?.at(-1);
  const body=enabled&&latestPrimary&&latestComparison ? JSON.stringify(live?{
    primary_symbol:first.symbol,comparison_symbol:second.symbol,timeframe,...rules,alert_after:alertAfter,
    available_through:Math.min(latestPrimary.time,latestComparison.time)+step,
  }:{
    primary,comparison,timeframe,...rules,primary_feed:first,comparison_feed:second,replay,
  }) : "";
  const endpoint=live?"/research/api/indicators/smt/live":"/research/api/indicators/smt";
  const [result,setResult]=useState<{body:string;refresh:number;events?:SmtEvent[];authority?:string;coverage?:SmtCoverage;error?:string}>();
  const [notice,setNotice]=useState<{scope:string;alert:SmtEvent}>();
  useEffect(()=>{
    if(!body)return;
    const controller=new AbortController();
    const timer=window.setTimeout(async()=>{
      try{
        const response=await fetch(endpoint,{
          method:"POST",headers:{"Content-Type":"application/json"},body,signal:controller.signal,
        });
        if(!response.ok){
          const failure=await response.json().catch(()=>null);
          throw new Error(typeof failure?.detail==="string"?failure.detail:"SMT calculation unavailable");
        }
        const data=await response.json() as {events:SmtEvent[];authority:string;alerts:SmtEvent[];corrected_ids?:string[];coverage?:SmtCoverage};
        if(!controller.signal.aborted){
          setResult({body,refresh,events:data.events,authority:data.authority,coverage:data.coverage});
          const alert=data.alerts?.at(-1);
          if(alert)setNotice({scope,alert});
          else setNotice(previous=>{
            if(previous?.scope!==scope)return previous;
            const matching=data.events.find(event=>event.id===previous.alert.id);
            const withdrawn=data.corrected_ids?.includes(previous.alert.id) ||
              (matching ? matching.status!=="SMT_CONFIRMED" : data.coverage?.status!=="DEGRADED");
            return withdrawn?undefined:previous;
          });
        }
      }catch(error){
        if(!controller.signal.aborted)setResult({body,refresh,error:error instanceof Error?error.message:"SMT calculation unavailable"});
      }
    },80);
    return()=>{window.clearTimeout(timer);controller.abort()};
  },[body,refresh,endpoint,scope]);
  const current=result?.body===body&&result.refresh===refresh?result:undefined;
  return {...current,pending:Boolean(body&&!current),alert:notice?.scope===scope?notice.alert:undefined};
}
