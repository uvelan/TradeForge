import {useEffect,useState} from "react";
import type {Candle,CandleResponse,Timeframe} from "./types";

type Pivot = {time:number; price:number; confirmed_at:number};
type Status = "SMT_CANDIDATE" | "SMT_CONFIRMED" | "SMT_INVALIDATED";
export type SmtEvent = {
  id:string;
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

export function smtFeed(symbol: string, data: CandleResponse | null | undefined) {
  if(!data?.feed_id)return undefined;
  return {symbol,source:data.source,feed_id:data.feed_id,price_basis:data.price_basis,dataset_id:data.dataset_id};
}

export function useSmt(primary: Candle[], comparison: Candle[] | undefined, timeframe: Timeframe, enabled: boolean, rules:SmtRules,
  first:ReturnType<typeof smtFeed>, second:ReturnType<typeof smtFeed>, replay:boolean, refresh:number) {
  const body=enabled&&primary.length&&comparison?.length ? JSON.stringify({
    primary,comparison,timeframe,...rules,primary_feed:first,comparison_feed:second,replay,
  }) : "";
  const [result,setResult]=useState<{body:string;refresh:number;events?:SmtEvent[];authority?:string;error?:string}>();
  useEffect(()=>{
    if(!body)return;
    const controller=new AbortController();
    const timer=window.setTimeout(async()=>{
      try{
        const response=await fetch("/research/api/indicators/smt",{
          method:"POST",headers:{"Content-Type":"application/json"},body,signal:controller.signal,
        });
        if(!response.ok){
          const failure=await response.json().catch(()=>null);
          throw new Error(typeof failure?.detail==="string"?failure.detail:"SMT calculation unavailable");
        }
        const data=await response.json() as {events:SmtEvent[];authority:string};
        if(!controller.signal.aborted){
          setResult({body,refresh,events:data.events,authority:data.authority});
        }
      }catch(error){
        if(!controller.signal.aborted)setResult({body,refresh,error:error instanceof Error?error.message:"SMT calculation unavailable"});
      }
    },80);
    return()=>{window.clearTimeout(timer);controller.abort()};
  },[body,refresh]);
  const current=result?.body===body&&result.refresh===refresh?result:undefined;
  return {...current,pending:Boolean(body&&!current)};
}
