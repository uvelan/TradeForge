export type SymbolName='EURUSD'|'GBPUSD'|'USDJPY'|'XAUUSD';
export type Timeframe='M1'|'M3'|'M5'|'M15'|'M30'|'H1'|'H4'|'D1';
export type SourceName='DEMO'|'MT5'|'MT5_SAVED'|'DUKASCOPY_SAVED';
export type ChartSource=SourceName|'BINANCE_SPOT';
export type ChartSymbol=SymbolName|'BTCUSDT';
export interface Candle {time:number;available_at?:number;open:number;high:number;low:number;close:number;volume?:number;
  availability_basis?:'ARCHIVE_SUPPLIED'|'MODELED_NOMINAL_CLOSE';source_partition_indexes?:number[]}
export type CandleRange = {start:number;end:number};
export interface CandleCoverage extends CandleRange {gaps:CandleRange[];duplicate_bars:number;truncated:boolean}
export interface ArchiveOrigin {file_observations?:string;source_completeness:string;reconstruction:string;source_sha256?:string;start?:number;end?:number;checked_bars?:number;missing_slots?:number[]}
export interface CandleResponse {source_completeness?:"UNVERIFIED";origin?:ArchiveOrigin;source:ChartSource;synthetic:boolean;price_basis:string;candles:Candle[];replay_cutoffs?:number[];dataset_id?:string;feed_id?:string;latest_unconfirmed?:boolean;coverage?:CandleCoverage;partitions?:{id:string;sha256:string}[]}
export interface BrokerSymbol {name:string;description:string;digits:number}
export type SignalReference={event_key:string;status:import("./SignalExplorer").SignalStatus};
export interface Plan {
 knowledge_cutoff_utc?:string;
 snapshot_id?:string;
 signal_reference?:SignalReference;
 symbol:SymbolName; direction:'BUY'|'SELL';strategy:string;session:'ASIA'|'LONDON'|'NEW_YORK'|'OTHER';timeframe:Timeframe;
 context:string;observation:string;evidence:string;hypothesis:string;setup:string;trigger:string;invalidation:string;management:string;
 entry:string;stop:string;target:string;equity_usd:string;risk_percent:string;pip_value_per_lot_usd:string;
}
export interface Trade {id:string;symbol:SymbolName;direction:'BUY'|'SELL';status:string;created_at:string;updated_at:string;version?:number;versions?:{id:string;version:number;plan:Plan;created_at:string}[];plan:Plan;outcome?:{pnl_usd:string;result_r:string}|null}
export interface Risk {stop_pips:string;maximum_planned_loss_usd:string;theoretical_lots:string;illustrative_lots:string;reward_risk:string;limitations:string[]}
export interface Analytics {trades:number;active_plans:number;closed:number;win_rate:number|null;expectancy_r:number|null;net_pnl_usd:string;profit_factor:number|null;note:string}
export interface Dataset {id:string;sha256?:string;provider:string;symbol:string;kind:string;timeframe:string|null;rows_count:number;created_at:string;relative_path:string;price_basis:string}
export interface Package {id:string;trade_id:string;plan_version:number;created_at:string;knowledge_cutoff:string}
export interface Review {original_integrity:string;duplicate_key:string|null;corrects_review_id:string|null;recorded_package?:0|1;has_original?:0|1;id:string;package_id:string;assessment:string;verification:string;created_at:string}
export interface JournalEntry {snapshot_id?:string;plan_reference?:PlanReference;supersedes?:string;tags?:string[];signal_reference?:SignalReference;id:string;kind:string;title:string;content:string;created_at:string}
export interface Overview {analytics:Analytics;mt5:{connected:boolean;reason?:string;broker?:string;mode?:string};datasets:Dataset[];recent_trades:Trade[];recent_reviews:Review[];journal_count:number}
export interface BacktestResult {strategy:string;source:string;synthetic:boolean;symbol:string;timeframe:string;count:number;eligible_signals:number;ambiguous_excluded:number;ambiguous_rate:number|null;missing_tick_windows?:number;candle_gaps?:{start:number;end:number}[];unresolved_outcomes?:{signal_time:number;reason:string}[];win_rate:number|null;expectancy_r:number|null;expectancy_bounds_r:{worst:number;best:number}|null;total_r:number|null;data_note:string;execution_model?:{version:number;max_quote_gap_ms:number;slippage_bps:string;commission_bps_per_side:string};lineage?:{candle_partitions:{id:string;sha256:string}[];tick_partition?:{id:string;sha256:string};tick_partitions?:{id:string;sha256:string}[]};trades:{entry_time:number;exit_time?:number;exit_time_ms?:number;exit?:number;exit_reason?:string;side:string;entry:number;stop:number;target:number;result_r:number}[]}

export interface BacktestRun {id:string;created_at:string;schema_version:1;engine_version:number;market:CandleResponse;result:BacktestResult}
export interface BacktestRunSummary {id:string;created_at:string;count:number;expectancy_r:number|null;source:string;timeframe:string;symbol:string;synthetic:boolean;model:'TICK'|'OHLC'}

export type ReplayPane = {symbol:string;timeframe:Timeframe} & (CandleResponse | {error:string});
export type ReplayViewId = 'primary' | 'comparison' | '2' | '3' | '4';
export type ReplaySelection = {view: ReplayViewId; time: number};
export interface ReplaySession {id:string;market:CandleResponse;views:Record<string,ReplayPane>;
 request?:{start?:number|null;end?:number|null;window_bars?:number|null};
 checkpoint:{position:number;bookmarks:number[];selected?:ReplaySelection|null}|null}
export interface ReplayViewRequest {view:string;symbol:string;timeframe:Timeframe}

export type PlanReference={trade_id:string;version:number};
export type JournalContext={snapshot_id?:string;plan_reference?:PlanReference;supersedes?:string;tags?:string[]};
