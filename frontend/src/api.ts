import type {BrokerSymbol,CandleRange,ChartSource,Plan,SourceName,SymbolName,Timeframe} from './types';

export class ApiError extends Error {constructor(public status:number,message:string){super(message);this.name='ApiError'}}

async function request<T>(url:string,options?:RequestInit):Promise<T> {
  let response:Response;
  try {response=await fetch('/research/api'+url,{...options,headers:{...(options?.body instanceof FormData?{}:{'Content-Type':'application/json'}),...options?.headers}})}
  catch {throw new ApiError(0,'Backend unreachable. Start FastAPI on 127.0.0.1:8000.');}
  if(!response.ok){let message=`Request failed (${response.status})`;try{const body=await response.json();message=typeof body.detail==='string'?body.detail:JSON.stringify(body.detail||body)}catch{}throw new ApiError(response.status,message)}
  return response.json() as Promise<T>;
}
const post=<T>(url:string,payload:unknown)=>request<T>(url,{method:'POST',body:JSON.stringify(payload)});
export const api={
  get:<T>(path:string)=>request<T>(path),post,
  overview:()=>request<import('./types').Overview>('/overview'),
  brokerSymbols:()=>request<BrokerSymbol[]>('/market/mt5/symbols'),
  candles:(symbol:string,timeframe:Timeframe,source:ChartSource,limit=240,includeLatest=false,range?:CandleRange)=>
    request<import('./types').CandleResponse>('/market/candles?'+new URLSearchParams({
      symbol,timeframe,source,limit:String(limit),include_latest:String(includeLatest),
      ...(range?{start:String(range.start),end:String(range.end)}:{}),
    })),
  quote:(symbol:SymbolName,source:'MT5'|'DEMO')=>request<{symbol:string;bid:number;ask:number;stale:boolean;source:string}>(`/market/quote?symbol=${symbol}&source=${source}`),
  trades:()=>request<import('./types').Trade[]>('/trades'),
  createPlan:(plan:Plan)=>post<import('./types').Trade>('/trades',plan),
  trade:(id:string)=>request<import('./types').Trade>('/trades/'+encodeURIComponent(id)),
  revisePlan:(id:string,version:number,plan:Plan)=>post<import('./types').Trade>(
    '/trades/'+encodeURIComponent(id)+'/revisions?expected_version='+version,plan),
  declinePlan:(id:string,reason:string)=>post<import('./types').Trade>('/trades/'+encodeURIComponent(id)+'/decline',{reason}),
  risk:(plan:Plan)=>post<import('./types').Risk>('/risk/evaluate',{plan}),
  createExport:(id:string,version?:number)=>post<{package_id:string;download_url:string}>(`/ai/exports/${id}${version?`?plan_version=${version}`:""}`,{}),
  closeTrade:(id:string,pnl:string,r:string,notes:string)=>post(`/trades/${id}/close`,{pnl_usd:pnl,result_r:r,notes}),
  packages:()=>request<import('./types').Package[]>('/ai/packages'),
  reviews:()=>request<import('./types').Review[]>('/ai/reviews'),
  importReview:(input:string)=>request<{id:string;assessment:string;verification:string}>('/ai/reviews',{method:'POST',body:input}),
  journal:()=>request<import('./types').JournalEntry[]>('/journal'),
  addJournal:(kind:string,title:string,content:string,signal_reference?:import('./types').SignalReference,context?:import('./types').JournalContext)=>post<{id:string}>('/journal',{kind,title,content,signal_reference,...context}),
  datasets:()=>request<import('./types').Dataset[]>('/datasets'),
  backtest:(symbol:SymbolName,timeframe:Timeframe,source:SourceName,range?:CandleRange,
    tick?:{id:string;slippage:string;commission:string;maxGap:string})=>
    request<import('./types').BacktestResult>('/backtest?'+new URLSearchParams({
      symbol,timeframe,source,...(range?{start:String(range.start),end:String(range.end)}:{}),
      ...(tick?{...(tick.id==="archive-range"?{tick_archive_range:"true"}:{tick_dataset_id:tick.id}),slippage_bps:tick.slippage,commission_bps:tick.commission,max_quote_gap_seconds:tick.maxGap}:{}),
    })),
  saveBacktest:(symbol:SymbolName,timeframe:Timeframe,source:SourceName,range?:CandleRange,
    tick?:{id:string;slippage:string;commission:string;maxGap:string})=>
    post<import('./types').BacktestRun>('/backtest/runs',{
      symbol,timeframe,source,...range,...(tick?{...(tick.id==="archive-range"?{tick_archive_range:true}:{tick_dataset_id:tick.id}),slippage_bps:tick.slippage,
        commission_bps:tick.commission,max_quote_gap_seconds:Number(tick.maxGap)}:{}),
    }),
  backtestRuns:()=>request<import('./types').BacktestRunSummary[]>('/backtest/runs'),
  backtestRun:(id:string)=>request<import('./types').BacktestRun>('/backtest/runs/'+encodeURIComponent(id)),
  startReplay:(symbol:string,timeframe:Timeframe,source:ChartSource,views:import('./types').ReplayViewRequest[],range?:CandleRange)=>
    post<import('./types').ReplaySession>('/replay/sessions',{symbol,timeframe,source,views,...range}),
  replays:(symbol:string,timeframe:Timeframe,source:ChartSource)=>
    request<{id:string;position:number;updated_at:string}[]>('/replay/sessions?'+new URLSearchParams({symbol,timeframe,source})),
  replay:(id:string)=>request<import('./types').ReplaySession>('/replay/sessions/'+encodeURIComponent(id)),
  saveCheckpoint:(id:string,position:number,bookmarks:number[])=>post('/replay/sessions/'+encodeURIComponent(id)+'/checkpoint',{position,bookmarks}),
  saveSnapshot:(png:string,symbol:string,timeframe:Timeframe,source:string,cutoff?:number,
    signal_reference?:import('./types').SignalReference)=>post<{id:string}>('/chart-snapshots',{png,symbol,timeframe,source,cutoff,signal_reference}),
  importMt5:(symbol:SymbolName,timeframe:Timeframe,limit:number)=>post('/market/import/mt5',{symbol,timeframe,limit}),
  importBi5:async(symbol:SymbolName,day:string,file:File,hour:string)=>{
    const f=new FormData();f.append('file',file);
    const q=new URLSearchParams({symbol,day});if(hour)q.set('hour',hour);
    return request<{tick_dataset:{id:string;rows:number};candle_rows:number}>(`/market/import/bi5?${q}`,{method:'POST',body:f});
  },
  importDeals:()=>post('/market/import/mt5-deals?days=30',{}),
};
