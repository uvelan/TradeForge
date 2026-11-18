import {useEffect, useState} from 'react';
import type {smtFeed, SmtRules} from './smt';
import type {Timeframe} from './types';

type Feed = ReturnType<typeof smtFeed>;
type Calendar = 'continuous' | 'forex_weekly' | 'unknown';
export type StrictSmtContext = {
  primary?: Feed;
  comparison?: Feed;
  timeframe: Timeframe;
  cutoff: number;
  rules: SmtRules;
};

type Pivot = {time: number; price: number; nominal_confirmation: number; available_at: number};
type Source = {
  source: string; symbol: string; timeframe: string; feed_id: string; price_basis: string;
  dataset_id?: string; authority: string; coverage?: {status: string};
};
type Revision = {
  id: string; state: string; available_at: number;
  timing: {eligibility_reason: string; dependency_references: Record<string, unknown>};
};
type SmtEvidence = {
  id: string; direction: string; leader: string; current_state: string; current_reason?: string;
  pivots: Record<string, {previous: Pivot; current: Pivot}>;
  confirmed_at: number | null; candidate_at: number; historically_eligible: boolean;
  matching_window: {status: string; start: number; end: number; available_at: number;
    coverage_prefix?: {start: number; end: number}};
  revisions: Revision[];
};
type Result = {
  status: string; reason?: string; matching_status?: string; sources: Record<string, Source>; events: SmtEvidence[];
  current: {id: string; state: string; current_eligible: boolean; reason?: string; direction: string}[];
  delivery?: {next_cursor: string | null; total: number};
};

function frozen(feed: Feed, timeframe: Timeframe, calendar: Calendar) {
  if (!feed?.archive_range || !feed.partitions?.length ||
      (feed.source !== 'MT5_SAVED' && feed.source !== 'DUKASCOPY_SAVED')) return undefined;
  const {archive_range: [start, end], ...identity} = feed;
  return {...identity, timeframe, calendar: {kind: calendar}, start, end};
}

const utc = (seconds: number) => new Date(seconds * 1000).toISOString();

export function StrictSmtEvidence({context}: {context: StrictSmtContext}) {
  const [calendar, setCalendar] = useState<Calendar>('unknown');
  const primary = frozen(context.primary, context.timeframe, calendar);
  const comparison = frozen(context.comparison, context.timeframe, calendar);
  const body = primary && comparison && calendar !== 'unknown' ? JSON.stringify({
    primary, comparison, relationship: 'POSITIVE', cutoff: context.cutoff, ...context.rules,
  }) : '';

  return <section className="smt-rules" aria-label="Strict causal SMT">
    <small>Positive relationship · {context.timeframe} · cutoff {utc(context.cutoff)}</small>
    {!primary || !comparison ? <small role="status">Select two compatible saved archives. Replay requires both frozen instruments.</small> :
      <label>Shared SMT market hours
        <select value={calendar} onChange={event => setCalendar(event.target.value as Calendar)}>
          <option value="unknown">Select market hours</option>
          <option value="forex_weekly">Forex · Sunday to Friday, 17:00 New York</option>
          <option value="continuous">Continuous · 24 hours</option>
        </select>
      </label>}
    {body && <StrictSnapshot key={body} body={body}/>}
  </section>;
}

function StrictSnapshot({body}: {body: string}) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [result, setResult] = useState<{cursor: string | null; data?: Result; error?: string}>();
  useEffect(() => {
    const controller = new AbortController();
    async function evaluate() {
      try {
        const response = await fetch('/research/api/indicators/smt/strict', {
          method: 'POST', headers: {'Content-Type': 'application/json'}, signal: controller.signal,
          body: JSON.stringify({...JSON.parse(body), cursor}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Strict SMT evaluation unavailable');
        if (!controller.signal.aborted) setResult({cursor, data});
      } catch (error) {
        if (!controller.signal.aborted) setResult({cursor, error: error instanceof Error ? error.message : 'Strict SMT evaluation unavailable'});
      }
    }
    void evaluate();
    return () => controller.abort();
  }, [body, cursor]);
  const page = result?.cursor === cursor ? result : undefined;
  const data = page?.data;
  if (!data) return <>
    <small role="status">{page?.error ?? 'Checking frozen SMT evidence…'}</small>
    {page?.error && cursor && <button type="button" onClick={() => setCursor(null)}>Return to latest SMT evidence</button>}
  </>;
  return <>
    <small role="status">{data.status}{data.reason && ` · ${data.reason}`}</small>
    {data.matching_status && <small>Matching · {data.matching_status}</small>}
    {Object.entries(data.sources).map(([side, source]) => <small key={side}>
      {side} · {source.symbol} · {source.source} · {source.feed_id} · {source.price_basis} · {source.authority}
      {source.coverage && ` · ${source.coverage.status}`}
    </small>)}
    <strong>Current SMT state</strong>
    {!data.current.length && <small>No active evidence</small>}
    {data.current.map(event => <small key={event.id}>{event.direction} · {event.state} · {
      event.current_eligible ? 'Eligible' : event.reason ?? 'Research only'
    }</small>)}
    <strong>Historical SMT evidence</strong>
    {data.events.map(event => <details key={event.id}>
      <summary>{event.direction} · {event.leader} leads · {event.current_state} · {utc(event.candidate_at)}</summary>
      <small>Matching {event.matching_window.status.toLowerCase()} · {utc(event.matching_window.start)}
        {' – '}{utc(event.matching_window.end)} · available {utc(event.matching_window.available_at)}</small>
      {event.matching_window.coverage_prefix && <small>Required session prefix · {
        utc(event.matching_window.coverage_prefix.start)}{' – '}{utc(event.matching_window.coverage_prefix.end)}</small>}
      <small>{event.confirmed_at === null ? 'Awaiting confirmation' : `Confirmed ${utc(event.confirmed_at)}`} · {
        event.historically_eligible ? 'Historically eligible' : 'Research only'
      }</small>
      {Object.entries(event.pivots).map(([side, pivots]) => Object.entries(pivots).map(([point, pivot]) =>
        <small key={side + point}>{side} {point} · {pivot.price} at {utc(pivot.time)} · nominal close {
          utc(pivot.nominal_confirmation)} · available {utc(pivot.available_at)}</small>))}
      {event.revisions.map(revision => <details key={revision.id}>
        <summary>{revision.state} · {utc(revision.available_at)} · {revision.timing.eligibility_reason}</summary>
        <pre>{JSON.stringify(revision.timing.dependency_references, null, 2)}</pre>
      </details>)}
    </details>)}
    {cursor && <button type="button" onClick={() => setCursor(null)}>Latest SMT evidence</button>}
    {data.delivery?.next_cursor && <button type="button" onClick={() => setCursor(data.delivery!.next_cursor)}>Older SMT evidence</button>}
  </>;
}
