import {useState, type ReactNode} from 'react';
import {EvidenceDisclosure, ReplayReceiptFields, ReplaySourcePins, ReplaySourceTable, evidenceWords as words, replayUtc as utc,
  useReplayEndpoint, type ReplaySourceCandle} from './ReplayEvidenceSources';
import {replayEvidenceLanes, replayLaneLabel, type ReplayEvidenceProps, type ReplayLane} from './replayEvidenceLanes';
import type {ReplayZoneContinuity, ReplayZoneMitigation, ReplayZonePage, ReplayZoneReceipt, ReplayZoneRevision,
  ReplayZoneStructure} from './replayZoneTypes';
import {useWorkspacePreference} from './useWorkspacePreference';

const title = 'Replay FVG and order blocks';
const zoneType = (type: ReplayZoneRevision['type']) => type === 'FVG' ? 'FVG' : 'Order block';
const bounds = (lower: number, upper: number) => lower + ' – ' + upper;

type Fields = [string, ReactNode][];
const ReceiptFields = ReplayReceiptFields;

export function ReplayZoneEvidence({session, cutoff, symbol, timeframe, linkedViews}: ReplayEvidenceProps) {
  const [preferred, setPreferred] = useWorkspacePreference<string>('replay-zones-pane.' + session.id, 'primary');
  const [open, setOpen] = useWorkspacePreference('replay-zones-open.' + session.id, 'off', ['on', 'off']);
  const [mitigation, setMitigation] = useWorkspacePreference<ReplayZoneMitigation>(
    'replay-zones-mitigation.' + session.id, 'wick', ['wick', 'close']);
  const lanes = replayEvidenceLanes(session, symbol, timeframe, linkedViews);
  const lane = lanes.find(choice => choice.view === preferred) ?? lanes[0];
  if (!lane) return null;
  return <details className="card replay-zone-evidence" open={open === 'on'}
    onToggle={event => setOpen(event.currentTarget.open ? 'on' : 'off')}>
    <summary>{title}</summary>
    {open === 'on' && <section aria-label={title}>
      <p>M5 replay cutoff {utc(cutoff)}</p>
      <div className="research-controls">
        {lanes.length > 1 && <label>Zone chart pane <select value={lane.view} onChange={event => setPreferred(event.target.value)}>
          {lanes.map(choice => <option key={choice.view} value={choice.view}>
            {replayLaneLabel(choice.view)} · {choice.symbol}
          </option>)}
        </select></label>}
        <label>Zone mitigation <select value={mitigation} onChange={event => setMitigation(event.target.value as ReplayZoneMitigation)}>
          <option value="wick">Wick</option>
          <option value="close">Close</option>
        </select></label>
      </div>
      {lanes.map(choice => <ZoneSelection key={choice.view + ':' + mitigation} sessionId={session.id}
        cutoff={cutoff} lane={choice} active={choice.view === lane.view} mitigation={mitigation}/>)}
    </section>}
  </details>;
}

type Query = {sessionId: string; cutoff: number; lane: ReplayLane; mitigation: ReplayZoneMitigation};

function ZoneSelection({sessionId, cutoff, lane, active, mitigation}: Query & {active: boolean}) {
  const [id, setId] = useWorkspacePreference<string>(
    ['replay-zones-selected', sessionId, lane.view, mitigation].join('.'), '');
  return active && <ZoneResults key={cutoff} sessionId={sessionId} cutoff={cutoff}
    lane={lane} mitigation={mitigation} id={id} onSelect={setId}/>;
}

