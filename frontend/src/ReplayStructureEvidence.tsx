import {useEffect, useState} from 'react';
import {api} from './api';
import type {Candle, CandleResponse, ReplaySession, Timeframe} from './types';
import {useWorkspacePreference} from './useWorkspacePreference';

type Partition = {id: string; sha256: string};
type SourceCandle = Required<Pick<Candle, 'time' | 'available_at' | 'open' | 'high' | 'low' | 'close'>> & {
  source_id: string; partitions: Partition[];
  availability_basis: 'ARCHIVE_SUPPLIED' | 'MODELED_NOMINAL_CLOSE';
};
type EventSummary = {
  id: string; type: string; side: 'HIGH' | 'LOW'; direction: 'BULLISH' | 'BEARISH' | null;
  anchor_time: number; observation_time: number; confirmation_time: number | null;
  available_at: number; level_price: number; status: string; rule_version: number;
  supporting_ids: string[]; state_before?: string; state_after?: string;
  liquidity_side?: string; lower?: number; upper?: number;
  break_price?: number; displacement?: number; level_available_at?: number;
};
type ReplayEvent = EventSummary & {evidence: SourceCandle[]};
type Evidence = {
  session_id: string; view: string; symbol: string; timeframe: Timeframe;
  cutoff: number; feed_id: string; price_basis: string; authority: 'RESEARCH_ONLY';
  rules: {mss: {min_displacement: number}};
  events: EventSummary[]; total: number; previous_before: number | null; selected: ReplayEvent | null;
};
type Lane = {view: string; symbol: string};

const utc = (time: number) => new Date(time * 1000).toISOString().replace('T', ' ').replace('.000Z', ' UTC');
const words = (value: string) => value.replaceAll('_', ' ');
const laneLabel = (view: string) => view === 'primary' ? 'Primary' : view === 'comparison' ? 'Comparison' : 'View ' + view;

function supportsEvidence(market: CandleResponse, symbol: string, timeframe: Timeframe) {
  return market.source === 'DUKASCOPY_SAVED' && market.price_basis === 'BID' &&
    Boolean(market.partitions?.length) && timeframe === 'M5' && (symbol === 'EURUSD' || symbol === 'GBPUSD');
}

export function ReplayStructureEvidence({session, cutoff, symbol, timeframe, linkedViews}: {
  session: ReplaySession; cutoff: number; symbol: string; timeframe: Timeframe; linkedViews: number;
}) {
  const [preferred, setPreferred] = useWorkspacePreference<string>('replay-structure-pane.' + session.id, 'primary');
  const lanes: Lane[] = [];
  if (supportsEvidence(session.market, symbol, timeframe)) lanes.push({view: 'primary', symbol});
  for (const [view, market] of Object.entries(session.views)) {
    if ((view === 'comparison' || Number(view) <= linkedViews) &&
        !('error' in market) && supportsEvidence(market, market.symbol, market.timeframe)) {
      lanes.push({view, symbol: market.symbol});
    }
  }
  const lane = lanes.find(({view}) => view === preferred) ?? lanes[0];
  if (!lane) return null;
  return <section className="card replay-structure-evidence" aria-label="Replay structure and liquidity">
    <h3>Replay structure and liquidity</h3>
    <p>M5 replay cutoff {utc(cutoff)}</p>
    {lanes.length > 1 && <div className="research-controls">
      <label>Event chart pane <select value={lane.view} onChange={event => setPreferred(event.target.value)}>
        {lanes.map(({view, symbol: name}) => <option key={view} value={view}>{laneLabel(view)} · {name}</option>)}
      </select></label>
    </div>}
    <LaneEvidence key={lane.view} sessionId={session.id} cutoff={cutoff} lane={lane}/>
  </section>;
}

function LaneEvidence({sessionId, cutoff, lane}: {sessionId: string; cutoff: number; lane: Lane}) {
  const [eventId, setEventId] = useWorkspacePreference<string>('replay-structure-event.' + sessionId + '.' + lane.view, '');
  return <CutoffEvidence key={cutoff} sessionId={sessionId} cutoff={cutoff} lane={lane}
    eventId={eventId} onEvent={setEventId}/>;
}

