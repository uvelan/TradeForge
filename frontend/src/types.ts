export type SymbolName='EURUSD'|'GBPUSD'|'USDJPY'|'XAUUSD';
export type Timeframe='M1'|'M3'|'M5'|'M15'|'M30'|'H1'|'H4'|'D1';
export type SourceName='DEMO'|'MT5'|'MT5_SAVED'|'DUKASCOPY_SAVED';
export type ChartSource=SourceName|'BINANCE_SPOT';
export type ChartSymbol=SymbolName|'BTCUSDT';
export interface Candle {time:number;open:number;high:number;low:number;close:number;volume?:number}
export interface CandleResponse {source:ChartSource;synthetic:boolean;price_basis:string;candles:Candle[];dataset_id?:string;feed_id?:string;latest_unconfirmed?:boolean}
export interface BrokerSymbol {name:string;description:string;digits:number}
export interface Plan {
 symbol:SymbolName; direction:'BUY'|'SELL';strategy:string;session:'ASIA'|'LONDON'|'NEW_YORK'|'OTHER';timeframe:Timeframe;
 context:string;observation:string;evidence:string;hypothesis:string;setup:string;trigger:string;invalidation:string;management:string;
 entry:string;stop:string;target:string;equity_usd:string;risk_percent:string;pip_value_per_lot_usd:string;
}
export interface Trade {id:string;symbol:SymbolName;direction:'BUY'|'SELL';status:string;created_at:string;updated_at:string;version?:number;plan:Plan;outcome?:{pnl_usd:string;result_r:string}|null}
export interface Risk {stop_pips:string;maximum_planned_loss_usd:string;theoretical_lots:string;illustrative_lots:string;reward_risk:string;limitations:string[]}
export interface Analytics {trades:number;active_plans:number;closed:number;win_rate:number|null;expectancy_r:number|null;net_pnl_usd:string;profit_factor:number|null;note:string}
export interface Dataset {id:string;provider:string;symbol:string;kind:string;timeframe:string|null;rows_count:number;created_at:string;relative_path:string;price_basis:string}
export interface Package {id:string;trade_id:string;plan_version:number;created_at:string;knowledge_cutoff:string}
export interface Review {id:string;package_id:string;assessment:string;verification:string;created_at:string}
export interface JournalEntry {id:string;kind:string;title:string;content:string;created_at:string}
export interface Overview {analytics:Analytics;mt5:{connected:boolean;reason?:string;broker?:string;mode?:string};datasets:Dataset[];recent_trades:Trade[];recent_reviews:Review[];journal_count:number}
export interface BacktestResult {strategy:string;source:string;synthetic:boolean;symbol:string;timeframe:string;count:number;ambiguous_excluded:number;win_rate:number|null;expectancy_r:number|null;total_r:number;data_note:string;trades:{entry_time:number;side:string;entry:number;stop:number;target:number;result_r:number}[]}
