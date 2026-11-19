import {BoundFields,Choice,NumberField,fieldName,rulesIn} from './StrategyRules';
import type {Definition,Instrument,Purpose,Node,Group,TradeIntent,Attempt} from './strategyTypes';

export function withPurpose(definition:Definition,purpose:Purpose):Definition {
  function replace(node:Node):Node{
    return node.type==='RULE'?{...node,evidence:{...node.evidence,purpose}}:{...node,children:node.children.map(replace)};
  }
  const change=(group:Group|null)=>group?replace(group) as Group:null;
  return {...definition,availability_modes:[purpose],context:change(definition.context),setup:change(definition.setup),
    trigger:change(definition.trigger),invalidation:change(definition.invalidation)};
}

function InstrumentFields({label,value,onChange}:{label:string;value:Instrument;onChange:(value:Instrument)=>void}){
  return <fieldset className="strategy-subgroup"><legend>{label}</legend><div className="strategy-fields">
    <Choice label={label+' symbol'} value={value.symbol} options={['EURUSD','GBPUSD'] as const} onChange={symbol=>symbol&&onChange({...value,symbol})}/>
    <Choice label={label+' source'} value={value.source} options={['DUKASCOPY_SAVED','MT5_SAVED'] as const} onChange={source=>source&&onChange({...value,source})}/>
    <Choice label={label+' basis'} value={value.price_basis} options={['BID','ASK','LAST_TRADE'] as const} onChange={price_basis=>price_basis&&onChange({...value,price_basis})}/>
    <NumberField label={label+' modeled delay (seconds)'} value={value.modeled_delay_seconds} min={0} max={86400}
      onChange={delay=>onChange({...value,modeled_delay_seconds:delay??0})}/>
  </div></fieldset>;
}

export function ScopeFields({value,onChange}:{value:Definition;onChange:(value:Definition)=>void}){
  const scope=value.instruments;
  return <section className="card strategy-block"><h2>Evidence scope</h2>
    <div className="strategy-fields">
      <Choice label="Evaluation purpose" value={value.availability_modes[0]} options={['HISTORICAL_SIMULATION','STRICT_RECORDED'] as const}
        display={item=>item==='HISTORICAL_SIMULATION'?'Historical simulation':'Strict recorded'} onChange={purpose=>purpose&&onChange(withPurpose(value,purpose))}/>
      {(['context','trigger'] as const).map(name=><Choice key={name} label={name[0].toUpperCase()+name.slice(1)+' timeframe'}
        value={value.timeframes[name]} options={['M1','M5'] as const}
        onChange={frame=>frame&&onChange({...value,timeframes:{...value.timeframes,[name]:frame}})}/>)}
      <Choice label="Market calendar" options={['forex_weekly','continuous'] as const}
        value={scope.calendar.kind} onChange={kind=>kind&&onChange({...value,instruments:{...scope,calendar:{...scope.calendar,kind}}})}/>
      {scope.calendar.kind==='forex_weekly'&&<>
        <label>Calendar timezone<input value={scope.calendar.timezone}
          onChange={e=>onChange({...value,instruments:{...scope,calendar:{...scope.calendar,timezone:e.target.value}}})}/></label>
        {(['sunday_open','friday_close'] as const).map(name=><label key={name}>{fieldName(name)}
          <input type="time" value={scope.calendar[name]}
            onChange={e=>onChange({...value,instruments:{...scope,calendar:{...scope.calendar,[name]:e.target.value}}})}/></label>)}
      </>}
    </div>
    <InstrumentFields label="Primary" value={scope.primary} onChange={primary=>onChange({...value,instruments:{...scope,primary}})}/>
    {scope.comparison&&<InstrumentFields label="Comparison" value={scope.comparison} onChange={comparison=>onChange({...value,instruments:{...scope,comparison}})}/>}
    <button className="small-tool" type="button" onClick={()=>{
      const comparison:Instrument|null=scope.comparison?null:{...scope.primary,symbol:scope.primary.symbol==='EURUSD'?'GBPUSD':'EURUSD'};
      onChange({...value,instruments:{...scope,comparison}});
    }}>{scope.comparison?'Remove comparison':'Add comparison'}</button>
  </section>;
}

const emptyIntent:TradeIntent={role:null,direction:null,entry:null,earliest:null,expiry:null};

