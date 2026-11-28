import {useEffect, useRef, useState, type ReactNode} from 'react';
import {X} from 'lucide-react';
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
  useEffect(()=>{
    let current=true;
    api.journal().then(list=>{if(current)setEntries(list.filter(e=>e.snapshot_id));})
      .catch(e=>{if(current)setError(e.message);});
    return ()=>{current=false;};
  },[revision]);
  if(error)return <p role="alert">{error}</p>;
  if(!entries)return <p role="status">Loading chart decisions…</p>;
  if(!entries.length)return <p>No journal entries with a chart capture.</p>;
  return <ul>{entries.map(entry=><li key={entry.id}><strong>{entry.title}</strong><SnapshotDetails id={entry.snapshot_id!} onReturn={onReturn}/></li>)}</ul>;
}
