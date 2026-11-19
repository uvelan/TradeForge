import type {ReactNode} from 'react';
import type {Bound,Catalog,Definition,Dependency,Frame,Group,Node,Rule,Scalar,SchemaField} from './strategyTypes';

export function keyFor(prefix:string){return prefix+'-'+crypto.randomUUID().slice(0,8);}
export function groupFor(prefix:string):Group {
  return {type:'GROUP',id:keyFor(prefix),operator:'ALL',children:[],unknown_policy:null,within:null};
}
export function rulesIn(node:Node|null):Rule[] {
  return !node ? [] : node.type==='RULE' ? [node] : node.children.flatMap(rulesIn);
}
export function fieldName(name:string){return name.replaceAll('_',' ');}
export function NumberField({label,value,onChange,min,max,step=1}:{label:string;value:number|null;onChange:(value:number|null)=>void;min?:number;max?:number;step?:number|string}){
  return <label>{label}<input type="number" value={value??''} min={min} max={max} step={step}
    onChange={event=>onChange(event.target.value===''?null:event.target.valueAsNumber)}/></label>;
}
export function Choice<T extends string>({label,value,onChange,options,blank,display}:{label:string;value:T|'';onChange:(value:T|'')=>void;
  options:readonly T[];blank?:string;display?:(value:T)=>ReactNode}){
  return <label>{label}<select value={value} onChange={event=>onChange(event.target.value as T|'')}>
    {blank&&<option value="">{blank}</option>}
    {options.map(item=><option key={item} value={item}>{display?display(item):item}</option>)}
  </select></label>;
}
export function BoundFields({label,value,onChange,frame}:{label:string;value:Bound|null;onChange:(value:Bound|null)=>void;frame:Frame}){
  return <fieldset className="strategy-subgroup"><legend>{label}</legend>
    <Choice label={label+' unit'} options={['SECONDS','ELIGIBLE_BARS'] as const} value={value?.unit??''}
      blank="Unspecified" onChange={unit=>onChange(unit?{unit,count:1,timeframe:unit==='ELIGIBLE_BARS'?frame:null}:null)}/>
    {value&&<NumberField label={label+' count'} value={value.count} min={1} max={value.unit==='SECONDS'?86400:500}
      onChange={count=>onChange({...value,count:count??0})}/>}
    {value?.unit==='ELIGIBLE_BARS'&&<Choice label={label+' timeframe'} options={['M1','M5'] as const}
      value={value.timeframe??frame} onChange={timeframe=>timeframe&&onChange({...value,timeframe})}/>}
  </fieldset>;
}

function setting(name:string,schema:SchemaField,value:Scalar,onChange:(value:Scalar)=>void){
  const field=schema.anyOf?.find(option=>option.type!=='null')??schema;
  if(field.enum)return <Choice label={name} value={typeof value==='string'?value:''} options={field.enum}
    blank={schema.anyOf?'Unspecified':undefined} onChange={next=>onChange(next||null)}/>;
  if(field.type==='boolean')return <label>{name}<input type="checkbox" checked={value===true} onChange={event=>onChange(event.target.checked)}/></label>;
  if(field.type==='integer'||field.type==='number')return <NumberField label={name} value={typeof value==='number'?value:null}
    min={field.minimum??field.exclusiveMinimum} max={field.maximum} step={field.type==='integer'?1:'any'} onChange={onChange}/>;
  return <label>{name}<input value={typeof value==='string'?value:''} onChange={event=>onChange(event.target.value||null)}/></label>;
}

