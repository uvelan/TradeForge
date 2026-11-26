import {useState, type ReactNode} from 'react';

type Partition = {id: string; sha256: string};
export type ReplaySourceBar = {
  time: number; available_at: number; open: number; high: number; low: number; close: number;
  source_id: string; partitions: Partition[];
  availability_basis: 'ARCHIVE_SUPPLIED' | 'MODELED_NOMINAL_CLOSE';
};
export type ReplaySourceCandle = ReplaySourceBar & {
  roles: {role: string; event_id: string}[];
};

export const replayUtc = (time: number) => new Date(time * 1000).toISOString().replace('T', ' ').replace('.000Z', ' UTC');
export const evidenceWords = (value: string) => value.replaceAll('_', ' ');

export function EvidenceDisclosure({label, children}: {label: string; children: ReactNode}) {
  const [expanded, setExpanded] = useState(false);
  return <details onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>{label}</summary>
    {expanded && children}
  </details>;
}

export function ReplaySourcePins({rows}: {rows: Pick<ReplaySourceBar, 'partitions'>[]}) {
  const partitions = [...new Map(rows.flatMap(row => row.partitions).map(pin => [pin.id, pin])).values()];
  return <ul>{partitions.map(pin => <li key={pin.id} className="evidence-id">
    Dataset <code>{pin.id}</code> · SHA-256 <code>{pin.sha256}</code>
  </li>)}</ul>;
}

export function ReplaySourceTable({name, rows, direct = false}: {name: string; rows: (ReplaySourceBar | ReplaySourceCandle)[]; direct?: boolean}) {
  const columns = ['Opened', 'Available', 'Availability basis', 'Open', 'High', 'Low', 'Close', 'Source ID'];
  return <div className="replay-evidence-sources" tabIndex={0}>
    <table aria-label={name}>
      <thead><tr>{[...columns, ...(direct ? ['Event role'] : [])].map(label =>
        <th key={label} scope="col">{label}</th>)}</tr></thead>
      <tbody>{rows.map(row => <tr key={row.source_id}>
        <td>{replayUtc(row.time)}</td><td>{replayUtc(row.available_at)}</td>
        <td>{evidenceWords(row.availability_basis)}</td><td>{row.open}</td><td>{row.high}</td><td>{row.low}</td><td>{row.close}</td>
        <td className="evidence-id"><code>{row.source_id}</code></td>
        {direct && <td>{'roles' in row && row.roles.map(({role, event_id}) => <div key={role + event_id}>
          {evidenceWords(role).toLowerCase()} <code title={event_id}>{event_id.slice(0, 12)}</code>
        </div>)}</td>}
      </tr>)}</tbody>
    </table>
  </div>;
}
