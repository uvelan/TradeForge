import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {useTimestampFormat} from './timezone';
import {Badge, Notice, SectionTitle, Spinner} from './components';
import {BoundFields,Choice,GroupFields,Producers,fieldName,groupFor,rulesIn} from './StrategyRules';
import {AttemptFields,Policies,ScopeFields,upgradeDraft} from './StrategyPolicies';
import type {Catalog,Definition,Diff,Link,Listed,Phase,RecordVersion,Template,Validation} from './strategyTypes';
import {StrategyEvaluation,type EvidenceChartLocation} from './StrategyEvaluation';
import './strategyBuilder.css';

const endpoint='/strategies/v1';
const phases:Phase[]=['context','setup','trigger','invalidation'];
const purposeLabel=(value:string)=>value==='HISTORICAL_SIMULATION'?'Historical simulation':'Strict recorded';
type History=Omit<Listed,'purpose'>;
type HistoryResponse=Omit<History,'strategy_id'>;
const scopedHistory=(rows:HistoryResponse[],strategy_id:string):History[]=>rows.map(row=>({...row,strategy_id}));
function historyRow({strategy_id,version,version_id,created_at,configuration_fingerprint,status,definition}:RecordVersion):History{
  return {strategy_id,version,version_id,created_at,configuration_fingerprint,status,name:definition.name,schema_version:definition.schema_version};
}

function validationError(error:unknown):Validation|null {
  if(!(error instanceof Error))return null;
  try {
    const data=JSON.parse(error.message) as Validation;
    return Array.isArray(data.errors)?data:null;
  } catch {return null;}
}

function Temporal({value,onChange}:{value:Definition;onChange:(value:Definition)=>void}){
  const ids=phases.flatMap(phase=>rulesIn(value[phase]).map(rule=>rule.id));
  function update(index:number,link:Link){onChange({...value,temporal:value.temporal.map((old,i)=>i===index?link:old)});}
  return <section className="card strategy-block"><h2>Temporal requirements</h2>
    <p className="muted">Order uses causal availability. Equal timestamps cannot satisfy strict precedence.</p>
    {value.temporal.map((link,i)=><fieldset key={i} className="strategy-subgroup"><legend>Relationship {i+1}</legend>
      <div className="strategy-fields">
        <Choice label="Anchor condition" options={ids} value={link.first} blank="Choose condition"
          onChange={first=>first&&update(i,{...link,first})}/>
        <Choice label="Related condition" options={ids} value={link.then} blank="Choose condition"
          onChange={then=>then&&update(i,{...link,then})}/>
        <Choice label="Availability relation" options={['AFTER','BEFORE','WITHIN_AFTER'] as const} value={link.relation}
          display={relation=>relation==='BEFORE'?'Related before anchor':relation==='AFTER'?'Related after anchor':'Related within a window after anchor'}
          onChange={relation=>relation&&update(i,{...link,relation,within:relation==='WITHIN_AFTER'?link.within:null})}/>
      </div>
      {link.relation==='WITHIN_AFTER'&&<BoundFields label="Relationship window" value={link.within} frame={value.timeframes.trigger}
        onChange={within=>update(i,{...link,within})}/>}
      <button className="small-tool" type="button"
        onClick={()=>onChange({...value,temporal:value.temporal.filter((_item,index)=>i!==index)})}>Remove relationship</button>
    </fieldset>)}
    <button className="small-tool" disabled={ids.length<2||value.temporal.length>=96} type="button"
      onClick={()=>onChange({...value,temporal:[...value.temporal,{first:ids[0],then:ids[1],relation:'AFTER',within:null}]})}>Add temporal relationship</button>
    <BoundFields label="Setup expiry" value={value.expiry} frame={value.timeframes.trigger} onChange={expiry=>onChange({...value,expiry})}/>
  </section>;
}

