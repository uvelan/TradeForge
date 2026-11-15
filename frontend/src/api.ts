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
  risk:(plan:Plan)=>post<import('./types').Risk>('/risk/evaluate',{plan}),
  createExport:(id:string)=>post<{package_id:string;download_url:string}>(`/ai/exports/${id}`,{}),
  closeTrade:(id:string,pnl:string,r:string,notes:string)=>post(`/trades/${id}/close`,{pnl_usd:pnl,result_r:r,notes}),
  packages:()=>request<import('./types').Package[]>('/ai/packages'),
  reviews:()=>request<import('./types').Review[]>('/ai/reviews'),
  importReview:(input:unknown)=>post<{id:string;assessment:string;verification:string}>('/ai/reviews',input),
  journal:()=>request<import('./types').JournalEntry[]>('/journal'),
  addJournal:(kind:string,title:string,content:string)=>post('/journal',{kind,title,content}),
  datasets:()=>request<import('./types').Dataset[]>('/datasets'),
  backtest:(symbol:SymbolName,timeframe:Timeframe,source:SourceName,range?:CandleRange)=>
    request<import('./types').BacktestResult>('/backtest?'+new URLSearchParams({
      symbol,timeframe,source,...(range?{start:String(range.start),end:String(range.end)}:{}),
    })),
  importMt5:(symbol:SymbolName,timeframe:Timeframe,limit:number)=>post('/market/import/mt5',{symbol,timeframe,limit}),
  importBi5:async(symbol:SymbolName,day:string,file:File,hour:string)=>{
    const f=new FormData();f.append('file',file);
    const q=new URLSearchParams({symbol,day});if(hour)q.set('hour',hour);
    return request<{tick_dataset:{id:string;rows:number};candle_rows:number}>(`/market/import/bi5?${q}`,{method:'POST',body:f});
  },
  importDeals:()=>post('/market/import/mt5-deals?days=30',{}),
};