function ZoneResults({sessionId, cutoff, lane, mitigation, id, onSelect}: Query & {id: string; onSelect: (id: string) => void}) {
  const [before, setBefore] = useState<number>();
  const {view, symbol} = lane;
  const query = new URLSearchParams({view, cutoff: String(cutoff), mitigation,
    ...(id ? {zone_id: id} : {}), ...(before == null ? {} : {before: String(before)}),
  });
  const url = '/replay/sessions/' + encodeURIComponent(sessionId) + '/zones?' + query;
  const {page, pending, error: failure} = useReplayEndpoint<ReplayZonePage>(url, result => {
    if (result.session_id !== sessionId || result.view !== view || result.symbol !== symbol || result.cutoff !== cutoff ||
        result.source !== 'DUKASCOPY_SAVED' || result.price_basis !== 'BID' || result.timeframe !== 'M5' ||
        result.authority !== 'RESEARCH_ONLY' || result.rules.fvg.mitigation !== mitigation || result.rules.ob.mitigation !== mitigation) {
      throw new Error('Replay zones did not match this pane, cutoff, and mitigation rule.');
    }
  });
  if (failure) return <p role="alert">{failure}</p>;
  if (!page) return <p role="status">Loading replay zones…</p>;
  const selected = !pending && page.selected?.zone_id === id ? page.selected : null;
  return <>
    <p>{page.symbol} {page.timeframe} · {page.price_basis} · {page.feed_id} · Research only</p>
    <div className="research-controls">
      <label>Replay zone <select value={id} onChange={event => onSelect(event.target.value)}>
        <option value="">Choose FVG or order block</option>
        {id && !page.zones.some(zone => zone.zone_id === id) && <option value={id} disabled={!selected}>
          {selected ? zoneLabel(selected) : pending ? 'Loading selected zone…' : 'Selected zone unavailable'}
        </option>}
        {[...page.zones].reverse().map(zone => <option key={zone.zone_id} value={zone.zone_id}>{zoneLabel(zone)}</option>)}
      </select></label>
      {page.previous_before != null && <button type="button" className="small-tool" disabled={pending}
        onClick={() => setBefore(page.previous_before ?? undefined)}>Older replay zones</button>}
      {before != null && <button type="button" className="small-tool" onClick={() => setBefore(undefined)}>
        Latest replay zones
      </button>}
      {page.total > page.zones.length && <small>{page.zones.length} of {page.total} zones</small>}
    </div>
    {selected ? <ZoneReceipt key={selected.id} zone={selected} mitigation={mitigation}/> : id ?
      <p role="status">{pending ? 'Loading selected replay zone…' : 'Selected zone unavailable at this cutoff.'}</p> :
      page.zones.length === 0 && <p role="status">No replay zones at this cutoff.</p>}
  </>;
}

function zoneLabel(zone: ReplayZoneRevision) {
  return [zoneType(zone.type), zone.direction, words(zone.state), utc(zone.formation_available_at)].join(' · ');
}

function ZoneReceipt({zone, mitigation}: {zone: ReplayZoneReceipt; mitigation: ReplayZoneMitigation}) {
  const current = zone.revisions.at(-1);
  const fields: Fields = [
    ['Zone', zoneType(zone.type)], ['Direction', zone.direction],
    ['Bounds', bounds(zone.lower, zone.upper)], ['Remaining bounds', bounds(zone.remaining_lower, zone.remaining_upper)],
    ['State', words(zone.state)], ['Reason', words(zone.reason)],
    ['Mitigation', zone.mitigation_pct + '% · ' + mitigation], ['Revision', zone.revision],
    ['Anchor candle', utc(zone.anchor_time)], ['Formation candle', utc(zone.formation_time)],
    ['Formation confirmed', utc(zone.formation_confirmation_time)], ['First available', utc(zone.formation_available_at)],
    ['Revision observed', utc(zone.observation_time)], ['Revision confirmed', utc(zone.confirmation_time)],
    ['Revision available', utc(zone.available_at)],
    ...(zone.observed_price != null ? [['Observed price', zone.observed_price] as Fields[number]] : []),
    ['Algorithm version', zone.algorithm_version], ['Identity policy version', zone.identity_policy_version],
    ['Zone ID', <code className="evidence-id">{zone.zone_id}</code>],
    ['Revision event ID', <code className="evidence-id">{zone.id}</code>],
  ];
  return <article aria-label="Selected replay zone" data-zone-id={zone.zone_id}
    data-revision-id={zone.id} data-available-at={zone.available_at}>
    <ReceiptFields label="Zone receipt" fields={fields}/>
    {current?.id === zone.id && current.continuity &&
      <ZoneGapEvidence id={current.id} continuity={current.continuity} evidence={zone.evidence}/>}
    {zone.revisions.length > 0 && <EvidenceDisclosure label={zone.revisions.length + ' lifecycle revisions'}>
      <ol aria-label="Zone lifecycle revisions">{zone.revisions.map(revision => <li key={revision.id}>
        {utc(revision.available_at)} · {words(revision.state)} · {words(revision.reason)} · {revision.mitigation_pct}% ·
        remaining {bounds(revision.remaining_lower, revision.remaining_upper)}
      </li>)}</ol>
    </EvidenceDisclosure>}
    {zone.evidence.length > 0 && <EvidenceDisclosure label={zone.evidence.length + ' direct source candles'}>
      <ReplaySourcePins rows={zone.evidence}/>
      <ReplaySourceTable name="Direct zone source candles" rows={zone.evidence} direct/>
    </EvidenceDisclosure>}
    {zone.structure && <OrderBlockStructure structure={zone.structure}/>}
  </article>;
}