export function upgradeDraft(value:Definition):Definition {
  const {entry_intent,earliest,expiry,...costs}=value.execution;
  return {...value,schema_version:2,defaults_policy:'STRATEGY_DEFAULTS_V2',status:'DRAFT',execution:costs,attempt:null,
    trade_intent:{...emptyIntent,earliest:earliest==='AFTER_CAUSAL_AVAILABILITY'?earliest:null,expiry:expiry??null,
      entry:entry_intent==='MARKET'?{kind:entry_intent}:entry_intent?{kind:entry_intent,price:null}:null}};
}

export function AttemptFields({value,onChange}:{value:Definition;onChange:(value:Definition)=>void}){
  const attempt=value.attempt;
  const update=(next:Attempt|null)=>onChange({...value,attempt:next});
  const aliases=rulesIn(value.context).filter(rule=>rule.observation==='CRT_REFERENCE'&&rule.alias).map(rule=>rule.alias!);
  return <section className="card strategy-block"><h2>Attempt and context</h2>
    <div className="strategy-fields">
      <Choice label="Context activation" value={attempt?.context_mode??''} options={['LATCHED','CONTINUOUS'] as const}
        blank="Unspecified" onChange={context_mode=>update(context_mode?{
          expiry_origin:'CONTEXT_ACTIVATION',candidate_limit:32,crt_reference_alias:null,...attempt,context_mode,
        }:null)}/>
      {attempt&&<>
        <Choice label="Expiry origin" value={attempt.expiry_origin} options={['CONTEXT_ACTIVATION','SETUP_ACTIVATION'] as const}
          onChange={expiry_origin=>expiry_origin&&update({...attempt,expiry_origin})}/>
        <NumberField label="Candidate limit" value={attempt.candidate_limit} min={1} max={128}
          onChange={limit=>update({...attempt,candidate_limit:limit??0})}/>
        <Choice label="Tracked CRT family" value={attempt.crt_reference_alias??''} options={aliases} blank="Unspecified"
          onChange={alias=>update({...attempt,crt_reference_alias:alias||null})}/>
      </>}
    </div>
    <p className="muted">The selected CRT family stays pinned. Eligible-bar windows use the traded instrument and the chosen timeframe.</p>
  </section>;
}

function TradeFields({value,onChange}:{value:Definition;onChange:(value:Definition)=>void}){
  const trade=value.trade_intent??emptyIntent;
  const set=(update:Partial<TradeIntent>)=>onChange({...value,trade_intent:{...trade,...update}});
  const basis=(trade.role==='COMPARISON'?value.instruments.comparison:value.instruments.primary)?.price_basis;
  const entry=trade.entry;
  return <>
    <Choice label="Traded instrument" value={trade.role??''} options={value.instruments.comparison?['PRIMARY','COMPARISON'] as const:['PRIMARY'] as const}
      blank="Unspecified" onChange={role=>set({role:role||null})}/>
    <Choice label="Trade direction" value={trade.direction??''} options={['LONG','SHORT'] as const}
      blank="Unspecified" onChange={direction=>set({direction:direction||null})}/>
    <Choice label="Entry intent" options={['MARKET','LIMIT','STOP'] as const} value={entry?.kind??''} blank="Unspecified"
      onChange={kind=>set({entry:kind==='MARKET'?{kind}:kind?{kind,price:null}:null})}/>
    {entry&&entry.kind!=='MARKET'&&<>
      <NumberField label="Fixed absolute entry price" value={entry.price?.value??null} min={0} step="any"
        onChange={amount=>set({entry:{kind:entry.kind,price:amount!==null&&basis?{
          kind:'FIXED_ABSOLUTE',value:amount,basis:entry.price?.basis??basis,
        }:null}})}/>
      <Choice label="Entry price basis" value={entry.price?.basis??basis??''} options={['BID','ASK','LAST_TRADE'] as const}
        onChange={next=>{if(next&&entry.price)set({entry:{kind:entry.kind,price:{...entry.price,basis:next}}});}}/>
    </>}
    <Choice label="Earliest execution" value={trade.earliest??''} options={['AFTER_CAUSAL_AVAILABILITY'] as const}
      blank="Unspecified" onChange={earliest=>set({earliest:earliest||null})}/>
    <BoundFields label="Order expiry" value={trade.expiry} frame={value.timeframes.trigger} onChange={expiry=>set({expiry})}/>
  </>;
}

