import {useEffect, useState} from 'react';
import {api} from './api';
import type {CandleResponse, ReplaySession, Timeframe} from './types';
import {useWorkspacePreference} from './useWorkspacePreference';

type Partition = {id: string; sha256: string};
type OHLC = {open: number; high: number; low: number; close: number};
type Child = OHLC & {source_id: string; time: number; available_at: number; partitions: Partition[]};
type H1Evidence = {
  symbol: string; feed_id: string; price_basis: string;
  start: number; end: number; cutoff: number; available_at: number | null;
  status: 'FORMING' | 'COMPLETE' | 'UNRESOLVED' | 'CLOSED'; reason: string | null;
  ohlc: OHLC | null; children: Child[]; missing_slots: number[];
};
type Lane = {view: string; symbol: string; market: CandleResponse};

const hour = 3600;
const day = 24 * hour;
const hourAt = (time: number) => Math.floor(time / hour) * hour;
const dayAt = (time: number) => Math.floor(time / day) * day;
const labels = {FORMING: 'Still forming', COMPLETE: 'Complete', UNRESOLVED: 'Unresolved', CLOSED: 'Market closed'};
const utc = (time: number) => new Date(time * 1000).toISOString().replace('T', ' ').replace('.000Z', ' UTC');
const columns = ['open', 'high', 'low', 'close'] as const;
const laneLabel = (view: string) => view === 'primary' ? 'Primary' : view === 'comparison' ? 'Comparison' : 'View ' + view;

function capturedHourBounds(market: CandleResponse, cutoff: number) {
  const {coverage, candles} = market;
  if (!coverage) return;
  const left = coverage.truncated ? candles[0]?.time : coverage.start;
  // Use the captured interval even when an entire hour has no source rows.
  const right = Math.min(coverage.end, cutoff);
  if (left == null || right <= left) return;
  return {
    first: hourAt(left),
    last: Math.ceil(right / hour) * hour - hour,
  };
}

function supportsHour(market: CandleResponse, symbol: string, timeframe: Timeframe) {
  return market.source === 'DUKASCOPY_SAVED' && Boolean(market.coverage) &&
    timeframe === 'M5' && (symbol === 'EURUSD' || symbol === 'GBPUSD');
}

export function ReplayH1Evidence({session, cutoff, symbol, timeframe, linkedViews}: {
  session: ReplaySession; cutoff: number; symbol: string; timeframe: Timeframe; linkedViews: number;
}) {
  const [preferred, setPreferred] = useWorkspacePreference<string>('replay-h1-view', 'primary');
  const lanes: Lane[] = [];
  if (supportsHour(session.market, symbol, timeframe)) lanes.push({view: 'primary', symbol, market: session.market});
  for (const [view, market] of Object.entries(session.views)) {
    const visible = view === 'comparison' || Number(view) <= linkedViews;
    if (visible && !('error' in market) && supportsHour(market, market.symbol, market.timeframe)) {
      lanes.push({view, symbol: market.symbol, market});
    }
  }
  const lane = lanes.find(({view}) => view === preferred) ?? lanes[0];
  if (!lane) return null;
  return <section className="card replay-h1-evidence" aria-label="H1 replay evidence">
    <h3>H1 replay evidence</h3>
    <p>M5 replay cutoff {utc(cutoff)}</p>
    {lanes.length > 1 && <div className="research-controls">
      <label>H1 chart pane <select value={lane.view} onChange={event => setPreferred(event.target.value)}>
        {lanes.map(({view, symbol: name}) => <option key={view} value={view}>{laneLabel(view)} · {name}</option>)}
      </select></label>
    </div>}
    <LaneEvidence key={lane.view} sessionId={session.id} cutoff={cutoff} lane={lane}/>
  </section>;
}

