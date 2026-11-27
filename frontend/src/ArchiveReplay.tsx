import {useEffect, useRef} from 'react';
import type {Candle, ReplayPane, ReplayViewId} from './types';

export const archiveViewIds = ['primary', '2', '3', '4'] as const;
export type ArchiveViewId = (typeof archiveViewIds)[number];
export type ArchivePane = {view: ArchiveViewId; data: ReplayPane};
export const replayPaneName = (view: ReplayViewId) =>
  view === 'primary' ? 'Primary chart' : view === 'comparison' ? 'SMT comparison' : 'Chart view ' + view;

const utc = (time: number) => new Date(time * 1000).toISOString();

export function FrozenReplayCoverage({panes, start, end}: {
  panes: ArchivePane[]; start?: number | null; end?: number | null;
}) {
  return <section className="archive-replay-coverage" aria-label="Frozen replay coverage">
    <div className="archive-replay-heading">
      <h2>Frozen replay coverage</h2>
      {start != null && end != null &&
        <p><time dateTime={utc(start)}>{utc(start)}</time> to <time dateTime={utc(end)}>{utc(end)}</time> (exclusive)</p>}
    </div>
    <ul>{panes.map(({view, data}) => {
      const coverage = 'candles' in data ? data.coverage : undefined;
      const count = 'candles' in data ? data.candles.length : 0;
      const partial = coverage && (coverage.gaps.length > 0 || coverage.truncated);
      const edges = coverage?.slot_start != null && coverage.slot_end != null &&
        (coverage.slot_start !== coverage.start || coverage.slot_end !== coverage.end)
        ? {start:utc(coverage.slot_start),end:utc(coverage.slot_end)} : undefined;
      return <li key={view}>
        <strong>{replayPaneName(view)} <span>{data.symbol} {data.timeframe}</span></strong>
        <span>{count.toLocaleString()} closed {count === 1 ? 'bar' : 'bars'}</span>
        {'error' in data ? <span className="archive-replay-warning">{data.error}</span> :
          !count ? <span className="archive-replay-warning">No completed candles in range</span> :
          partial ? <span className="archive-replay-warning">Partial coverage · {coverage.gaps.length} UTC {coverage.gaps.length === 1 ? 'gap' : 'gaps'}
            {coverage.truncated && ' · truncated'}</span> :
          !coverage ? <span>Coverage unavailable</span> : <span>No UTC gaps reported</span>}
        {count>0&&edges&&<details>
          <summary className="archive-replay-warning">Partial edge coverage</summary>
          <span>Eligible whole slots from <time dateTime={edges.start}>{edges.start}</time> to
            {' '}<time dateTime={edges.end}>{edges.end}</time> (exclusive)</span>
        </details>}
      </li>;
    })}</ul>
  </section>;
}

export function ReplayCandleInspector({panes, pane, candles, selectedTime, precision, onView, onCandle}: {
  panes: ArchivePane[]; pane: ArchivePane; candles: Candle[]; selectedTime?: number;
  precision: number; onView: (view: ArchiveViewId) => void; onCandle: (time: number) => void;
}) {
  const picker = useRef<HTMLSelectElement>(null);
  useEffect(() => { picker.current?.focus({preventScroll: true}); }, []);
  const index = selectedTime === undefined ? candles.length - 1 : candles.findIndex(bar => bar.time === selectedTime);
  const shown = candles[index];
  const partitions = 'candles' in pane.data ? pane.data.partitions : undefined;
  const sliderIndex = Math.max(0, index < 0 ? candles.length - 1 : index);
  const sliderTime = candles[sliderIndex]?.time;
  return <section className="archive-replay-inspection" aria-label="Replay candle inspection">
    <div className="archive-replay-inspection-controls">
      <label>Inspection pane
        <select ref={picker} value={pane.view} onChange={event => onView(event.target.value as ArchiveViewId)}>
          {panes.map(({view, data}) => <option key={view} value={view}
            disabled={'error' in data || data.candles.length === 0}>{replayPaneName(view)} · {data.symbol} {data.timeframe}</option>)}
        </select>
      </label>
      <label className="archive-replay-scrubber">Inspect candle
        <input type="range" min={0} max={Math.max(0, candles.length - 1)} value={sliderIndex}
          aria-valuetext={sliderTime === undefined ? 'No completed candles' : utc(sliderTime)}
          disabled={candles.length === 0}
          onChange={event => onCandle(candles[Number(event.target.value)].time)}/>
      </label>
    </div>
    <section className="archive-replay-readout" aria-label="Selected candle">
      {shown ? <>
        <time dateTime={utc(shown.time)}>{utc(shown.time)}</time>
        <dl aria-label="Selected candle OHLC">
          {(['open', 'high', 'low', 'close'] as const).map(field => <div key={field}>
            <dt>{field}</dt><dd>{shown[field].toFixed(precision)}</dd>
          </div>)}
        </dl>
      </> : <p role="status">{selectedTime === undefined ? 'No completed candles in this pane at the replay cutoff.' :
        'The selected candle is beyond the replay cutoff.'}</p>}
      {shown && 'candles' in pane.data && <details className="archive-replay-candle-source">
        <summary>Selected candle source</summary>
        <dl>
          {shown.available_at!==undefined&&<div><dt>Available at (UTC)</dt>
            <dd><time dateTime={utc(shown.available_at)}>{utc(shown.available_at)}</time></dd></div>}
          {shown.availability_basis&&<div><dt>Availability basis</dt>
            <dd>{shown.availability_basis==='ARCHIVE_SUPPLIED'?'Archive supplied':'Modeled nominal close'}</dd></div>}
        </dl>
        {shown.source_partition_indexes?.length ? <ul>{shown.source_partition_indexes.map(index=>{
          const partition=partitions?.[index];
          return partition&&<li key={partition.id}><span>Partition {partition.id}</span>
            <span>SHA-256 {partition.sha256}</span></li>;
        })}</ul> : <p>No source partitions recorded for this candle.</p>}
      </details>}
    </section>
  </section>;
}
