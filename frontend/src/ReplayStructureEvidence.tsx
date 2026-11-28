import {useState} from 'react';
import type {Timeframe} from './types';
import {EvidenceDisclosure, ReplaySourcePins, ReplaySourceTable, evidenceWords as words, replayUtc as utc, useReplayEndpoint, type ReplaySourceCandle} from './ReplayEvidenceSources';
import {replayEvidenceLanes, replayLaneLabel, type ReplayEvidenceProps, type ReplayLane} from './replayEvidenceLanes';
import {useWorkspacePreference} from './useWorkspacePreference';

type EventSummary = {
  id: string; type: string; side: 'HIGH' | 'LOW'; direction: 'BULLISH' | 'BEARISH' | null;
  anchor_time: number; observation_time: number; confirmation_time: number | null;
  available_at: number; level_price: number; status: string; rule_version: number;
  supporting_ids: string[]; state_before?: string; state_after?: string;
  liquidity_side?: string; lower?: number; upper?: number;
  break_price?: number; displacement?: number; level_available_at?: number;
};
type ReplayEvent = EventSummary & {evidence: ReplaySourceCandle[]};
type Evidence = {
  session_id: string; view: string; symbol: string; timeframe: Timeframe;
  cutoff: number; feed_id: string; price_basis: string; authority: 'RESEARCH_ONLY';
  rules: {mss: {min_displacement: number}};
  events: EventSummary[]; total: number; previous_before: number | null; selected: ReplayEvent | null;
};
export function ReplayStructureEvidence({session, cutoff, symbol, timeframe, linkedViews}: ReplayEvidenceProps) {
  const [preferred, setPreferred] = useWorkspacePreference<string>('replay-structure-pane.' + session.id, 'primary');
  const [open, setOpen] = useWorkspacePreference('replay-structure-open.' + session.id, 'off', ['on', 'off']);
  const lanes = replayEvidenceLanes(session, symbol, timeframe, linkedViews);
  const lane = lanes.find(({view}) => view === preferred) ?? lanes[0];
  if (!lane) return null;
  return <details className="card replay-structure-evidence" open={open === 'on'}
    onToggle={event => setOpen(event.currentTarget.open ? 'on' : 'off')}>
    <summary>Replay structure and liquidity</summary>
    {open === 'on' && <section aria-label="Replay structure and liquidity">
      <p>M5 replay cutoff {utc(cutoff)}</p>
      {lanes.length > 1 && <div className="research-controls">
        <label>Event chart pane <select value={lane.view} onChange={event => setPreferred(event.target.value)}>
          {lanes.map(({view, symbol: name}) => <option key={view} value={view}>{replayLaneLabel(view)} · {name}</option>)}
        </select></label>
      </div>}
      {lanes.map(choice => <LaneEvidence key={choice.view} sessionId={session.id} cutoff={cutoff}
        lane={choice} active={choice.view === lane.view}/>)}
    </section>}
  </details>;
}

function LaneEvidence({sessionId, cutoff, lane, active}: {
  sessionId: string; cutoff: number; lane: ReplayLane; active: boolean;
}) {
  const [eventId, setEventId] = useWorkspacePreference<string>('replay-structure-event.' + sessionId + '.' + lane.view, '');
  return active && <CutoffEvidence key={cutoff} sessionId={sessionId} cutoff={cutoff} lane={lane}
    eventId={eventId} onEvent={setEventId}/>;
}

