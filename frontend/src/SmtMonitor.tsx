import {useEffect, useRef, useState} from 'react';
import {smtCoverageNotice, type SmtCoverage, type SmtEvent, type SmtRules} from './smt';
import type {Timeframe} from './types';
import {StrictSmtEvidence, type StrictSmtContext} from './StrictSmtEvidence';

const endpoint = '/research/api/indicators/smt/monitor';

type Watch = SmtRules & {primary_symbol:string; comparison_symbol:string; timeframe:Timeframe};
type MonitorStatus = {
  configuration: Watch | null;
  last_poll_at?: number;
  error?: string | null;
  coverage?: SmtCoverage;
  latest_alert?: SmtEvent | null;
  latest_correction?: {source_correction:{observed_at:number; changes:unknown[]}} | null;
};

export function SmtMonitor({watch, strict}: {watch?:Watch; strict?:StrictSmtContext}) {
  const [status,setStatus] = useState<MonitorStatus>();
  const [error,setError] = useState('');
  const [saving,setSaving] = useState(false);
  const [refresh,setRefresh] = useState(0);
  const [mode,setMode] = useState<'legacy'|'strict'>('legacy');
  const statusSequence = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      const sequence = ++statusSequence.current;
      const isCurrent = () => !controller.signal.aborted && sequence === statusSequence.current;
      try {
        const response = await fetch(endpoint,{signal:controller.signal});
        if (!response.ok) throw new Error('Background SMT status unavailable');
        const data:MonitorStatus = await response.json();
        if (isCurrent()) setStatus(data);
      } catch {
        if (isCurrent()) setStatus(undefined);
      }
    }
    void load();
    const timer = window.setInterval(load,5000);
    return () => {controller.abort(); window.clearInterval(timer);};
  },[refresh]);

  async function save(config:Watch, enabled:boolean) {
    setSaving(true);
    setError('');
    try {
      const response = await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...config,enabled})});
      if (!response.ok) throw new Error('Could not update background SMT monitoring');
      statusSequence.current++;
      setStatus({configuration:enabled ? config : null});
      setRefresh(previous => previous+1);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Background SMT settings unavailable');
    } finally {
      setSaving(false);
    }
  }

  const active = status?.configuration;
  const coverageNotice = smtCoverageNotice(status?.coverage);
  const matches = watch && active && (Object.keys(watch) as (keyof Watch)[]).every(key => watch[key] === active[key]);
  if (!watch && !active && !strict) return null;
  return <details>
    <summary>{strict ? 'SMT monitor' : 'Background SMT'}</summary>
    {strict && <label>SMT evidence mode <select value={mode}
      onChange={event => setMode(event.target.value === 'strict' ? 'strict' : 'legacy')}>
      <option value="legacy">Legacy research SMT</option>
      <option value="strict">Strict causal SMT</option>
    </select></label>}
    {mode === 'strict' && strict ? <StrictSmtEvidence context={strict}/> : <div className="smt-rules">
      <small role="status">{error || status?.error || coverageNotice || (active
        ? `${active.primary_symbol} / ${active.comparison_symbol} · ${active.timeframe} · ${status?.last_poll_at ? 'Polling' : 'Starting'}`
        : status ? 'Monitoring off' : 'Checking monitor status…')}</small>
      {watch && !matches && <button type="button" className="small-tool" disabled={saving || !status}
        onClick={()=>void save(watch,true)}>{active?'Use current chart and rules':'Start monitoring'}</button>}
      {active && <button type="button" className="small-tool" disabled={saving} onClick={()=>void save(active,false)}>Stop monitoring</button>}
      {status?.latest_alert?.confirmed_at && <small>{coverageNotice ? 'Saved SMT_CONFIRMED' : 'SMT_CONFIRMED'} · {status.latest_alert.direction} · {new Date(status.latest_alert.confirmed_at*1000).toISOString()}</small>}
      {status?.latest_correction && <small>SMT_SOURCE_CORRECTED · {new Date(status.latest_correction.source_correction.observed_at*1000).toISOString()} · {status.latest_correction.source_correction.changes.length} changed observations</small>}
    </div>}
  </details>;
}
