import type {ReplaySourceCandle} from './ReplayEvidenceSources';

export const confluenceProfile = 'CRT_SMT_M5_V1';

export type ConfluenceStatus = 'ALIGNED' | 'CONTRADICTED' | 'NOT_ALIGNED' | 'INCOMPLETE';
export type ConfluenceDirection = 'BULLISH' | 'BEARISH' | null;

export type ConfluenceCandle = ReplaySourceCandle & {
  symbol: 'EURUSD' | 'GBPUSD';
  source: 'DUKASCOPY_SAVED';
  feed_id: string;
  price_basis: 'BID';
  timeframe: 'M5';
};

export type ConfluenceSource = Pick<ConfluenceCandle, 'symbol' | 'source' | 'feed_id' | 'price_basis' | 'timeframe' | 'partitions'> & {
  start: number;
  end: number;
};

export type ConfluenceCoverageIssue = {
  symbol: 'EURUSD' | 'GBPUSD';
  reason: string;
  missing_intervals: {start: number; end: number}[];
};

export type ConfluenceComponent = {
  family_id: string | null;
  revision_id: string | null;
  revision: number | null;
  state: string;
  reason: string;
  condition: 'SATISFIED' | 'NOT_SATISFIED' | 'INCOMPLETE';
  direction: ConfluenceDirection;
  available_at: number | null;
  observation_time: number | null;
  confirmation_time: number | null;
  coverage_reason?: string;
  coverage_issues?: ConfluenceCoverageIssue[];
  published_state?: string;
  sources: ConfluenceSource[];
  evidence: ConfluenceCandle[];
};

export type ConfluencePin = {
  profile: typeof confluenceProfile;
  rule_version: number;
  cutoff: number;
  receipt_id: string;
};

export type ConfluenceReceipt = ConfluencePin & {
  session_id: string;
  authority: 'RESEARCH_ONLY';
  status: ConfluenceStatus;
  reason: string;
  direction: ConfluenceDirection;
  first_available_at: number | null;
  components: {crt: ConfluenceComponent; smt: ConfluenceComponent};
};
