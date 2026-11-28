import {useEffect,useRef,useState} from 'react';
import {api} from './api';
import {TERMINAL_SHORTCUT_GUIDE} from './terminalKeyboard';
import {listPresets,restorePreset} from './WorkspacePresets';
import {useWorkspacePreference} from './useWorkspacePreference';

export type CommandTarget={type:'plan'|'dataset'|'signal'|'journal';id:string;label:string;status?:'SMT_CONFIRMED'};
const shortcuts=['Ctrl+K','Ctrl+Shift+K','Alt+K'] as const;
type Shortcut=typeof shortcuts[number];
const recentsKey='tradeforge.commands.recent.v1';

export function CommandSettings(){
  const [value,setValue]=useWorkspacePreference<Shortcut>('command-shortcut','Ctrl+K',shortcuts);
  useEffect(()=>{window.dispatchEvent(new Event('tradeforge-command-shortcut'));},[value]);
  return <section className="card data-trust">
    <h2>Keyboard shortcuts</h2>
    <label className="field">Command palette<select value={value} onChange={event=>{
      setValue(event.target.value as Shortcut);
    }}>{shortcuts.map(shortcut=><option key={shortcut}>{shortcut}</option>)}</select></label>
    <dl className="evidence-grid">
      {TERMINAL_SHORTCUT_GUIDE.map(([binding, action])=><div key={binding}><dt>{binding}</dt><dd>{action}</dd></div>)}
    </dl>
  </section>;
}

function configuredShortcut():Shortcut{
  try{const value=localStorage.getItem('tradeforge.workspace.command-shortcut') as Shortcut;
    if(shortcuts.includes(value))return value;}catch{}
  return 'Ctrl+K';
}
function recentCommands():string[]{
  try{const value:unknown=JSON.parse(localStorage.getItem(recentsKey)??'[]');
    if(Array.isArray(value))return value.filter(item=>typeof item==='string').slice(0,6);}catch{}
  return [];
}


export function CommandPalette({commands,onTarget}: {commands:{id:string;label:string;run:()=>void}[];onTarget:(target:CommandTarget)=>Promise<void>}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const [query,setQuery]=useState('');
  const [active,setActive]=useState(0);
  const [visible,setVisible]=useState(false);
  const [shortcut,setShortcut]=useState(configuredShortcut);
  const [recent,setRecent]=useState(recentCommands);
  const [offset,setOffset]=useState(0);
  const [remote,setRemote]=useState<CommandTarget[]>([]);
  const [next,setNext]=useState<number|null>(null);
  const [failure,setFailure]=useState('');
  const [performing,setPerforming]=useState(false);
  useEffect(()=>{
    const refresh=()=>setShortcut(configuredShortcut());
    window.addEventListener('tradeforge-command-shortcut',refresh);
    window.addEventListener('storage',refresh);
    return ()=>{window.removeEventListener('tradeforge-command-shortcut',refresh);window.removeEventListener('storage',refresh);};
  },[]);
  useEffect(()=>{
    if(!visible||query.trim().length<2)return;
    let live=true;
    const timer=window.setTimeout(()=>{
      api.get<{items:CommandTarget[];next_offset:number|null}>('/workspace/search?'+new URLSearchParams({q:query,offset:String(offset)}))
        .then(page=>{if(live){setRemote(page.items);setNext(page.next_offset);}})
        .catch(()=>{if(live)setFailure('Workspace search unavailable');});
    },180);
    return ()=>{live=false;window.clearTimeout(timer);};
  },[visible,query,offset]);
  const keyword=query.trim().toLowerCase();
  const installed=visible?listPresets().map(item=>({id:'preset:'+item.name,label:'Restore workspace '+item.name,run:()=>restorePreset(item)})):[];
  const local=[...commands,...installed].filter(command=>command.label.toLowerCase().includes(keyword)).sort((a,b)=>{
    if(keyword)return Number(!a.label.toLowerCase().startsWith(keyword))-Number(!b.label.toLowerCase().startsWith(keyword));
    const rank=(id:string)=>recent.includes(id)?recent.indexOf(id):recent.length;
    return rank(a.id)-rank(b.id);
  }).slice(0,12);
  const results=[...local,...remote.map(item=>({id:item.type+':'+item.id,label:item.label,run:()=>onTarget(item)}))];
  const open=()=>{setQuery('');setOffset(0);setRemote([]);setNext(null);setFailure('');setActive(0);setVisible(true);if(!dialog.current?.open)dialog.current?.showModal();};
  useEffect(()=>{
    const key=(event:KeyboardEvent)=>{
      const expected=event.altKey&&!event.ctrlKey&&!event.shiftKey?'Alt+K':
        event.ctrlKey&&!event.altKey?(event.shiftKey?'Ctrl+Shift+K':'Ctrl+K'):undefined;
      if(expected!==shortcut||event.key.toLowerCase()!=='k'||event.defaultPrevented||
        document.querySelector('dialog[open],[role="dialog"]')||
        (event.target instanceof Element&&event.target.closest('input,textarea,select,[contenteditable],.drawer,.small-modal')))return;
      event.preventDefault();open();
    };
    window.addEventListener('keydown',key);
    return()=>window.removeEventListener('keydown',key);
  },[shortcut]);
  const run=async(command:typeof results[number])=>{
    if(performing)return;
    setPerforming(true);setFailure('');
    try{
      await command.run();
      const history=[command.id,...recent.filter(item=>item!==command.id)].slice(0,6);
      setRecent(history);
      try{localStorage.setItem(recentsKey,JSON.stringify(history));}catch{}
      dialog.current?.close();
    }catch(error){setFailure((error as Error).message);}
    finally{setPerforming(false);}
  };
  return <>
    <button className="small-tool" onClick={open}>Commands <kbd>{shortcut}</kbd></button>
    <dialog ref={dialog} className="command-palette" onClose={()=>setVisible(false)}>
      <form onSubmit={event=>{event.preventDefault();if(results[active])run(results[active]);}}>
        <label>Search workspace commands<input autoFocus value={query} onChange={event=>{setQuery(event.target.value);setActive(0);setOffset(0);setRemote([]);setNext(null);setFailure('');}}
          onKeyDown={event=>{
            if(event.key==='ArrowDown'||event.key==='ArrowUp'){
              event.preventDefault();setActive(Math.max(0,Math.min(results.length-1,active+(event.key==='ArrowDown'?1:-1))));
            }
          }}/></label>
        {results.length?<ul>{results.map((command,index)=><li key={command.id}>
          <button type="button" className="signal-row" aria-current={index===active} onClick={()=>run(command)}>{command.label}</button>
        </li>)}</ul>:<p>No matching commands.</p>}
        {failure&&<p role="alert">{failure}</p>}
        {next!==null&&<button type="button" className="small-tool" onClick={()=>{setOffset(next);setActive(0);}}>More results</button>}
        <button type="button" className="small-tool" onClick={()=>dialog.current?.close()}>Close</button>
      </form>
    </dialog>
  </>;
}
