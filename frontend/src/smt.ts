import {useEffect,useMemo,useState} from "react";
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
  status:Status;
  transitions:{status:Status;time:number}[];
};

export type SmtRules = {period:number;alignment_bars:number;tolerance_bps:number;confirmation_bars:number};

export function smtFeed(symbol: string, data: CandleResponse | null | undefined) {
  if(!data?.feed_id)return undefined;
  return {symbol,source:data.source,feed_id:data.feed_id,price_basis:data.price_basis,dataset_id:data.dataset_id};
}

export function useSmt(primary: Candle[], comparison: Candle[] | undefined, timeframe: Timeframe, enabled: boolean, rules:SmtRules,
  first:ReturnType<typeof smtFeed>, second:ReturnType<typeof smtFeed>, replay:boolean) {
  const scope=JSON.stringify([first,second,timeframe,rules,enabled,replay]);
  const alertAfter=useMemo(()=>Math.floor(Date.now()/1000),[scope]);
  const body=enabled&&primary.length&&comparison?.length ? JSON.stringify({
    primary,comparison,timeframe,...rules,primary_feed:first,comparison_feed:second,replay,
    alert_after:!replay&&first?.source==="MT5"?alertAfter:undefined,
  }) : "";
  const [result,setResult]=useState<{body:string;events?:SmtEvent[];error?:string}>();
  const [notification,setNotification]=useState<{scope:string;event:SmtEvent}>();
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
        const data=await response.json() as {events:SmtEvent[];alerts:SmtEvent[]};
        if(!controller.signal.aborted){
          setResult({body,events:data.events});
          const latest=data.alerts?.at(-1);
          if(latest)setNotification({scope,event:latest});
        }
      }catch(error){
        if(!controller.signal.aborted)setResult({body,error:error instanceof Error?error.message:"SMT calculation unavailable"});
      }
    },80);
    return()=>{window.clearTimeout(timer);controller.abort()};
  },[body,scope]);
  return {...(result?.body===body?result:undefined),alert:notification?.scope===scope?notification.event:undefined};
}