export function Producers({value,catalog,onChange}:{value:Definition;catalog:Catalog;onChange:(value:Definition)=>void}){
  const entries=value.indicators;
  function update(index:number,entry:Dependency){onChange({...value,indicators:entries.map((old,i)=>i===index?entry:old)});}
  function configured(kind:string,id=keyFor('indicator')):Dependency {
    const producer=catalog.indicators[kind];
    return {id,indicator:kind,algorithm_version:producer.algorithm_version,role:producer.roles[0],
      timeframe:producer.timeframes.includes(value.timeframes.trigger)?value.timeframes.trigger:producer.timeframes[0],
      settings:{},producers:[]};
  }
  return <section className="card strategy-block">
    <div className="strategy-heading"><h2>Indicator dependencies</h2>
      <button className="small-tool" type="button" disabled={entries.length>=24}
        onClick={()=>onChange({...value,indicators:[...entries,configured(Object.keys(catalog.indicators)[0])]})}>Add indicator</button></div>
    {entries.map((dep,index)=>{
      const producer=catalog.indicators[dep.indicator];
      return <details key={dep.id} className="strategy-subgroup">
        <summary>{dep.indicator} v{dep.algorithm_version} · {dep.role} · {dep.timeframe} · {dep.id}</summary>
        <div className="strategy-fields">
          <Choice label="Indicator type" options={Object.keys(catalog.indicators)} value={dep.indicator}
            onChange={kind=>kind&&update(index,configured(kind,dep.id))}/>
          <Choice label="Instrument role" options={producer.roles} value={dep.role} onChange={role=>role&&update(index,{...dep,role})}/>
          <Choice label="Indicator timeframe" options={producer.timeframes} value={dep.timeframe} onChange={timeframe=>timeframe&&update(index,{...dep,timeframe})}/>
          {producer.required_producers.map((kind,i)=><Choice key={kind} label={kind+' producer'}
            options={entries.filter(item=>item.indicator===kind&&item.role===dep.role&&item.timeframe===dep.timeframe).map(item=>item.id)}
            value={dep.producers[i]??''} blank="Choose exact producer" onChange={id=>update(index,{...dep,producers:id?[id]:[]})}/>)}
          {Object.entries(producer.parameters).map(([name,schema])=><div key={name}>
            {setting(fieldName(name),schema,Object.hasOwn(dep.settings,name)?dep.settings[name]:producer.defaults[name],
              next=>update(index,{...dep,settings:{...dep.settings,[name]:next}}))}
          </div>)}
        </div>
        {producer.required_canonical_producers.length>0&&<p className="muted">Canonical dependencies {producer.required_canonical_producers.join(', ')}</p>}
        <button className="small-tool" type="button" onClick={()=>onChange({...value,indicators:entries.filter(item=>item!==dep)})}>Remove indicator</button>
      </details>;
    })}
  </section>;
}

export function makeRule(dependency:Dependency,observation:string,catalog:Catalog,purpose:Definition['availability_modes'][0],id=keyFor('rule')):Rule {
  const spec=catalog.observations[observation];
  return {type:'RULE',id,dependency:dependency.id,observation,operator:spec.operators[0],expected:spec.values[0]??0,
    evidence:{state:spec.state_requirements[0],purpose,max_age_seconds:3600}};
}

function RuleFields({node,value,catalog,update}:{node:Rule;value:Definition;catalog:Catalog;update:(rule:Rule)=>void}){
  const dep=value.indicators.find(item=>item.id===node.dependency);
  const spec=catalog.observations[node.observation];
  const tracked=rulesIn(value.context).find(rule=>rule.alias&&rule.alias===value.attempt?.crt_reference_alias);
  const trackedAlias=dep?.indicator==='CRT'&&tracked?.id!==node.id?tracked?.alias:undefined;
  const requiredObservation=spec?.requires_bound_observation;
  const requiresFamily=Boolean(trackedAlias||requiredObservation);
  const aliases=(['context','setup','trigger','invalidation'] as const).flatMap(phase=>rulesIn(value[phase]))
    .filter(rule=>rule.id!==node.id&&rule.alias&&rule.dependency===node.dependency&&
      catalog.observations[rule.observation]?.category===spec?.category&&
      (!trackedAlias||rule.alias===trackedAlias)&&(!requiredObservation||rule.observation===requiredObservation))
    .map(rule=>rule.alias!);
  return <div className="strategy-fields">
    <Choice label="Rule dependency" options={value.indicators.map(item=>item.id)} value={node.dependency} blank="Choose dependency"
      display={id=>{const item=value.indicators.find(entry=>entry.id===id)!;return item.indicator+' · '+id;}}
      onChange={id=>{const selected=value.indicators.find(item=>item.id===id);if(selected)
        update(makeRule(selected,catalog.indicators[selected.indicator].observations[0],catalog,node.evidence.purpose,node.id));}}/>
    {dep&&<Choice label="Observation" options={catalog.indicators[dep.indicator].observations} value={node.observation}
      display={key=>catalog.observations[key].label} onChange={key=>key&&update(makeRule(dep,key,catalog,node.evidence.purpose,node.id))}/>}
    {spec&&<>
      <Choice label="Comparison operator" options={spec.operators} value={node.operator} onChange={operator=>operator&&update({...node,operator})}/>
      {spec.value_type==='enum'?<Choice label="Expected value" options={spec.values} value={String(node.expected)}
        onChange={expected=>expected&&update({...node,expected})}/>:<NumberField label="Expected price" value={Number(node.expected)}
        step="any" onChange={expected=>update({...node,expected:expected??0})}/>}
      <Choice label="Evidence state" options={spec.state_requirements} value={node.evidence.state} onChange={state=>state&&update({...node,evidence:{...node.evidence,state}})}/>
    </>}
    {value.schema_version===2&&<>
      {!requiresFamily&&<Choice label="Observation selection" value={node.selection??''}
        options={['EARLIEST_ELIGIBLE','LATEST_ELIGIBLE','BOUND_FAMILY'] as const} blank="Choose policy"
        onChange={selection=>update({...node,selection:selection||null,
          same_family_as:selection==='BOUND_FAMILY'?node.same_family_as:null})}/>}
      <label>Evidence alias<input maxLength={40} value={node.alias??''}
        onChange={event=>update({...node,alias:event.target.value||null})}/></label>
      {(requiresFamily||node.selection==='BOUND_FAMILY')&&<Choice label="Same family as" value={node.same_family_as??''}
        options={aliases} blank="Choose alias" onChange={alias=>update({...node,selection:'BOUND_FAMILY',same_family_as:alias||null})}/>}
      {requiresFamily&&aliases.length===0&&<p className="muted">{trackedAlias?
        'Use the tracked CRT reference dependency.':'Add a confirmed SMT direction rule with an alias on this dependency.'}</p>}
    </>}
    <NumberField label="Evidence age in seconds" value={node.evidence.max_age_seconds} min={1} max={86400}
      onChange={max_age_seconds=>update({...node,evidence:{...node.evidence,max_age_seconds:max_age_seconds??0}})}/>
  </div>;
}

