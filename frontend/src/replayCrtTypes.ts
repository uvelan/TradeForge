import type {ReplaySourceBar, ReplaySourceCandle} from './ReplayEvidenceSources';
import type {ReplayZoneContinuity} from './replayZoneTypes';

export type ReplayCrtRevision = {
  id: string; range_id: string; reference_id: string;
  state: string; reason: string; revision: number;
  reference_start: number; reference_end: number; reference_available_at: number;
  reference_ohlc: [number, number, number, number]; reference_frame: 'H1'; reference_mode: 'aggregated';
  high: number; low: number; midpoint: number;
  observation_time: number; confirmation_time: number; available_at: number;
  sweep_side?: 'HIGH' | 'LOW' | null; swept_boundary?: number; sweep_extreme?: number;
  penetration?: number; reclaim_close?: number | null; sweep_id?: string; reclaim_id?: string;
  replacement_reference?: string;
  algorithm_version: number; identity_policy_version: number;
  continuity?: ReplayZoneContinuity;
};

export type ReplayCrtReceipt = ReplayCrtRevision & {
  reference_children: ReplaySourceBar[];
  revisions: ReplayCrtRevision[];
  evidence: ReplaySourceCandle[];
};

export type ReplayCrtPage = {
  session_id: string; view: string; cutoff: number; symbol: string; timeframe: string;
  source: string; feed_id: string; price_basis: string; authority: 'RESEARCH_ONLY';
  ranges: ReplayCrtRevision[]; total: number; previous_before: number | null;
  selected: ReplayCrtReceipt | null;
};
