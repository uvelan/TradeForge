import {ReplayH1Evidence} from './ReplayH1Evidence';
import {ReplayStructureEvidence} from './ReplayStructureEvidence';
import {ReplayZoneEvidence} from './ReplayZoneEvidence';
import {ReplayCrtEvidence} from './ReplayCrtEvidence';
import {ReplayConfluenceEvidence} from './ReplayConfluenceEvidence';
import type {ReplaySession, Timeframe} from './types';
import type {ReplayEvidenceProps} from './replayEvidenceLanes';

const REPLAY_EVIDENCE_PANELS: ReadonlyArray<readonly [prefix: string, Component: (props: ReplayEvidenceProps) => ReturnType<typeof ReplayH1Evidence>]> = [
  ['h1', ReplayH1Evidence],
  ['events', ReplayStructureEvidence],
  ['zones', ReplayZoneEvidence],
  ['crt', ReplayCrtEvidence],
  ['confluence', ReplayConfluenceEvidence],
];

export function ReplayEvidencePanels({
  session,
  cutoff,
  symbol,
  timeframe,
  linkedViews,
}: {
  session: ReplaySession;
  cutoff: number;
  symbol: string;
  timeframe: Timeframe;
  linkedViews: number;
}) {
  return <>
    {REPLAY_EVIDENCE_PANELS.map(([prefix, Panel]) => (
      <Panel
        key={prefix === 'h1' ? session.id : `${prefix}:${session.id}`}
        session={session}
        cutoff={cutoff}
        symbol={symbol}
        timeframe={timeframe}
        linkedViews={linkedViews}
      />
    ))}
  </>;
}
