import {useState} from 'react';

const prefix='tradeforge.workspace.';
const storeKey='tradeforge.presets.v1';
const enumSettings:Record<string,string[]> = {
  'chart-views':['1','2','3','4'], source:['MT5','MT5_SAVED','DUKASCOPY_SAVED','BINANCE_SPOT','DEMO'],
  'forex-symbol':['EURUSD','GBPUSD','USDJPY','XAUUSD'], 'show-smt':['on','off'],
  'comparison-placement':['Overlay','New pane'],
};
const frames=['M1','M3','M5','M15','M30','H1','H4','D1'];
type Preset={version:1;name:string;preferences:Record<string,string>};

function validSetting(key:string,value:unknown):value is string {
  if(typeof value!=='string'||value.length>64)return false;
  if(Object.hasOwn(enumSettings,key))return enumSettings[key].includes(value);
  if(/^(timeframe|secondary-timeframe|view-[34]-frame)$/.test(key))return frames.includes(value);
  if(/^(broker-symbol|chart-comparison|view-[234]-symbol)$/.test(key))return /^[a-z0-9._#+-]{0,64}$/i.test(value);
  if(/^(candles-up|candles-down|comparison-up|comparison-down)$/.test(key))return /^#[0-9a-f]{6}$/i.test(value);
  if(key==='timezone'){
    if(value==='auto')return true;
    try{new Intl.DateTimeFormat('en',{timeZone:value});return true;}catch{return false;}
  }
  if(key==='command-shortcut')return ['Ctrl+K','Ctrl+Shift+K','Alt+K'].includes(value);
  if(/^link-(crosshair|range|zoom|instrument|timeframe|replay)$/.test(key))return ['on','off'].includes(value);
  if(/^smt-(width|alignment|tolerance|confirmation)$/.test(key))return /^\d+(\.\d+)?$/.test(value)&&Number(value)<=100;
  return false;
}

export function parsePreset(raw:unknown):Preset {
  if(!raw||typeof raw!=='object')throw new Error('Invalid preset');
  const {version,name,preferences}=raw as Partial<Preset>;
  if(version!==1||typeof name!=='string'||!name.trim()||name.length>60||
    !preferences||typeof preferences!=='object'||Array.isArray(preferences))throw new Error('Invalid preset schema');
  const values=Object.entries(preferences);
  if(values.length>36||values.some(([key,value])=>!validSetting(key,value)))throw new Error('Preset contains unsupported settings');
  return {version:1,name:name.trim(),preferences:Object.fromEntries(values)};
}

export function listPresets():Preset[] {
  try {
    const list:unknown=JSON.parse(localStorage.getItem(storeKey)??'[]');
    return Array.isArray(list)?list.slice(0,20).map(parsePreset):[];
  }catch{return [];}
}

export function restorePreset(item:Preset){
  if(!window.confirm('Apply these workspace preferences and reload TradeForge?'))return;
  for(const [key,value] of Object.entries(item.preferences))localStorage.setItem(prefix+key,value);
  window.location.reload();
}

export function WorkspacePresets() {
  const [presets,setPresets]=useState(listPresets);
  const [name,setName]=useState('');
  const [error,setError]=useState('');
  const [renaming,setRenaming]=useState<string>();
  const [newName,setNewName]=useState('');
  function store(preset:Preset,replace?:string) {
    if(presets.some(item=>item.name===preset.name&&item.name!==replace))throw new Error('This preset name already exists');
    const next=[...presets.filter(item=>item.name!==replace),preset];
    if(next.length>20)throw new Error('Delete a preset before saving another');
    localStorage.setItem(storeKey,JSON.stringify(next));setPresets(next);setName('');setError('');
  }
  function attempt(fn:()=>void){try{fn();}catch(reason){setError((reason as Error).message);}}
  function capture() {
    const preferences:Record<string,string>={};
    for(let i=0;i<localStorage.length;i++){
      const stored=localStorage.key(i);
      if(!stored?.startsWith(prefix))continue;
      const key=stored.slice(prefix.length);const value=localStorage.getItem(stored);
      if(validSetting(key,value))preferences[key]=value;
    }
    store(parsePreset({version:1,name,preferences}));
  }
  return <section className="card data-trust">
    <h2>Workspace presets</h2>
    <div className="research-controls">
      <label>Preset name<input value={name} maxLength={60} onChange={e=>setName(e.target.value)}/></label>
      <button className="btn btn-primary" disabled={!name.trim()} onClick={()=>attempt(capture)}>Save current workspace</button>
      <label>Import preset JSON<input type="file" accept=".json,application/json" onChange={async event=>{
        const file=event.target.files?.[0];event.target.value='';
        if(!file)return;
        if(file.size>10000){setError('Preset file exceeds 10 KB');return;}
        try{store(parsePreset(JSON.parse(await file.text())));}catch(reason){setError((reason as Error).message);}
      }}/></label>
    </div>
    <button className="small-tool" onClick={()=>attempt(()=>{
      if(!window.confirm('Reset chart and workspace preferences to defaults?'))return;
      const keys=Array.from({length:localStorage.length},(_,index)=>localStorage.key(index));
      keys.forEach(key=>{
        if(key?.startsWith(prefix)&&validSetting(key.slice(prefix.length),localStorage.getItem(key)))localStorage.removeItem(key);
      });
      window.location.reload();
    })}>Restore default preferences</button>
    {error&&<p role="alert">{error}</p>}
    {presets.map(item=><div className="research-controls" key={item.name}>
      <strong>{item.name}</strong>
      {renaming===item.name?<><label>New name<input value={newName} maxLength={60} onChange={event=>setNewName(event.target.value)}/></label>
        <button className="small-tool" onClick={()=>attempt(()=>{store(parsePreset({...item,name:newName}),item.name);setRenaming(undefined);})}>Save name</button>
        <button className="small-tool" onClick={()=>setRenaming(undefined)}>Cancel rename</button></>:
        <button className="small-tool" onClick={()=>{setRenaming(item.name);setNewName(item.name);}}>Rename</button>}
      <button className="small-tool" onClick={()=>attempt(()=>{
        let number=2;
        const base=item.name.slice(0,51);
        while(presets.some(saved=>saved.name===`${base} (${number})`))number++;
        store({...item,name:`${base} (${number})`});
      })}>Duplicate</button>
      <button className="small-tool" onClick={()=>attempt(()=>{
        restorePreset(item);
      })}>Restore</button>
      <a className="small-tool" download="tradeforge-workspace.json" href={'data:application/json;charset=utf-8,'+encodeURIComponent(JSON.stringify(item,null,2))}>Export</a>
      <button className="small-tool" onClick={()=>attempt(()=>{
        if(!window.confirm('Delete this workspace preset?'))return;
        const next=presets.filter(preset=>preset.name!==item.name);localStorage.setItem(storeKey,JSON.stringify(next));setPresets(next);
      })}>Delete</button>
    </div>)}
  </section>;
}