function CutoffEvidence({sessionId, cutoff, lane, eventId, onEvent}: {
  sessionId: string; cutoff: number; lane: ReplayLane; eventId: string; onEvent: (id: string) => void;
}) {
  const [before, setBefore] = useState<number>();
  const {view, symbol} = lane;
  const path = '/replay/sessions/' + encodeURIComponent(sessionId) + '/structure-liquidity';
  const query = new URLSearchParams({
    view, cutoff: String(cutoff), ...(eventId ? {event_id: eventId} : {}),
    ...(before != null ? {before: String(before)} : {}),
  });
  const url = path + '?' + query;
  const {page, pending, error: failure} = useReplayEndpoint<Evidence>(url, receipt => {
    if (receipt.session_id !== sessionId || receipt.view !== view || receipt.symbol !== symbol || receipt.cutoff !== cutoff) {
      throw new Error('Replay event evidence did not match this pane and cutoff.');
    }
  });
  const selected = !pending && page?.selected?.id === eventId ? page.selected : null;
  const inList = page?.events.some(event => event.id === eventId);
  if (failure) return <p role="alert">{failure}</p>;
  if (!page) return <p role="status">Loading replay events…</p>;
  return <>
    <p>{page.symbol} {page.timeframe} · {page.price_basis} · {page.feed_id} · {page.authority === 'RESEARCH_ONLY' && 'Research only'}</p>
    <div className="research-controls">
      <label>Replay event <select value={eventId} onChange={event => onEvent(event.target.value)}>
        <option value="">Choose event</option>
        {eventId && !inList && <option value={eventId} disabled={!selected}>
          {selected ? eventLabel(selected) : 'Selected event unavailable'}
        </option>}
        {[...page.events].reverse().map(event => <option key={event.id} value={event.id}>{eventLabel(event)}</option>)}
      </select></label>
      {page.previous_before != null && <button type="button" className="small-tool" disabled={pending}
        onClick={() => setBefore(page.previous_before ?? undefined)}>Older replay events</button>}
      {before != null && <button type="button" className="small-tool" onClick={() => setBefore(undefined)}>
        Latest replay events
      </button>}
      {page.total > page.events.length && <small>{page.events.length} of {page.total} events</small>}
    </div>
    {selected ? <EventReceipt event={selected} mssThreshold={page.rules.mss.min_displacement}/> : eventId ?
      <p role="status">{pending ? 'Loading selected replay event…' : 'Selected event unavailable at this cutoff.'}</p> :
      page.events.length === 0 && <p role="status">No replay events at this cutoff.</p>}
  </>;
}

function eventLabel(event: EventSummary) {
  return words(event.type) + ' · ' + words(event.liquidity_side ?? event.side) + ' · ' + utc(event.available_at);
}

function EventReceipt({event, mssThreshold}: {event: ReplayEvent; mssThreshold: number}) {
  const direct = event.evidence.filter(row => row.roles.length > 0);
  const context = event.evidence.filter(row => row.roles.length === 0);
  const rows = [
    ['Event', words(event.type)], ['Side', words(event.side)], ['Direction', event.direction ?? '—'],
    ['Status', words(event.status)], ['Level', event.level_price],
    ['Anchor', utc(event.anchor_time)], ['Observation', utc(event.observation_time)],
    ['Confirmation', event.confirmation_time === null ? 'Unconfirmed' : utc(event.confirmation_time)],
    ['First available', utc(event.available_at)], ['Rule version', event.rule_version],
    ...(event.level_available_at != null ? [['Level first available', utc(event.level_available_at)]] : []),
    ...(event.break_price != null ? [['Crossing price', event.break_price]] : []),
    ...(event.displacement != null ?
      [['Directional body', event.displacement], ['MSS threshold (price)', mssThreshold]] : []),
    ...(event.liquidity_side ? [['Liquidity side', event.liquidity_side]] : []),
    ...(event.lower != null && event.upper != null ? [['Pool range', event.lower + ' – ' + event.upper]] : []),
    ...(event.state_before && event.state_after ?
      [['State change', words(event.state_before) + ' → ' + words(event.state_after)]] : []),
  ];
  return <article aria-label="Selected replay event" data-event-id={event.id} data-available-at={event.available_at}>
    <dl className="evidence-grid" aria-label="Event receipt">
      {rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
      <div><dt>Event ID</dt><dd className="evidence-id">{event.id}</dd></div>
      {event.supporting_ids.length > 0 && <div><dt>Supporting events</dt><dd className="evidence-id">
        {event.supporting_ids.join(', ')}
      </dd></div>}
    </dl>
    {direct.length > 0 && <EvidenceDisclosure label={direct.length + ' direct source candles'}>
      <ReplaySourcePins rows={event.evidence}/>
      <ReplaySourceTable name="Direct event source candles" rows={direct} direct/>
    </EvidenceDisclosure>}
    {context.length > 0 && <EvidenceDisclosure label={context.length + ' calculation context candles'}>
      <ReplaySourceTable name="Calculation context candles" rows={context}/>
    </EvidenceDisclosure>}
  </article>;
}
