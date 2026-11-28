import {useEffect, useRef, useState, type ReactNode} from 'react';
import {X} from 'lucide-react';
import {TextArea} from './components';
import {api} from './api';
import {SnapshotDetails, type DecisionLocation} from './SnapshotDetails';
import type {JournalEntry} from './types';

// Keep the controls mounted when their panel expands, including overlay subscriptions.
export function TerminalSurface({title, active, close, children, inline=false}:{
  title:string; active:boolean; close:()=>void; children:ReactNode; inline?:boolean
}){
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{
    const dialog=ref.current!;
    if(active){
      if(dialog.open)dialog.close();
      dialog.showModal();
      const settings=dialog.querySelector<HTMLDetailsElement>('.indicator-library, :scope > details.data-trust');
      if(settings)settings.open=true;
      return ()=>dialog.close();
    }
    if(inline && !dialog.open)dialog.setAttribute('open','');
  },[active,inline]);
  return <dialog ref={ref} role={active?'dialog':'region'} className="terminal-surface" aria-label={title}
    onCancel={event=>{event.preventDefault();close();}}>
    {active&&<header><h2>{title}</h2><button type="button" aria-label={'Close '+title} onClick={close}><X size={18}/></button></header>}
    {children}
  </dialog>;
}

export function ChartJournal({onReturn,revision}:{onReturn?:(location:DecisionLocation)=>void;revision:number}){
  const [entries,setEntries]=useState<JournalEntry[]>();
  const [error,setError]=useState('');
  const [attempt,retry]=useState(0);
  useEffect(()=>{
    let current=true;
    setError('');setEntries(undefined);
    api.journal().then(list=>{if(current)setEntries(list.filter(e=>e.snapshot_id));})
      .catch(e=>{if(current)setError(e.message);});
    return ()=>{current=false;};
  },[revision,attempt]);
  if(error)return <div><p role="alert">{error}</p><button type="button" onClick={()=>retry(n=>n+1)}>Retry journal</button></div>;
  if(!entries)return <p role="status">Loading chart decisions…</p>;
  if(!entries.length)return <p>No journal entries with a chart capture.</p>;
  return <ul>{entries.map(entry=><li key={entry.id}><strong>{entry.title}</strong><SnapshotDetails id={entry.snapshot_id!} onReturn={onReturn}/></li>)}</ul>;
}


// The parent keys this editor by the immutable capture. Only journal refresh crosses captures.
export function ChartPassEditor({snapshot,symbol,active,onCommitted}:{
  snapshot:string;symbol:string;active:boolean;onCommitted:()=>void
}){
  const [reason,setReason]=useState('');
  const [pending,setPending]=useState(false);
  const [status,setStatus]=useState('');
  const edit=useRef(0);
  if(!active)return null;
  return <form onSubmit={async event=>{
    event.preventDefault();
    if(pending||reason.trim().length<5)return;
    const submitted=edit.current;
    setPending(true);setStatus('');
    try{
      await api.addJournal('CHART','No trade · '+symbol,reason.trim(),undefined,{snapshot_id:snapshot,tags:['no-trade']});
      onCommitted();
      if(submitted===edit.current){setReason('');setStatus('Pass saved to journal');}
      else setStatus('Pass saved with the earlier text. Your current edits are unsaved.');
    }catch(error){setStatus((submitted===edit.current?'':'Earlier submission failed. ')+(error as Error).message);}
    finally{setPending(false);}
  }}>
    <TextArea label="Reason to pass" value={reason} onChange={value=>{edit.current++;setReason(value);setStatus('');}}/>
    <button disabled={pending||reason.trim().length<5}>Save pass to journal</button>
    {status&&<p role="status">{status}</p>}
  </form>;
}
