import {useEffect,useState} from 'react';
import {api} from './api';
import {Badge, Card, Notice, SectionTitle} from './components';

type Version={id:string;book:string;start:string;version:number};
type Report={amounts:Record<string,string|null>;status:string;currency:string;minor_decimals:number;opening:string|null;broker_closing:string|null;broker_equity:string|null;
  external_known_minor:number;realized_known_minor:number;charges_known_minor:number;closing_minor:number|null;balance_residual_minor:number|null;
  unrealized_minor:number|null;equity_minor:number|null;issues:{source:string;reason:string}[];positions:unknown[];loss_limits:unknown};
type Receipt=Version&{end:string;mode:string;cursor:number;total:number;report:Report|null;original_v1_report:Report|null;assessment_policy:string;assessment_error:{field:(string|number)[];reason:string;value:unknown}|null};
type Trace={row:{id:string;kind:string};cash_after_minor:number|null};
export default function StatementReconciliation(){
  const [versions,setVersions]=useState<Version[]>([]),[receipt,setReceipt]=useState<Receipt|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[rows,setRows]=useState<Trace[]>([]);
  const [evidence,setEvidence]=useState<unknown>(null),[offset,setOffset]=useState(0);
  useEffect(()=>{let active=true;api.get<Version[]>('/statements').then(v=>{if(active)setVersions(v)}).catch(e=>{if(active)setError(String(e))});return()=>{active=false}},[]);
  async function act(work:()=>Promise<void>){setBusy(true);setError('');try{await work()}catch(e){setError(String(e))}finally{setBusy(false)}}
  async function show(r:Receipt,page=0){
    const root='/statements/'+r.id;
    const [traces,source]=await Promise.all([api.get<Trace[]>(root+'/rows?limit=50&offset='+page),api.get(root+'/evidence')]);
    setReceipt(r);setRows(traces);setEvidence(source);setOffset(page);
  }
  const r=receipt?.report;
  return <Card><section aria-label="Broker statement reconciliation">
    <SectionTitle eyebrow="LEDGER AUDIT" title="Broker statement reconciliation" action={<a className="small-tool" href="/examples/controlled-statement.json" download>Download controlled example</a>}/>
    <p className="muted">Import redacted statement JSON with a local account alias. Amounts use the statement currency.</p>
    <div className="research-controls">
      <label>Redacted statement <input type="file" accept=".json" disabled={busy} onChange={e=>{
        const file=e.target.files?.[0];e.target.value='';if(file)void act(async()=>{const v=await api.statementFile<Receipt>(file);setVersions(await api.get('/statements'));await show(v)})}}/></label>
      <label>Reporting period and revision <select disabled={busy} value={receipt?.id??''} onChange={e=>{const id=e.target.value;void act(async()=>show(await api.get('/statements/'+id)))}}>
        <option value="" disabled>Select a statement</option>{versions.map(v=><option key={v.id} value={v.id}>{v.book} · {v.start.slice(0,10)} · v{v.version}</option>)}</select></label>
    </div>
    {error&&<Notice tone="red">{error}</Notice>}
    {receipt&&<><p className="mono">{receipt.mode} · {receipt.start} to {receipt.end} · {receipt.cursor}/{receipt.total} rows committed</p>
    {receipt.original_v1_report&&<><p className="muted">Historical V1 receipt · current assessment uses closing valuation V2.</p>
    <details><summary>Inspect original V1 verdict · {receipt.original_v1_report.status}</summary><pre>{JSON.stringify(receipt.original_v1_report,null,2)}</pre></details></>}
    {receipt.assessment_error&&<Notice tone="red">Current assessment unavailable · {receipt.assessment_error.field.join('.')} · {String(receipt.assessment_error.value)} · {receipt.assessment_error.reason}</Notice>}
    {!r&&!receipt.assessment_error&&<button className="btn btn-primary" disabled={busy} onClick={()=>void act(async()=>show(await api.post('/statements/'+receipt.id+'/advance?cursor='+receipt.cursor,{})))}>Reconcile next batch</button>}
    {r&&<><h3><Badge tone={r.status==='RECONCILED'?'green':'amber'}>{r.status}</Badge> · {r.currency}</h3><div className="table-scroll"><table><tbody>{Object.entries({
      'Opening balance':r.opening,'External movements (known)':r.amounts.external_known_minor,'Realized trading (known)':r.amounts.realized_known_minor,
      'Charges (known)':r.amounts.charges_known_minor,'Calculated balance':r.amounts.closing_minor,'Broker balance':r.broker_closing,
      'Balance difference':r.amounts.balance_residual_minor,'Open P&L':r.amounts.unrealized_minor,'Calculated equity':r.amounts.equity_minor,'Broker equity':r.broker_equity
    }).map(([key,value])=><tr key={key}><th>{key}</th><td className="mono">{value??'Unknown'}</td></tr>)}</tbody></table></div>
    {r.issues.map((issue,i)=><Notice tone="amber" key={i}>{issue.source} · {issue.reason}</Notice>)}
    <details><summary>Positions, margin, equity and risk calculations</summary><pre>{JSON.stringify(r,null,2)}</pre></details></>}
    <details><summary>Statement fingerprint, source evidence and revision changes</summary><pre>{JSON.stringify(evidence,null,2)}</pre></details>
    <h3>Source rows</h3>{rows.map(t=><details key={t.row.id}><summary>{t.row.id} · {t.row.kind} · balance {t.cash_after_minor??'unknown'} minor units</summary><pre>{JSON.stringify(t,null,2)}</pre></details>)}
    <div className="research-controls">
      {offset>0&&<button className="small-tool" disabled={busy} onClick={()=>void act(()=>show(receipt,offset-50))}>Previous rows</button>}
      {offset+50<receipt.cursor&&<button className="small-tool" disabled={busy} onClick={()=>void act(()=>show(receipt,offset+50))}>Next rows</button>}
    </div></>}
  </section></Card>;
}