function VersionHistory({record,history,load,disabled}:{record:RecordVersion;history:History[];load:()=>void;disabled:boolean}){
  const [from,setFrom]=useState(Math.max(1,record.version-1));
  const [to,setTo]=useState(record.version);
  const [result,setResult]=useState<Diff>();
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const format=useTimestampFormat();
  const versionOptions=history.map(row=>String(row.version));
  return <section className="card strategy-block"><h2>Version history</h2>
    <ol className="strategy-history">{history.map(row=><li key={row.version_id}>
      <strong>v{row.version} · {row.status}</strong> · {format(row.created_at)}<br/><code>{row.configuration_fingerprint}</code>
    </li>)}</ol>
    {history.length<record.version&&<button type="button" className="small-tool" disabled={disabled} onClick={load}>Load older versions</button>}
    {history.length>1&&<div className="strategy-fields">
      <Choice label="Compare from" value={String(from)} options={versionOptions} onChange={next=>{setFrom(Number(next));setResult(undefined);}}/>
      <Choice label="Compare to" value={String(to)} options={versionOptions} onChange={next=>{setTo(Number(next));setResult(undefined);}}/>
      <button className="small-tool" disabled={busy||disabled} type="button" onClick={()=>{
        setBusy(true);setError('');setResult(undefined);
        void api.get<Diff>(endpoint+'/'+record.strategy_id+'/compare?from_version='+from+'&to_version='+to)
          .then(setResult).catch((error:Error)=>setError(error.message)).finally(()=>setBusy(false));
      }}>Compare versions</button>
    </div>}
    {error&&<p role="alert">{error}</p>}
    {result&&<><p>{result.same_configuration?'Same configuration fingerprint':'Configuration fingerprint changed'}</p>
      {result.changes.length>0&&<div className="table-scroll"><table><caption>Version changes</caption>
        <thead><tr><th>Field</th><th>Before</th><th>After</th></tr></thead>
        <tbody>{result.changes.map(change=><tr key={change.path}><td>{change.path}</td><td>{JSON.stringify(change.before)}</td>
          <td>{JSON.stringify(change.after)}</td></tr>)}</tbody></table></div>}</>}
  </section>;
}

