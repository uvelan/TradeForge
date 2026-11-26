import {useEffect, useRef, useState, type ReactNode} from 'react';
import {api} from './api';
import type {ReplayCrtPage, ReplayCrtReceipt, ReplayCrtRevision} from './replayCrtTypes';
import {EvidenceDisclosure, ReplaySourcePins, ReplaySourceTable, evidenceWords as words, replayUtc as utc} from './ReplayEvidenceSources';
import {replayEvidenceLanes, replayLaneLabel, type ReplayEvidenceProps, type ReplayLane} from './replayEvidenceLanes';
import {useWorkspacePreference} from './useWorkspacePreference';

const title = 'Replay Candle Range Theory';
const inputTime = (stamp: number) => new Date(stamp * 1000).toISOString().slice(0, 19);

type Fields = [string, ReactNode][];
function ReceiptFields({label, fields}: {label: string; fields: Fields}) {
  return <dl className="evidence-grid" aria-label={label}>
    {fields.map(([term, value]) => <div key={term}><dt>{term}</dt><dd>{value}</dd></div>)}
  </dl>;
}

export function ReplayCrtEvidence({session, cutoff, symbol, timeframe, linkedViews}: ReplayEvidenceProps) {
  const [preferred, setPreferred] = useWorkspacePreference<string>('replay-crt-pane.' + session.id, 'primary');
  const [open, setOpen] = useWorkspacePreference('replay-crt-open.' + session.id, 'off', ['on', 'off']);
  const [savedCutoff, setSavedCutoff] = useWorkspacePreference<string>('replay-crt-cutoff.' + session.id, '');
  const coverage = session.market.coverage;
  const start = coverage?.truncated ? session.market.candles[0]?.time : coverage?.start;
  const [savedReplayCutoff, exactCutoff] = savedCutoff.split(':').map(Number);
  const restoreCutoff = Number.isSafeInteger(exactCutoff) && savedReplayCutoff === cutoff &&
    start != null && exactCutoff >= start && exactCutoff <= cutoff;
  useEffect(() => {
    if (savedCutoff && !restoreCutoff) setSavedCutoff('');
  }, [savedCutoff, restoreCutoff, setSavedCutoff]);
  const lanes = replayEvidenceLanes(session, symbol, timeframe, linkedViews).filter(({view}) => {
    const market = view === 'primary' ? session.market : session.views[view];
    return market && !('error' in market) && Boolean(market.coverage);
  });
  const lane = lanes.find(choice => choice.view === preferred) ?? lanes[0];
  if (!lane || start == null) return null;
  return <details className="card replay-crt-evidence" open={open === 'on'}
    onToggle={event => setOpen(event.currentTarget.open ? 'on' : 'off')}>
    <summary>{title}</summary>
    {open === 'on' && <section aria-label={title}>
      {lanes.length > 1 && <div className="research-controls">
        <label>CRT chart pane <select value={lane.view} onChange={event => setPreferred(event.target.value)}>
          {lanes.map(choice => <option key={choice.view} value={choice.view}>
            {replayLaneLabel(choice.view)} · {choice.symbol}
          </option>)}
        </select></label>
      </div>}
      <CrtClock key={cutoff} sessionId={session.id} replayCutoff={cutoff} start={start} lane={lane}
        cutoff={restoreCutoff ? exactCutoff : cutoff} onCutoff={stamp =>
          setSavedCutoff(stamp === cutoff ? '' : cutoff + ':' + stamp)}/>
    </section>}
  </details>;
}

function CrtClock({sessionId, replayCutoff, start, lane, cutoff, onCutoff}: {
  sessionId: string; replayCutoff: number; start: number; lane: ReplayLane;
  cutoff: number; onCutoff: (cutoff: number) => void;
}) {
  const [draft, setDraft] = useState(inputTime(cutoff));
  return <>
    <p>Chart replay cutoff <time dateTime={new Date(replayCutoff * 1000).toISOString()}>{utc(replayCutoff)}</time>
      {cutoff !== replayCutoff && <> · CRT evidence as of <time dateTime={new Date(cutoff * 1000).toISOString()}>{utc(cutoff)}</time></>}
    </p>
    <form className="research-controls" onSubmit={event => {
      event.preventDefault();
      const stamp = Date.parse(draft + 'Z') / 1000;
      if (Number.isSafeInteger(stamp) && stamp >= start && stamp <= replayCutoff) onCutoff(stamp);
    }}>
      <label>CRT as of (UTC) <input type="datetime-local" step={1} required value={draft}
        min={inputTime(start)} max={inputTime(replayCutoff)} onChange={event => setDraft(event.target.value)}/></label>
      <button type="submit" className="small-tool">Inspect CRT cutoff</button>
      {cutoff !== replayCutoff && <button type="button" className="small-tool" onClick={() => {
        setDraft(inputTime(replayCutoff));
        onCutoff(replayCutoff);
      }}>Use replay cutoff</button>}
    </form>
    <CrtLane key={lane.view} sessionId={sessionId} cutoff={cutoff} lane={lane}/>
  </>;
}