export function Policies({value,onChange}:{value:Definition;onChange:(value:Definition)=>void}){
  const {risk,execution,management}=value;
  const frame=value.timeframes.trigger;
  const exposureRoles=value.instruments.comparison?['PRIMARY','COMPARISON'] as const:['PRIMARY'] as const;
  return <div className="strategy-policies">
    <section className="card strategy-block"><h2>Risk</h2><div className="strategy-fields">
      {([
        ['account_risk_pct','Account risk per trade (%)',2],['max_simultaneous_exposure_pct','Simultaneous exposure (%)',100],
        ['max_session_loss_pct','Session loss limit (%)',100],['min_reward_risk','Minimum reward / risk',1000],
      ] as const).map(([key,label,max])=><NumberField key={key} label={label} value={risk[key]} step="any" min={0} max={max}
        onChange={amount=>onChange({...value,risk:{...risk,[key]:amount}})}/>)}
      {exposureRoles.map(role=><NumberField key={role} label={role+' instrument risk cap (%)'} step="any" min={0} max={100}
        value={risk.instrument_limits.find(item=>item.role===role)?.max_risk_percent??null} onChange={amount=>{
          const limits=risk.instrument_limits.filter(item=>item.role!==role);
          if(amount!==null)limits.push({role,max_risk_percent:amount});
          onChange({...value,risk:{...risk,instrument_limits:limits}});
        }}/>)}
    </div></section>
    <section className="card strategy-block"><h2>Execution intent</h2><div className="strategy-fields">
      {value.schema_version===2?<TradeFields value={value} onChange={onChange}/>:
        <p>Legacy entry intent {execution.entry_intent??'unspecified'}</p>}
      <Choice label="Execution price basis" options={['BID','ASK','LAST_TRADE'] as const} value={execution.price_basis??''} blank="Unspecified"
        onChange={price_basis=>onChange({...value,execution:{...execution,price_basis:price_basis||null}})}/>
      <Choice label="Intrabar ambiguity" options={['REJECT_AMBIGUOUS','ADVERSE_FIRST'] as const} value={execution.intrabar??''} blank="Unspecified"
        onChange={intrabar=>onChange({...value,execution:{...execution,intrabar:intrabar||null}})}/>
      {(['spread_bps','slippage_bps','commission_bps'] as const).map(key=><NumberField key={key} label={fieldName(key)}
        value={execution[key]} step="any" min={0} max={10000} onChange={amount=>onChange({...value,execution:{...execution,[key]:amount}})}/>)}
    </div></section>
    <section className="card strategy-block"><h2>Management</h2><div className="strategy-fields">
      <NumberField label="Initial stop distance (pips)" value={management.initial_stop?.distance??null} step="any"
        onChange={distance=>onChange({...value,management:{...management,initial_stop:distance===null?null:{kind:'FIXED_PIPS',distance}}})}/>
      <NumberField label="Target (R multiple)" value={management.target?.multiple??null} step="any"
        onChange={multiple=>onChange({...value,management:{...management,target:multiple===null?null:{kind:'REWARD_MULTIPLE',multiple}}})}/>
      <NumberField label="Break-even (R multiple)" value={management.break_even_at_r} step="any"
        onChange={break_even_at_r=>onChange({...value,management:{...management,break_even_at_r}})}/>
      <NumberField label="Trailing stop (pips)" value={management.trailing_pips} step="any"
        onChange={trailing_pips=>onChange({...value,management:{...management,trailing_pips}})}/>
      {management.partial_exit&&<>
        <NumberField label="Partial exit at (R multiple)" value={management.partial_exit.at_r} step="any" onChange={at_r=>
          onChange({...value,management:{...management,partial_exit:{at_r:at_r??0,percent:management.partial_exit!.percent}}})}/>
        <NumberField label="Partial exit size (%)" value={management.partial_exit.percent} step="any" onChange={percent=>
          onChange({...value,management:{...management,partial_exit:{at_r:management.partial_exit!.at_r,percent:percent??0}}})}/>
      </>}
    </div>
      <button className="small-tool" type="button" onClick={()=>onChange({...value,management:{...management,
        partial_exit:management.partial_exit?null:{at_r:0,percent:0}}})}>{management.partial_exit?'Remove partial exit':'Configure partial exit'}</button>
      <BoundFields label="Time-based exit" value={management.time_exit} frame={frame} onChange={time_exit=>onChange({...value,management:{...management,time_exit}})}/>
    </section>
  </div>;
}