export function GroupFields({node,value,catalog,update,depth=1}:{node:Group;value:Definition;catalog:Catalog;update:(group:Group)=>void;depth?:number}){
  const first=value.indicators[0];
  function replace(index:number,child:Node){update({...node,children:node.children.map((entry,i)=>i===index?child:entry)});}
  const allowed=node.children.length<24 && (node.operator!=='NOT'||node.children.length===0);
  return <fieldset className="strategy-group">
    <legend>{depth===1?'Conditions':node.id}</legend>
    <Choice label="Group operator" options={['ALL','ANY','NOT','SEQUENCE'] as const} value={node.operator}
      onChange={operator=>operator&&update({...node,operator,unknown_policy:operator==='NOT'?'PROPAGATE':null,
        within:operator==='SEQUENCE'?node.within:null})}/>
    {node.operator==='NOT'&&<p className="muted">Unknown stays unknown.</p>}
    {node.operator==='SEQUENCE'&&<BoundFields label="Sequence window" frame={value.timeframes.trigger} value={node.within} onChange={within=>update({...node,within})}/>}
    {node.children.map((child,i)=><div className="strategy-node" key={child.id}>
      {child.type==='GROUP'?<GroupFields node={child} value={value} catalog={catalog} depth={depth+1} update={next=>replace(i,next)}/>:
        <><p className="muted">{child.id}</p><RuleFields node={child} value={value} catalog={catalog} update={next=>replace(i,next)}/></>}
      <div className="strategy-actions">
        {node.operator==='SEQUENCE'&&i>0&&<button className="small-tool" type="button" onClick={()=>{
          const children=[...node.children];[children[i-1],children[i]]=[children[i],children[i-1]];update({...node,children});
        }}>Move earlier</button>}
        <button className="small-tool" type="button" onClick={()=>update({...node,children:node.children.filter(candidate=>candidate!==child)})}>Remove condition</button>
      </div>
    </div>)}
    <div className="strategy-actions">
      <button className="small-tool" type="button" disabled={!first||!allowed}
        onClick={()=>update({...node,children:[...node.children,makeRule(first,catalog.indicators[first.indicator].observations[0],catalog,value.availability_modes[0])]})}>Add condition</button>
      <button className="small-tool" type="button" disabled={!allowed||depth>=6||node.operator==='SEQUENCE'}
        onClick={()=>update({...node,children:[...node.children,groupFor('group')]})}>Add nested group</button>
    </div>
  </fieldset>;
}