function CrtLane({sessionId, cutoff, lane}: {sessionId: string; cutoff: number; lane: ReplayLane}) {
  const [id, setId] = useWorkspacePreference<string>('replay-crt-selected.' + sessionId + '.' + lane.view, '');
  return <CrtRanges key={cutoff} sessionId={sessionId} cutoff={cutoff} lane={lane} id={id} onSelect={setId}/>;
}

function CrtRanges({sessionId, cutoff, lane, id, onSelect}: {
  sessionId: string; cutoff: number; lane: ReplayLane; id: string; onSelect: (id: string) => void;
}) {
  const [response, setResponse] = useState<{url: string; page: ReplayCrtPage}>();
  const [error, setError] = useState<{url: string; message: string}>();
  const [before, setBefore] = useState<number>();
  const request = useRef<{url: string; promise: Promise<ReplayCrtPage>}>(undefined);
  const {view, symbol} = lane;
  const query = new URLSearchParams({view, cutoff: String(cutoff)});
  if (id) query.set('range_id', id);
  if (before != null) query.set('before', String(before));
  const url = '/replay/sessions/' + encodeURIComponent(sessionId) + '/crt?' + query;

  useEffect(() => {
    let current = true;
    const promise = request.current?.url === url ? request.current.promise : api.get<ReplayCrtPage>(url);
    request.current = {url, promise};
    promise.then(page => {
      if (page.session_id !== sessionId || page.view !== view || page.symbol !== symbol || page.cutoff !== cutoff ||
          page.source !== 'DUKASCOPY_SAVED' || page.price_basis !== 'BID' || page.timeframe !== 'M5' ||
          page.authority !== 'RESEARCH_ONLY') {
        throw new Error('CRT evidence did not match this replay pane and cutoff.');
      }
      if (current) {
        setResponse({url, page});
        setError(undefined);
      }
    }).catch(reason => {
      if (current) setError({url, message: reason instanceof Error ? reason.message : String(reason)});
    });
    return () => {current = false;};
  }, [url, sessionId, view, symbol, cutoff]);

  if (error?.url === url) return <p role="alert">{error.message}</p>;
  if (!response) return <p role="status">Loading replay CRT ranges…</p>;
  const {page} = response;
  const pending = response.url !== url;
  const selected = !pending && page.selected?.range_id === id ? page.selected : null;
  return <>
    <p>{page.symbol} H1 from M5 · {page.price_basis} · {page.feed_id} · Research only</p>
    <div className="research-controls">
      <label>CRT H1 reference <select value={id} onChange={event => onSelect(event.target.value)}>
        <option value="">Choose a completed H1 reference</option>
        {id && !page.ranges.some(range => range.range_id === id) && <option value={id} disabled={!selected}>
          {selected ? rangeLabel(selected) : pending ? 'Loading selected CRT range…' : 'Selected CRT range unavailable'}
        </option>}
        {[...page.ranges].reverse().map(range => <option key={range.range_id} value={range.range_id}>
          {rangeLabel(range)}
        </option>)}
      </select></label>
      {page.previous_before != null && <button type="button" className="small-tool" disabled={pending}
        onClick={() => setBefore(page.previous_before ?? undefined)}>Older CRT ranges</button>}
      {before != null && <button type="button" className="small-tool" onClick={() => setBefore(undefined)}>
        Latest CRT ranges
      </button>}
      <small>{page.ranges.length} of {page.total} CRT ranges</small>
    </div>
    {selected ? <CrtReceipt key={selected.id} range={selected}/> : id ?
      <p role="status">{pending ? 'Loading selected CRT range…' : 'Selected CRT range unavailable at this cutoff.'}</p> :
      page.ranges.length === 0 && <p role="status">No completed H1 CRT references at this cutoff.</p>}
  </>;
}

function rangeLabel(range: ReplayCrtRevision) {
  return utc(range.reference_start) + ' · H1 · ' + words(range.state);
}