function ZoneGapEvidence({id, continuity, evidence}: {
  id: string; continuity: ReplayZoneContinuity; evidence: ReplaySourceCandle[];
}) {
  const boundary = (time: number, role: string) => {
    const source = evidence.find(row => row.time === time &&
      row.roles.some(item => item.role === role && item.event_id === id));
    return <>
      <div>{utc(time)}</div>
      {source && <>
        <div className="evidence-id">Source ID <code>{source.source_id}</code></div>
        <ReplaySourcePins rows={[source]}/>
      </>}
    </>;
  };
  const intervals = continuity.missing_open_intervals;
  return <section aria-label={intervals ? 'Missing open candles evidence' : 'Unverifiable closure evidence'}
    data-revision-id={id}>
    <ReceiptFields label="Gap evidence" fields={[
      ['Missing candle opens [start, end) · UTC', intervals
        ? intervals.map(({start, end}) => <div key={start}>[{utc(start)}, {utc(end)})</div>)
        : 'Missing open intervals could not be verified under the market calendar.'],
      ['Previous observed candle open', boundary(continuity.previous_observed_open, 'GAP_PREVIOUS_OBSERVED')],
      ['Next observed candle open', boundary(continuity.next_observed_open, 'GAP_NEXT_OBSERVED')],
    ]}/>
  </section>;
}

function OrderBlockStructure({structure}: {structure: ReplayZoneStructure}) {
  const {pivot} = structure;
  return <EvidenceDisclosure label={'Qualifying structure event · ' + words(structure.type)}>
    <ReceiptFields label="Qualifying structure receipt" fields={[
      ['Event', words(structure.type)], ['Direction', structure.direction],
      ['Broken level', structure.level_price], ['Level first available', utc(structure.level_available_at)],
      ['Crossing price', structure.break_price], ['Break candle', utc(structure.break_time)],
      ['Confirmed', utc(structure.confirmation_time)], ['First available', utc(structure.available_at)],
      ['Structure event ID', <code className="evidence-id">{structure.id}</code>],
      ['Broken pivot ID', <code className="evidence-id">{structure.broken_pivot_id}</code>],
      ['Pivot', words(pivot.side) + ' · ' + words(pivot.classification) + ' · ' + pivot.price],
      ['Pivot candle', utc(pivot.pivot_time)], ['Pivot confirmed', utc(pivot.confirmation_time)],
      ['Pivot first available', utc(pivot.available_at)],
    ]}/>
    {structure.evidence.length > 0 && <EvidenceDisclosure label={structure.evidence.length + ' qualifying structure source candles'}>
      <ReplaySourcePins rows={structure.evidence}/>
      <ReplaySourceTable name="Qualifying structure source candles" rows={structure.evidence} direct/>
    </EvidenceDisclosure>}
  </EvidenceDisclosure>;
}
