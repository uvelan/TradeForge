import type {ReplaySourceCandle} from './ReplayEvidenceSources';

export type ReplayZoneMitigation = 'wick' | 'close';
export type ReplayZoneRevision = {
  id: string; zone_id: string; type: 'FVG' | 'OB'; direction: 'BULLISH' | 'BEARISH';
  state: string; reason: string; revision: number;
  lower: number; upper: number; remaining_lower: number; remaining_upper: number; mitigation_pct: number;
  anchor_time: number; formation_time: number; formation_confirmation_time: number;
  formation_available_at: number; observation_time: number; confirmation_time: number;
  available_at: number; observed_price: number | null;
  algorithm_version: number; identity_policy_version: number;
};

export type ReplayZoneStructure = {
  id: string; type: string; direction: string; side: string; status: string;
  broken_pivot_id: string; level_price: number; level_time: number; level_available_at: number;
  break_price: number; break_time: number; confirmation_time: number; available_at: number;
  state_before: string; state_after: string; candidate_id: string | null;
  pivot: {id: string; side: string; price: number; pivot_time: number; confirmation_bar_time: number;
    confirmation_time: number; available_at: number; classification: string};
  evidence: ReplaySourceCandle[];
};

export type ReplayZoneReceipt = ReplayZoneRevision & {
  revisions: ReplayZoneRevision[];
  evidence: ReplaySourceCandle[];
  structure: ReplayZoneStructure | null;
};

export type ReplayZonePage = {
  session_id: string; view: string; cutoff: number; symbol: string; timeframe: string;
  source: string; feed_id: string; price_basis: string; authority: 'RESEARCH_ONLY';
  rules: {fvg: {mitigation: ReplayZoneMitigation}; ob: {mitigation: ReplayZoneMitigation}};
  zones: ReplayZoneRevision[]; total: number; previous_before: number | null;
  selected: ReplayZoneReceipt | null;
};
