import {BoundFields,Choice,NumberField,fieldName} from './StrategyRules';
import type {Definition,Instrument,Purpose,Node,Group} from './strategyTypes';

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
      <Choice label="Entry intent" options={['MARKET','LIMIT','STOP'] as const} value={execution.entry_intent??''} blank="Unspecified"
        onChange={entry_intent=>onChange({...value,execution:{...execution,entry_intent:entry_intent||null}})}/>
      <Choice label="Execution price basis" options={['BID','ASK','LAST_TRADE'] as const} value={execution.price_basis??''} blank="Unspecified"
        onChange={price_basis=>onChange({...value,execution:{...execution,price_basis:price_basis||null}})}/>
      <Choice label="Intrabar ambiguity" options={['REJECT_AMBIGUOUS','ADVERSE_FIRST'] as const} value={execution.intrabar??''} blank="Unspecified"
        onChange={intrabar=>onChange({...value,execution:{...execution,intrabar:intrabar||null}})}/>
      {(['spread_bps','slippage_bps','commission_bps'] as const).map(key=><NumberField key={key} label={fieldName(key)}
        value={execution[key]} step="any" min={0} max={10000} onChange={amount=>onChange({...value,execution:{...execution,[key]:amount}})}/>)}
    </div><p className="muted">Earliest execution intent is after causal availability.</p>
      <BoundFields label="Order expiry" value={execution.expiry} frame={frame} onChange={expiry=>onChange({...value,execution:{...execution,expiry}})}/>
    </section>
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
