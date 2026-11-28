import {useEffect, useState, type ReactNode} from 'react';
import {api} from './api';
import {EvidenceDisclosure, ReplayReceiptFields, ReplaySourcePins, ReplaySourceTable, evidenceWords as words, replayUtc as utc, useReplayEndpoint} from './ReplayEvidenceSources';
import {replayEvidenceLanes, type ReplayEvidenceProps} from './replayEvidenceLanes';
import {confluenceProfile, type ConfluenceCandle, type ConfluenceComponent, type ConfluencePin,
  type ConfluenceReceipt} from './replayConfluenceTypes';
import {announceSavedConfluence, useSavedConfluenceReceipts} from './useSavedConfluenceReceipts';
import {useWorkspacePreference} from './useWorkspacePreference';

const title = 'Replay CRT and SMT confluence';
const follow = 'current';
const iso = (stamp: number) => new Date(stamp * 1000).toISOString();
const inputTime = (stamp: number) => iso(stamp).slice(0, 19);
const timeValue = (stamp: number | null) =>
  stamp == null ? 'Unavailable' : <time dateTime={iso(stamp)}>{utc(stamp)}</time>;
const idValue = (value: string | null) => value == null ? 'Unavailable' : <code className="evidence-id">{value}</code>;

function Fields({label, entries}: {label: string; entries: [string, ReactNode][]}) {
  return <ReplayReceiptFields label={label} fields={entries}/>;
}

function isPin(item: unknown): item is ConfluencePin {
  if (!item || typeof item !== 'object') return false;
  const pin = item as Partial<ConfluencePin>;
  return pin.profile === confluenceProfile && Number.isSafeInteger(pin.rule_version) && pin.rule_version! > 0 &&
    Number.isSafeInteger(pin.cutoff) && typeof pin.receipt_id === 'string' && /^[a-f0-9]{64}$/.test(pin.receipt_id);
}

export function ReplayConfluenceEvidence({session, cutoff, symbol, timeframe, linkedViews}: ReplayEvidenceProps) {
  const [open, setOpen] = useWorkspacePreference('replay-confluence-open.' + session.id, 'off', ['on', 'off']);
  const coverage = session.market.coverage;
  const start = coverage?.truncated ? session.market.candles[0]?.time : coverage?.start;
  const lanes = replayEvidenceLanes(session, symbol, timeframe, linkedViews);
  if (start == null || !lanes.some(lane => lane.symbol === 'EURUSD')) return null;
  return <details className="card replay-confluence-evidence" open={open === 'on'}
    onToggle={event => setOpen(event.currentTarget.open ? 'on' : 'off')}>
    <summary>{title}</summary>
    {open === 'on' && <ConfluenceInspector sessionId={session.id} chartCutoff={cutoff} start={start}/>}
  </details>;
}

function receiptPath(sessionId: string, cutoff: number, pin?: ConfluencePin) {
  const query = new URLSearchParams({cutoff: String(cutoff), profile: pin?.profile ?? confluenceProfile});
  if (pin) query.set('receipt_id', pin.receipt_id);
  return '/replay/sessions/' + encodeURIComponent(sessionId) + '/confluence?' + query;
}

function verifyReceipt(value: ConfluenceReceipt, sessionId: string, cutoff: number, version?: number, id?: string) {
  if (value.session_id !== sessionId || value.cutoff !== cutoff || (version != null && value.rule_version !== version) ||
      !isPin(value) || value.authority !== 'RESEARCH_ONLY' || (id && value.receipt_id !== id)) {
    throw new Error('The confluence receipt does not match the requested replay, rule version and cutoff.');
  }
}

function useReceipt(sessionId: string, cutoff: number | null, pin?: ConfluencePin) {
  const url = cutoff == null ? null : receiptPath(sessionId, cutoff, pin);
  const pinnedId = pin?.receipt_id;
  const version = pin?.rule_version;
  const {resolved, error} = useReplayEndpoint<ConfluenceReceipt>(url, value => {
    if (cutoff != null) verifyReceipt(value, sessionId, cutoff, version, pinnedId);
  });
  return url && (resolved !== undefined || error !== undefined) ? {url, value: resolved, error} : undefined;
}

