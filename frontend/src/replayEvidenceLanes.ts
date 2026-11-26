import type {CandleResponse, ReplaySession, Timeframe} from './types';

export type ReplayEvidenceProps = {
  session: ReplaySession; cutoff: number; symbol: string; timeframe: Timeframe; linkedViews: number;
};
export type ReplayLane = {view: string; symbol: string};

export const replayLaneLabel = (view: string) =>
  view === 'primary' ? 'Primary' : view === 'comparison' ? 'Comparison' : 'View ' + view;

function supportsEvidence(market: CandleResponse, symbol: string, timeframe: Timeframe) {
  return market.source === 'DUKASCOPY_SAVED' && market.price_basis === 'BID' &&
    Boolean(market.partitions?.length) && timeframe === 'M5' && (symbol === 'EURUSD' || symbol === 'GBPUSD');
}

export function replayEvidenceLanes(session: ReplaySession, symbol: string, timeframe: Timeframe, linkedViews: number) {
  const lanes: ReplayLane[] = [];
  if (supportsEvidence(session.market, symbol, timeframe)) lanes.push({view: 'primary', symbol});
  for (const [view, market] of Object.entries(session.views)) {
    if ((view === 'comparison' || Number(view) <= linkedViews) &&
        !('error' in market) && supportsEvidence(market, market.symbol, market.timeframe)) {
      lanes.push({view, symbol: market.symbol});
    }
  }
  return lanes;
}