function CutoffEvidence({sessionId, cutoff, lane, eventId, onEvent}: {
  sessionId: string; cutoff: number; lane: Lane; eventId: string; onEvent: (id: string) => void;
}) {
  const [response, setResponse] = useState<{eventId: string; url: string; page: Evidence}>();
  const [error, setError] = useState<{url: string; message: string}>();
  const [before, setBefore] = useState<number>();
  const {view, symbol} = lane;
  const path = '/replay/sessions/' + encodeURIComponent(sessionId) + '/structure-liquidity';
  const query = new URLSearchParams({
    view, cutoff: String(cutoff), ...(eventId ? {event_id: eventId} : {}),
    ...(before != null ? {before: String(before)} : {}),
  });
  const url = path + '?' + query;

  useEffect(() => {
    let current = true;
    api.get<Evidence>(url).then(receipt => {
      if (receipt.session_id !== sessionId || receipt.view !== view || receipt.symbol !== symbol || receipt.cutoff !== cutoff) {
        throw new Error('Replay event evidence did not match this pane and cutoff.');
      }
      if (current) setResponse({eventId, url, page: receipt});
    }).catch(reason => {
      if (current) setError({url, message: reason instanceof Error ? reason.message : String(reason)});
    });
    return () => {current = false;};
  }, [url, sessionId, view, symbol, cutoff, eventId]);

  const page = response?.page;
  const selected = response?.eventId === eventId && page?.selected?.id === eventId ? page.selected : null;
  const inList = page?.events.some(event => event.id === eventId);
  const pending = response?.url !== url;
  const failure = error?.url === url ? error.message : undefined;
  if (failure && !page) return <p role="alert">{failure}</p>;
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
      {page.previous_before != null && <button type="button" className="small-tool" disabled={pending && !failure}
        onClick={() => setBefore(page.previous_before!)}>Older replay events</button>}
      {before != null && <button type="button" className="small-tool" onClick={() => setBefore(undefined)}>
        Latest replay events
      </button>}
      {page.total > page.events.length && <small>{page.events.length} of {page.total} events</small>}
    </div>
    {failure && <p role="alert">{failure}</p>}
    {selected ? <EventReceipt event={selected} mssThreshold={page.rules.mss.min_displacement}/> : eventId && !failure ?
      <p role="status">{pending ? 'Loading selected replay event…' : 'Selected event unavailable at this cutoff.'}</p> :
      page.events.length === 0 && <p role="status">No replay events at this cutoff.</p>}
  </>;
}

function eventLabel(event: EventSummary) {
  return words(event.type) + ' · ' + words(event.liquidity_side ?? event.side) + ' · ' + utc(event.available_at);
}

function EventReceipt({event, mssThreshold}: {event: ReplayEvent; mssThreshold: number}) {
  const partitions = [...new Map(event.evidence.flatMap(row => row.partitions).map(pin => [pin.id, pin])).values()];
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
    {event.evidence.length > 0 && <details>
      <summary>{event.evidence.length} source candles</summary>
      <ul>{partitions.map(pin => <li key={pin.id} className="evidence-id">
        Dataset <code>{pin.id}</code> · SHA-256 <code>{pin.sha256}</code>
      </li>)}</ul>
      <div className="replay-evidence-sources">
        <table aria-label="Event source candles">
          <thead><tr>{['Opened', 'Available', 'Availability basis', 'Open', 'High', 'Low', 'Close', 'Source ID', 'Dataset'].map(label =>
            <th key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{event.evidence.map(row => <tr key={row.source_id}>
            <td>{utc(row.time)}</td><td>{utc(row.available_at)}</td>
            <td>{words(row.availability_basis)}</td><td>{row.open}</td><td>{row.high}</td><td>{row.low}</td><td>{row.close}</td>
            <td className="evidence-id"><code>{row.source_id}</code></td>
            <td>{row.partitions.map(pin => <code key={pin.id} title={pin.id}>{pin.id.slice(0, 12)} </code>)}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </details>}
  </article>;
}