function ConfluenceInspector({sessionId, chartCutoff, start}: {sessionId: string; chartCutoff: number; start: number}) {
  const [selection, select] = useWorkspacePreference<string>('replay-confluence-selected.' + sessionId, '');
  const [restore] = useState(() => /^[a-f0-9]{64}$/.test(selection) ? selection : undefined);
  const [selectedPin, remember] = useState<ConfluencePin>();
  const [probe, setProbe] = useState<{chartCutoff: number; cutoff: number}>();
  const cutoff = probe?.chartCutoff === chartCutoff ? probe.cutoff : chartCutoff;
  const savedList = useSavedConfluenceReceipts(sessionId, chartCutoff, restore);
  const page = savedList.page;
  const availablePins: ConfluencePin[] = (page?.receipts ?? []).filter(item =>
    isPin(item) && item.session_id === sessionId && item.cutoff <= chartCutoff);
  const extra = selectedPin ?? page?.selected;
  if (isPin(extra) && extra.receipt_id === selection && extra.cutoff <= chartCutoff &&
      !availablePins.some(item => item.receipt_id === extra.receipt_id)) {
    availablePins.push(extra);
  }
  const pin = availablePins.find(item => item.receipt_id === selection);
  const awaitingSelection = Boolean(restore === selection && !page && savedList.loading);
  const [saveMessage, setSaveMessage] = useState('');
  const [saveError, setSaveError] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (probe && probe.chartCutoff !== chartCutoff) setProbe(undefined);
  }, [probe, chartCutoff]);
  const current = useReceipt(sessionId, pin || awaitingSelection ? null : cutoff);
  const saved = useReceipt(sessionId, pin?.cutoff ?? null, pin);
  const reading = pin ? saved : selection === follow ? current : undefined;
  const receipt = reading?.value;
  const error = reading?.error ?? current?.error;
  const currentLabel = current?.value ? 'Current · ' + utc(cutoff) + ' · ' + words(current.value.status) :
    'Current · ' + utc(cutoff);

  async function save({profile, rule_version, cutoff, receipt_id}: ConfluenceReceipt) {
    setSaving(true);
    setSaveMessage('');
    setSaveError('');
    const pin = {profile, rule_version, cutoff, receipt_id};
    try {
      const pinned = await api.post<ConfluenceReceipt>(receiptPath(sessionId, cutoff, pin), {});
      verifyReceipt(pinned, sessionId, cutoff, rule_version, receipt_id);
      announceSavedConfluence(sessionId);
      setSaveMessage('Exact receipt saved.');
    } catch (reason) {
      setSaveError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  return <section aria-label={title}>
    <p><code>{confluenceProfile}</code> · Research only</p>
    <p>The latest EURUSD H1 CRT reference, built from M5 candles, and the newest EURUSD SMT candidate family
      must support the same direction. A low reclaim pairs with an active bullish confirmation. A high reclaim
      pairs with an active bearish confirmation. SMT uses exact GBPUSD M5 opens, pivot period 2, alignment 0,
      tolerance 0, confirmation 1 and expiry 64.</p>
    <p>Chart replay cutoff {timeValue(chartCutoff)}</p>
    <ConfluenceClock key={cutoff + ':' + chartCutoff} cutoff={cutoff} chartCutoff={chartCutoff} start={start}
      onCutoff={stamp => {setProbe({chartCutoff, cutoff: stamp}); select(follow);}}/>
    <div className="research-controls">
      <label>Confluence receipt <select value={pin || selection === follow ? selection : ''}
        onChange={event => {
          const value = event.target.value;
          remember(availablePins.find(item => item.receipt_id === value));
          select(value);
        }}>
        <option value="">Choose a confluence receipt</option>
        <option value={follow}>{currentLabel}</option>
        {availablePins.map(item => <option key={item.receipt_id} value={item.receipt_id}>
          Saved · {utc(item.cutoff)} · v{item.rule_version} · {item.receipt_id.slice(0, 12)}
        </option>)}
      </select></label>
      {savedList.loadOlder && <button type="button" className="small-tool" disabled={savedList.loading}
        onClick={savedList.loadOlder}>Load older saved receipts</button>}
      {savedList.error && <button type="button" className="small-tool"
        onClick={savedList.refresh}>Retry saved receipts</button>}
    </div>
    {savedList.error && <p role="alert">Saved receipts could not be loaded. {savedList.error}</p>}
    {selection && selection !== follow && !pin && !awaitingSelection && !savedList.error &&
      <p role="status">The saved receipt is unavailable at this chart cutoff.</p>}
    {error && <p role="alert">{error}</p>}
    {!reading && (pin || !current) && <p role="status">Loading replay confluence…</p>}
    {saveMessage && <p role="status">{saveMessage}</p>}
    {saveError && <p role="alert">{saveError}</p>}
    {receipt && <ConfluenceDetail key={receipt.receipt_id} receipt={receipt}
      showSave={!availablePins.some(item => item.receipt_id === receipt.receipt_id)} saving={saving} onSave={() => void save(receipt)}/>}
  </section>;
}

function ConfluenceClock({cutoff, chartCutoff, start, onCutoff}: {
  cutoff: number; chartCutoff: number; start: number; onCutoff: (stamp: number) => void;
}) {
  const [draft, setDraft] = useState(inputTime(cutoff));
  return <form className="research-controls" onSubmit={event => {
    event.preventDefault();
    const stamp = Date.parse(draft + 'Z') / 1000;
    if (Number.isSafeInteger(stamp) && stamp >= start && stamp <= chartCutoff) onCutoff(stamp);
  }}>
    <label>Confluence as of (UTC) <input type="datetime-local" step={1} required value={draft}
      min={inputTime(start)} max={inputTime(chartCutoff)} onChange={event => setDraft(event.target.value)}/></label>
    <button type="submit" className="small-tool">Inspect confluence cutoff</button>
    {cutoff !== chartCutoff && <button type="button" className="small-tool" onClick={() => onCutoff(chartCutoff)}>
      Use replay cutoff
    </button>}
  </form>;
}

function ConfluenceDetail({receipt, showSave, saving, onSave}: {
  receipt: ConfluenceReceipt; showSave: boolean; saving: boolean; onSave: () => void;
}) {
  return <article aria-label="Selected confluence receipt" data-receipt-id={receipt.receipt_id}
    data-rule-version={receipt.rule_version} data-cutoff={receipt.cutoff}>
    <Fields label="Confluence verdict" entries={[
      ['Verdict', words(receipt.status)], ['Reason', words(receipt.reason)], ['Direction', receipt.direction ?? 'Unavailable'],
      ['Receipt cutoff', timeValue(receipt.cutoff)], ['First available', timeValue(receipt.first_available_at)],
      ['Rule version', receipt.rule_version], ['Receipt ID', idValue(receipt.receipt_id)],
    ]}/>
    <CoverageRecoveries receipt={receipt}/>
    {showSave && <div className="research-controls">
      <button type="button" className="small-tool" disabled={saving} onClick={onSave}>Save exact receipt</button>
    </div>}
    <div className="evidence-pairs">
      <ComponentDetail name="CRT" scope="EURUSD H1 from M5" component={receipt.components.crt}/>
      <ComponentDetail name="SMT" scope="EURUSD and GBPUSD M5" component={receipt.components.smt}/>
    </div>
  </article>;
}

function CoverageRecoveries({receipt}: {receipt: ConfluenceReceipt}) {
  const recoveries = receipt.coverage_recoveries;
  if (receipt.rule_version === 1) return <p>Coverage recovery evidence is unavailable in this version 1 receipt.</p>;
  if (!recoveries?.length) return null;
  return <EvidenceDisclosure label={'Coverage recovery witnesses (' + recoveries.length + ')'}>
    <ul aria-label="Coverage recovery witnesses">
      {recoveries.map(witness => <li key={witness.source_id}>
        <Fields label={witness.symbol + ' coverage recovery'} entries={[
          ['Symbol', witness.symbol], ['Candle open', timeValue(witness.time)],
          ['Nominal close', timeValue(witness.nominal_close)], ['Available', timeValue(witness.available_at)],
          ['Availability basis', words(witness.availability_basis)], ['Source ID', idValue(witness.source_id)],
        ]}/>
        <ReplaySourcePins rows={[witness]}/>
      </li>)}
    </ul>
  </EvidenceDisclosure>;
}

function ComponentDetail({name, scope, component}: {name: string; scope: string; component: ConfluenceComponent}) {
  const fields: [string, ReactNode][] = [
    ['Condition', words(component.condition)], ['State', words(component.state)], ['Reason', words(component.reason)],
  ];
  if (component.published_state && component.published_state !== component.state) {
    fields.push(['Published revision state', words(component.published_state)]);
  }
  if (component.coverage_reason || component.coverage_issues?.length) {
    fields.push(['Coverage', component.coverage_issues?.length ?
      <ul className="confluence-coverage" aria-label={name + ' coverage issues'}>
        {component.coverage_issues.map(issue => <li key={issue.symbol}>
          <strong>{issue.symbol}</strong> · {words(issue.reason)}
          {issue.missing_intervals.map(({start, end}) => <div key={start}>[{utc(start)}, {utc(end)})</div>)}
        </li>)}
      </ul> : words(component.coverage_reason ?? '')]);
  }
  return <section aria-label={name + ' component'} data-revision-id={component.revision_id ?? undefined}>
    <h4>{name} · {scope}</h4>
    <Fields label={name + ' condition'} entries={[
      ...fields,
      ['Direction', component.direction ?? 'Unavailable'], ['Revision first available', timeValue(component.available_at)],
      ['Observed', timeValue(component.observation_time)], ['Confirmed', timeValue(component.confirmation_time)],
      ['Family ID', idValue(component.family_id)], ['Revision', component.revision ?? 'Unavailable'],
      ['Revision ID', idValue(component.revision_id)],
    ]}/>
    {component.sources.length > 0 && <EvidenceDisclosure label={name + ' pinned input archives'}>
      <ul>{component.sources.map(source => <li key={source.symbol}>
        {source.symbol} {source.timeframe} · {source.price_basis} · {source.feed_id} ·
        [{utc(source.start)}, {utc(source.end)})
      </li>)}</ul>
      <ReplaySourcePins rows={component.sources}/>
    </EvidenceDisclosure>}
    {component.evidence.length > 0 && <EvidenceDisclosure label={name + ' source candles (' + component.evidence.length + ')'}>
      <ConfluenceProof name={name} candles={component.evidence}/>
    </EvidenceDisclosure>}
  </section>;
}

function ConfluenceProof({name, candles}: {name: string; candles: ConfluenceCandle[]}) {
  const [sourceId, select] = useState<string>();
  const source = candles.find(row => row.source_id === sourceId);
  return <>
    <ul className="confluence-proof-sources" aria-label={name + ' source candle IDs'}>
      {candles.map(row => <li key={row.source_id}>
        <span>{row.symbol} · {utc(row.time)}</span>
        <button type="button" className="small-tool evidence-id" aria-pressed={row.source_id === sourceId}
          onClick={() => select(row.source_id)}>{row.source_id}</button>
      </li>)}
    </ul>
    {source && <section aria-label={name + ' exact source candle'} data-source-id={source.source_id}>
      <p>{source.symbol} {source.timeframe} · {source.price_basis} · {source.feed_id}</p>
      <ReplaySourceTable name={name + ' exact source candle'} rows={[source]} direct/>
      <ReplaySourcePins rows={[source]}/>
    </section>}
  </>;
}
