import {useState} from 'react';
import {useTimestampFormat} from './timezone';
import type {Plan,Trade} from './types';

export function planDiff(before:Plan,after:Plan){
  const keys=new Set([...Object.keys(before),...Object.keys(after)]);
  const text=(value:unknown)=>value===undefined?'—':typeof value==='string'?value:JSON.stringify(value);
  return Array.from(keys).flatMap(key=>{
    const old=text(before[key as keyof Plan]),next=text(after[key as keyof Plan]);
    return old===next?[]:[{key,old,next}];
  });
}
export function PlanHistory({trade,onRevise,onClose,initialVersion,onLesson}:{trade:Trade;initialVersion?:number;onRevise:()=>void;onClose:()=>void;onLesson:(version:number)=>void}){
  const versions=trade.versions??[];
  const [first,setFirst]=useState(versions[1]?.version??versions[0]?.version);
  const [second,setSecond]=useState(versions.some(item=>item.version===initialVersion)?initialVersion:versions[0]?.version);
  const format=useTimestampFormat();
  const left=versions.find(item=>item.version===first);
  const right=versions.find(item=>item.version===second);
  const diff=left&&right?planDiff(left.plan,right.plan):[];
  return <section className="card" aria-label="Plan versions">
    <h2>Plan versions · {trade.id}</h2>
    <div className="research-controls">
      {([['From version',first,setFirst],['To version',second,setSecond]] as const).map(([label,value,change])=>
        <label key={label}>{label}<select value={value} onChange={event=>change(Number(event.target.value))}>
          {versions.map(item=><option key={item.id} value={item.version}>v{item.version} · {format(item.created_at)}</option>)}
        </select></label>)}
      {trade.status==='PLANNED'&&<button className="small-tool" onClick={onRevise}>Create revision from latest</button>}
      {right&&<button className="small-tool" onClick={()=>onLesson(right.version)}>Record plan lesson</button>}
      <button className="small-tool" onClick={onClose}>Close version history</button>
    </div>
    {diff.length?<div className="table-scroll plan-diff"><table><thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead>
      <tbody>{diff.map(change=><tr key={change.key}><td>{change.key.replaceAll('_',' ')}</td><td>{change.old}</td><td>{change.next}</td></tr>)}</tbody>
    </table></div>:<p>No differences between the selected versions.</p>}
  </section>;
}