function LaneEvidence({sessionId, cutoff, lane}: {sessionId: string; cutoff: number; lane: Lane}) {
  const [picked, setPicked] = useState<number>();
  const bounds = capturedHourBounds(lane.market, cutoff);
  const selected = picked !== undefined && bounds && picked >= bounds.first && picked <= bounds.last
    ? picked : undefined;
  if (picked !== selected) setPicked(selected);
  if (!bounds) return <p>No captured H1 interval at this cutoff for {lane.symbol}.</p>;
  const {first, last} = bounds;
  const start = selected ?? Math.max(first, Math.min(last, hourAt(cutoff) - hour));
  const browseByDay = last - first >= 31 * day;
  const selectedDay = dayAt(start);
  const optionFirst = browseByDay ? Math.max(first, selectedDay) : first;
  const optionLast = browseByDay ? Math.min(last, selectedDay + day - hour) : last;
  const hours = Array.from({length: (optionLast - optionFirst) / hour + 1}, (_, i) => optionFirst + i * hour);
  const url = '/replay/sessions/' + encodeURIComponent(sessionId) + '/higher-timeframe?' +
    new URLSearchParams({view: lane.view, cutoff: String(cutoff), hour_start: String(start)});
  return <>
    <div className="research-controls">
      {browseByDay && <label>H1 UTC date <input type="date" value={utc(selectedDay).slice(0, 10)}
        min={utc(first).slice(0, 10)} max={utc(last).slice(0, 10)} onChange={event => {
          const chosenDay = event.target.valueAsNumber / 1000;
          if (chosenDay >= dayAt(first) && chosenDay <= dayAt(last)) setPicked(Math.max(first, chosenDay));
        }}/></label>}
      <label>H1 interval <select value={selected ?? ''} onChange={event =>
        setPicked(event.target.value ? Number(event.target.value) : undefined)}>
        <option value="">Latest elapsed hour or current forming hour</option>
        {hours.map(value => <option key={value} value={value}>{utc(value)} – {utc(value + hour)}</option>)}
      </select></label>
    </div>
    <CandleEvidence key={url} url={url}/>
  </>;
}

function CandleEvidence({url}: {url: string}) {
  const [evidence, setEvidence] = useState<H1Evidence>();
  const [error, setError] = useState('');
  useEffect(() => {
    let current = true;
    api.get<H1Evidence>(url).then(receipt => {if (current) setEvidence(receipt);})
      .catch(reason => {if (current) setError(reason instanceof Error ? reason.message : String(reason));});
    return () => {current = false;};
  }, [url]);

  if (error) return <p role="alert">{error}</p>;
  if (!evidence) return <p role="status">Loading H1 evidence…</p>;
  const {symbol, feed_id, price_basis, start, end, available_at, ohlc, children, status, reason, missing_slots} = evidence;
  const partitions = [...new Map(children.flatMap(child => child.partitions)
    .map(pin => [pin.id, pin])).values()];
  return <article aria-label="Selected H1 interval" data-start={start} data-end={end} data-status={status}>
    <p>{symbol} H1 · {price_basis} · {feed_id}</p>
    <p>From {utc(start)} to {utc(end)} exclusive</p>
    <p role="status">{labels[status]}{status === 'UNRESOLVED' && reason ? ' · ' + reason.toLowerCase().replaceAll('_', ' ') : ''}</p>
    {available_at != null && <p>Modeled availability {utc(available_at)}</p>}
    {ohlc && <dl className="evidence-grid" aria-label="H1 OHLC">
      {columns.map(field => <div key={field}><dt>{field.toUpperCase()}</dt><dd>{ohlc[field]}</dd></div>)}
    </dl>}
    {missing_slots.length > 0 && <p>Missing M5 candles {missing_slots.map(utc).join(', ')}</p>}
    {children.length > 0 && <details>
      <summary>{children.length} source candles</summary>
      <ul>{partitions.map(pin => <li key={pin.id} className="evidence-id">Dataset <code>{pin.id}</code> · SHA-256 <code>{pin.sha256}</code></li>)}</ul>
      <div className="replay-h1-sources">
        <table aria-label="M5 source candles">
          <thead><tr>{['Opened', 'Available', 'Open', 'High', 'Low', 'Close', 'Source ID', 'Dataset'].map(heading =>
            <th key={heading} scope="col">{heading}</th>)}</tr></thead>
          <tbody>{children.map(child => <tr key={child.source_id} data-source-id={child.source_id}>
            <td>{utc(child.time)}</td><td>{utc(child.available_at)}</td>
            {columns.map(field => <td key={field}>{child[field]}</td>)}
            <td className="evidence-id"><code>{child.source_id}</code></td>
            <td>{child.partitions.map(pin => <code key={pin.id} title={pin.id}>{pin.id.slice(0, 12)} </code>)}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </details>}
  </article>;
}