export function StrategyBuilder({openChart}:{openChart?:(location:EvidenceChartLocation)=>void}={}){
  const [catalog,setCatalog]=useState<Catalog>();
  const [templates,setTemplates]=useState<Template[]>([]);
  const [library,setLibrary]=useState<Listed[]>([]);
  const [libraryLoading,setLibraryLoading]=useState(true);
  const [libraryLoadFailed,setLibraryLoadFailed]=useState(false);
  const [offset,setOffset]=useState(0);
  const [record,setRecord]=useState<RecordVersion>();
  const [evaluationVersion,setEvaluationVersion]=useState<string>();
  const [history,setHistory]=useState<History[]>([]);
  const [definition,setDefinition]=useState<Definition>();
  const [validation,setValidation]=useState<Validation>();
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const [refreshWarning,setRefreshWarning]=useState('');
  const working=useRef(false);
  const format=useTimestampFormat();

  useEffect(()=>{
    let active=true;
    Promise.allSettled([api.get<Catalog>(endpoint+'/capabilities'),api.get<Template[]>(endpoint+'/templates'),
      api.get<Listed[]>(endpoint+'?limit=40')]).then(([cap,options,items])=>{
        if(!active)return;
        if(cap.status==='fulfilled'&&options.status==='fulfilled'){
          setCatalog(cap.value);setTemplates(options.value);
        }else{
          const failure=cap.status==='rejected'?cap:options;
          if(failure.status==='rejected')setError(failure.reason.message);
        }
        if(items.status==='fulfilled')setLibrary(items.value);
        else setLibraryLoadFailed(true);
        setLibraryLoading(false);
      });
    return()=>{active=false;};
  },[]);

  async function run(action:()=>Promise<void>){
    if(working.current)return;
    working.current=true;
    setBusy(true);setError('');setMessage('');
    try{await action();}catch(error){
      const detail=validationError(error);
      if(detail)setValidation(detail);
      else if(error instanceof Error)setError(error.message.includes('VERSION_CONFLICT')?'Another edit saved a newer version. Reload the saved definition before revising.':error.message);
    }finally{working.current=false;setBusy(false);}
  }
  function edit(update:Definition){setDefinition({...update,status:'DRAFT'});setValidation(undefined);setMessage('');}
  async function page(next:number){
    setLibrary(await api.get<Listed[]>(endpoint+'?limit=40&offset='+next));setOffset(next);
    setLibraryLoadFailed(false);
  }
  function accept(item:RecordVersion){
    setRecord(item);setDefinition(item.definition);setValidation(item.binding_revision);setRefreshWarning('');
    setHistory(previous=>{
      const known=[historyRow(item)];
      for(const row of previous){
        if(row.strategy_id!==item.strategy_id||row.version>=item.version)continue;
        // A stale reload can jump multiple versions. Do not page past a gap.
        if(row.version!==known.at(-1)!.version-1)break;
        known.push(row);
      }
      return known;
    });
  }
  async function refresh(item:RecordVersion,reloadLibrary=false){
    const jobs=[api.get<HistoryResponse[]>(endpoint+'/'+item.strategy_id+'/history?limit=40&before='+(item.version+1)).then(versions=>{
      setHistory([historyRow(item),...scopedHistory(versions.filter(row=>row.version<item.version),item.strategy_id)]);
    })];
    if(reloadLibrary)jobs.push(page(0));
    const results=await Promise.allSettled(jobs);
    const unavailable=[results[0].status==='rejected'?'version history':'',results[1]?.status==='rejected'?'strategy library':''].filter(Boolean);
    setRefreshWarning(unavailable.length?'Could not refresh '+unavailable.join(' and ')+'. The saved definition is unchanged.':'');
  }
  async function adopt(item:RecordVersion){
    accept(item);
    await refresh(item);
  }
  async function confirm(item:RecordVersion,text:string){
    // A successful write receipt is authoritative even if subsequent GETs fail.
    accept(item);setMessage(text);
    await refresh(item,true);
  }
  function begin(template:Template,blank=false){
    setRecord(undefined);setHistory([]);setValidation(undefined);setMessage('');setError('');setRefreshWarning('');
    const item=structuredClone(template.definition);
    setDefinition(blank?{...item,name:'Untitled strategy',description:'',context:null,setup:null,trigger:null,invalidation:null,
      temporal:[],indicators:[],trade_intent:undefined,attempt:null}:item);
  }
  function mutate(operation:'archive'|'duplicate'){
    if(!record)return;
    void run(async()=>{
      const item=await api.post<RecordVersion>(endpoint+'/'+record.strategy_id+'/'+operation,{expected_version:record.version});
      await confirm(item,operation==='archive'?'Strategy archived':'Independent draft created');
    });
  }

  return <div className="strategy-builder">
    <div className="page-heading"><div><span className="eyebrow">LIBRARY / RULES ENGINE</span><h1>Strategy Builder</h1><p>Define reusable rules and compare immutable versions.</p></div></div>
    <section className="card strategy-block" aria-label="Strategy library">
      <SectionTitle eyebrow="ARCHETYPES & VERSIONS" title="Strategy library" action={library.length>0&&<Badge tone="neutral">{library.length} strategies</Badge>}/>
      <div className="strategy-actions">
        {templates.map(template=><button className="small-tool" key={template.id} type="button" disabled={busy}
          onClick={()=>begin(template)}>Use {template.definition.name} template</button>)}
        {templates[0]&&<button className="btn btn-primary" type="button" disabled={busy} onClick={()=>begin(templates[0],true)}>New strategy</button>}
      </div>
      {libraryLoading?<div className="loading-zone" role="status"><Spinner/><span>Loading strategy library…</span></div>:library.length?<ul className="strategy-library">{library.map(item=><li key={item.strategy_id}>
        <button className="small-tool" disabled={busy} type="button" onClick={()=>void run(async()=>{
          await adopt(await api.get<RecordVersion>(endpoint+'/'+item.strategy_id));
        })}>{item.name}</button>
        <span>v{item.version} · <Badge tone={item.status==='READY_FOR_EVALUATION'?'green':item.status==='ARCHIVED'?'neutral':'amber'}>{item.status}</Badge> · {purposeLabel(item.purpose)} · {format(item.created_at)}</span>
      </li>)}</ul>:libraryLoadFailed?<div>
        <div className="notice notice-amber" role="status"><span>Could not load the strategy library.</span></div>
        <button className="small-tool" type="button" disabled={busy}
          onClick={()=>void run(()=>page(0))}>Retry strategy library</button>
      </div>:<p className="muted">No saved strategies on this page.</p>}
      <div className="strategy-actions">
        {offset>0&&<button className="small-tool" type="button" disabled={busy} onClick={()=>void run(()=>page(offset-40))}>Previous strategies</button>}
        {library.length===40&&<button className="small-tool" type="button" disabled={busy} onClick={()=>void run(()=>page(offset+40))}>More strategies</button>}
      </div>
    </section>
    {error&&<Notice tone="red">{error}</Notice>}
    {message&&<p className="success-row" role="status">{message}</p>}
    {refreshWarning&&record&&<div><p role="status">{refreshWarning}</p>
      <button type="button" className="small-tool" disabled={busy}
        onClick={()=>void run(()=>refresh(record,true))}>Refresh saved views</button></div>}
    {definition&&catalog&&<>
      <section className="card strategy-block" aria-label="Strategy editor">
        <div className="strategy-heading"><h2>Strategy editor</h2>{record&&<span>v{record.version} · {record.status}</span>}</div>
        {definition.schema_version===1&&<div className="strategy-actions">
          <p role="status">Legacy schema 1 requires revision before evaluation.</p>
          <button type="button" className="small-tool" disabled={busy||record?.status==='ARCHIVED'}
            onClick={()=>edit(upgradeDraft(definition))}>Revise as schema 2</button>
        </div>}
        <fieldset className="strategy-edit-fields" disabled={busy||record?.status==='ARCHIVED'||definition.schema_version===1}>
          <div className="strategy-fields">
            <label>Strategy name<input maxLength={100} value={definition.name} onChange={event=>edit({...definition,name:event.target.value})}/></label>
            <label>Definition status<output aria-label="Definition status">{record?.binding_revision&&definition===record.definition?'REVISION_REQUIRED':definition.status}</output></label>
          </div>
          <label>Description<textarea maxLength={2000} value={definition.description} onChange={event=>edit({...definition,description:event.target.value})}/></label>
          <ScopeFields value={definition} onChange={edit}/>
          <Producers value={definition} catalog={catalog} onChange={edit}/>
          {phases.map(phase=><section key={phase} className="card strategy-block" aria-label={phase+' phase'}>
            <h3>{phase[0].toUpperCase()+phase.slice(1)}</h3>
            {definition[phase]?<>
              <GroupFields node={definition[phase]!} value={definition} catalog={catalog} update={group=>edit({...definition,[phase]:group})}/>
              <button type="button" className="small-tool" onClick={()=>edit({...definition,[phase]:null})}>Clear {phase}</button>
            </>:<button className="small-tool" type="button" onClick={()=>edit({...definition,[phase]:groupFor(phase)})}>Add {phase} group</button>}
          </section>)}
          {definition.schema_version===2&&<AttemptFields value={definition} onChange={edit}/>}
          <Temporal value={definition} onChange={edit}/>
          <Policies value={definition} onChange={edit}/>
          <div className="strategy-actions">
            <button className="small-tool" type="button" onClick={()=>void run(async()=>{
              setValidation(await api.post<Validation>(endpoint+'/validate',definition));
            })}>Validate definition</button>
            <button className="small-tool" type="button" onClick={()=>void run(async()=>{
              const candidate={...definition,status:'READY_FOR_EVALUATION'} as const;
              const result=await api.post<Validation>(endpoint+'/validate',candidate);
              if(result.valid)setDefinition(candidate);
              setValidation(result);
            })}>Check readiness</button>
            {definition.status==='READY_FOR_EVALUATION'&&<button className="small-tool" type="button"
              onClick={()=>edit(definition)}>Return to draft</button>}
            <button className="btn btn-primary" type="button" onClick={()=>void run(async()=>{
              const item=await api.post<RecordVersion>(record?endpoint+'/'+record.strategy_id+'/revisions':endpoint,
                record?{expected_version:record.version,definition}:definition);
              await confirm(item,'Definition saved as version '+item.version);
            })}>{record?'Save revision':'Save strategy'}</button>
          </div>
        </fieldset>
        {record&&<div className="strategy-actions">
          {record.definition.schema_version===2&&record.status==='READY_FOR_EVALUATION'&&!record.binding_revision&&
            record.definition.availability_modes[0]==='HISTORICAL_SIMULATION'&&
            <button className="small-tool" type="button" disabled={busy} onClick={()=>setEvaluationVersion(record.version_id)}>Evaluate</button>}
          <button className="small-tool" disabled={busy} type="button" onClick={()=>void run(async()=>adopt(await api.get<RecordVersion>(endpoint+'/'+record.strategy_id)))}>Reload saved definition</button>
          <button className="small-tool" disabled={busy} type="button" onClick={()=>mutate('duplicate')}>Duplicate strategy</button>
          <button className="small-tool" disabled={busy||record.status==='ARCHIVED'} type="button" onClick={()=>mutate('archive')}>Archive strategy</button>
        </div>}
      </section>
      {record&&record.version_id===evaluationVersion&&<StrategyEvaluation key={'evaluation-'+record.version_id} record={record} openChart={openChart}/>}
      <section className="card strategy-block" aria-label="Definition validation">
        <h2>Definition validation</h2>
        <p>{purposeLabel(definition.availability_modes[0])} · Recorded data blocked · Evaluation pending TF-I09B</p>
        {record&&<p>Saved fingerprint <code>{record.configuration_fingerprint}</code></p>}
        {validation&&<>
          <strong role="status">{validation.status}</strong>
          {validation.configuration_fingerprint&&<p>Draft fingerprint <code>{validation.configuration_fingerprint}</code></p>}
          {validation.unresolved?.length>0&&<><h3>Unresolved settings</h3><ul>
            {validation.unresolved.map(field=><li key={field}>{fieldName(field)}</li>)}</ul></>}
          {validation.errors.length>0&&<ul role="alert">{validation.errors.map((issue,i)=><li key={i}>
            <strong>{issue.path||'Request'}</strong> · {issue.code} · {Array.isArray(issue.expected)?issue.expected.join(', '):issue.expected}
            {issue.condition_id&&<> · {issue.condition_id}</>}
          </li>)}</ul>}
        </>}
      </section>
      {record&&<VersionHistory key={record.version_id} record={record} history={history} disabled={busy} load={()=>void run(async()=>{
        const last=history.at(-1);
        if(last){
          const older=await api.get<HistoryResponse[]>(endpoint+'/'+record.strategy_id+'/history?limit=40&before='+last.version);
          setHistory(previous=>[...previous,...scopedHistory(older,record.strategy_id)]);
        }
      })}/>}
      <section className="card strategy-block"><details><summary>Unavailable capabilities</summary>
        <ul>{Object.entries(catalog.unsupported).map(([name,why])=><li key={name}>{fieldName(name)} · {why}</li>)}</ul></details></section>
    </>}
  </div>;
}