const identifier = (id: string) => <code className="evidence-id">{id}</code>;

function CrtReceipt({range}: {range: ReplayCrtReceipt}) {
  const latest = range.revisions.at(-1);
  const fields: Fields = [
    ['State', words(range.state)], ['Reason', words(range.reason)], ['Revision', range.revision],
    ['Reference opened', utc(range.reference_start)], ['Reference closed', utc(range.reference_end)],
    ['Reference first available', utc(range.reference_available_at)],
    ['Reference OHLC', range.reference_ohlc.join(' / ')],
    ['Reference low / midpoint / high', [range.low, range.midpoint, range.high].join(' / ')],
    ['Revision observed', utc(range.observation_time)], ['Revision confirmed', utc(range.confirmation_time)],
    ['Revision available', utc(range.available_at)],
    ['Range ID', identifier(range.range_id)], ['Reference ID', identifier(range.reference_id)],
    ['Revision event ID', identifier(range.id)],
    ['Algorithm version', range.algorithm_version], ['Identity policy version', range.identity_policy_version],
  ];
  if (range.sweep_side) fields.push(['Sweep side', range.sweep_side]);
  if (range.swept_boundary != null) fields.push(['Swept boundary', range.swept_boundary]);
  if (range.sweep_extreme != null) fields.push(['Sweep extreme', range.sweep_extreme]);
  if (range.penetration != null) fields.push(['Penetration', range.penetration]);
  if (range.sweep_id) {
    fields.push(['Sweep ID', identifier(range.sweep_id)], ['Reclaim close', range.reclaim_close ?? 'Unconfirmed']);
  }
  if (range.reclaim_id) fields.push(['Reclaim ID', identifier(range.reclaim_id)]);
  if (range.replacement_reference) fields.push(['Replacement reference ID', identifier(range.replacement_reference)]);
  return <article aria-label="Selected CRT range" data-range-id={range.range_id}
    data-revision-id={range.id} data-available-at={range.available_at}>
    <ReceiptFields label="CRT receipt" fields={fields}/>
    {latest?.id === range.id && latest.continuity && <CrtGapEvidence revision={latest} range={range}/>}
    <EvidenceDisclosure label={range.reference_children.length + ' H1 source candles'}>
      <ReplaySourcePins rows={range.reference_children}/>
      <ReplaySourceTable name="CRT H1 source candles" rows={range.reference_children}/>
    </EvidenceDisclosure>
    <EvidenceDisclosure label={range.revisions.length + ' CRT lifecycle revisions'}>
      <ol aria-label="CRT lifecycle revisions">{range.revisions.map(revision => <li key={revision.id}>
        {words(revision.state)} · {words(revision.reason)} ·
        observed {utc(revision.observation_time)} · confirmed {utc(revision.confirmation_time)} ·
        available {utc(revision.available_at)} · {identifier(revision.id)}
      </li>)}</ol>
    </EvidenceDisclosure>
    <EvidenceDisclosure label="CRT lifecycle source candles">
      <ReplaySourcePins rows={range.evidence}/>
      <ReplaySourceTable name="CRT lifecycle source candles" rows={range.evidence} direct/>
    </EvidenceDisclosure>
  </article>;
}

function CrtGapEvidence({revision, range}: {revision: ReplayCrtRevision; range: ReplayCrtReceipt}) {
  const continuity = revision.continuity!;
  const intervals = continuity.missing_open_intervals;
  function witness(time: number, role: string) {
    const source = range.evidence.find(row => row.time === time &&
      row.roles.some(item => item.role === role && item.event_id === revision.id));
    return <>
      <div>{utc(time)}</div>
      {source && <>
        <div className="evidence-id">Source ID {identifier(source.source_id)}</div>
        <ReplaySourcePins rows={[source]}/>
      </>}
    </>;
  }
  return <section aria-label={intervals ? 'CRT missing open candles evidence' : 'CRT unverifiable closure evidence'}
    data-revision-id={revision.id}>
    <ReceiptFields label="CRT continuity evidence" fields={[
      ['Missing candle opens [start, end) · UTC', intervals
        ? intervals.map(({start, end}) => <div key={start}>[{utc(start)}, {utc(end)})</div>)
        : 'Missing open intervals could not be verified under the market calendar.'],
      ['Previous observed candle open', witness(continuity.previous_observed_open, 'GAP_PREVIOUS_OBSERVED')],
      ['Next observed candle open', witness(continuity.next_observed_open, 'GAP_NEXT_OBSERVED')],
    ]}/>
  </section>;
}
