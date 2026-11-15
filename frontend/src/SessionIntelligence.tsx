import {useEffect,useMemo,useState} from 'react';
import {useTimestampFormat,useTimezone} from './timezone';
import {useWorkspacePreference} from './useWorkspacePreference';
import type {Candle} from './types';
import type {Drawing} from './Drawings';

const sessions=[
  {id:'asia',name:'Asia',zone:'Asia/Tokyo',hours:'09:00-17:00',kill:'09:00-11:00',color:'#658dba'},
  {id:'london',name:'London',zone:'Europe/London',hours:'08:00-16:00',kill:'08:00-10:00',color:'#74bcb3'},
  {id:'new-york',name:'New York',zone:'America/New_York',hours:'08:00-16:00',kill:'08:00-10:00',color:'#a383b9'},
];
const wallFormats=new Map<string,Intl.DateTimeFormat>();
function wallTime(epoch:number,zone:string) {
  let format=wallFormats.get(zone);
  if(!format){
    format=new Intl.DateTimeFormat('en-GB',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
    wallFormats.set(zone,format);
  }
  const parts=format.formatToParts(new Date(epoch*1000));
  const part=(type:string)=>Number(parts.find(item=>item.type===type)?.value);
  return Date.UTC(part('year'),part('month')-1,part('day'),part('hour'),part('minute'))/1000;
}

export function sessionBounds(epoch:number,zone:string,hours:string,dayOffset=0):[number,number]|undefined {
  if(!/^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$/.test(hours))return;
  const day=(Math.floor(wallTime(epoch,zone)/86400)+dayOffset)*86400;
  const targets=hours.split('-').map(value=>{
    const [hour,minute]=value.split(':').map(Number);return day+hour*3600+minute*60;
  });
  if(targets[1]<=targets[0])return;
  const utc=targets.map(target=>{
    let guess=target;
    for(let i=0;i<4;i++)guess+=target-wallTime(guess,zone);
    return guess;
  });
  if(utc.some((value,i)=>wallTime(value,zone)!==targets[i] ||
      [1800,3600,7200].some(offset=>wallTime(value-offset,zone)===targets[i]||wallTime(value+offset,zone)===targets[i])))return;
  return [utc[0],utc[1]];
}

function eligible(rows:Candle[],step:number,cutoff:number,bounds:[number,number]|undefined){
  return bounds?rows.filter(bar=>bar.time>=bounds[0]&&(bar.available_at??bar.time+step)<=cutoff&&bar.time+step<=bounds[1]):[];
}

function rangeText(rows:Candle[],digits:number){
  if(!rows.length)return 'Unavailable';
  const high=rows.reduce((level,bar)=>Math.max(level,bar.high),-Infinity);
  const low=rows.reduce((level,bar)=>Math.min(level,bar.low),Infinity);
  return `${high.toFixed(digits)} / ${low.toFixed(digits)} · ${rows.length} bars`;
}

function SessionRow({session,rows,step,cutoff,digits,onZone}: {
  session:typeof sessions[number];rows:Candle[];step:number;cutoff:number;digits:number;
  onZone:(id:string,drawing?:Drawing)=>void;
}) {
  const [hours,save]=useWorkspacePreference('session-v1-'+session.id,session.hours);
  const [kill,saveKill]=useWorkspacePreference('kill-v1-'+session.id,session.kill);
  const [overlay,setOverlay]=useWorkspacePreference('overlay-v1-'+session.id,'off',['on','off']);
  const format=useTimestampFormat();
  const bounds=sessionBounds(cutoff,session.zone,hours);
  const zone=sessionBounds(cutoff,session.zone,kill);
  const observed=eligible(rows,step,cutoff,bounds);
  const killBars=eligible(rows,step,cutoff,zone);
  let previous:Candle[]=[];
  let previousBounds:[number,number]|undefined;
  for(let day=-1;day>=-7&&!previous.length;day--){
    previousBounds=sessionBounds(cutoff,session.zone,hours,day);
    previous=eligible(rows,step,cutoff,previousBounds);
  }
  const overlayFirst=killBars[0]?.time;
  const overlayLast=killBars.at(-1)?.time;
  useEffect(()=>{
    const item=overlay==='on'&&overlayFirst!==undefined&&overlayLast!==undefined?{
      kind:'time-range' as const,id:session.id,color:session.color,visible:true,locked:true,label:session.name+' kill zone',
      anchors:[{time:overlayFirst,price:'1'},{time:overlayLast,price:'1'}],created_at:0,updated_at:0,
    }:undefined;
    onZone(session.id,item);
    return ()=>onZone(session.id);
  },[overlay,overlayFirst,overlayLast,onZone,session]);
  const until=bounds?(cutoff<bounds[0]?bounds[0]-cutoff:cutoff<bounds[1]?bounds[1]-cutoff:0):0;
  const duration=until?`${Math.floor(until/3600)}h ${Math.floor(until%3600/60)}m to ${cutoff<bounds![0]?'open':'close'}`:'Window ended';
  return <section><h3>{session.name}</h3>
    <label>{session.zone} hours <input value={hours} maxLength={11} onChange={event=>save(event.target.value)}/></label>
    <p>{bounds?`${format(bounds[0]*1000)} to ${format(bounds[1]*1000)} · ${duration}`:'Use same-day HH:MM-HH:MM hours outside DST ambiguity.'}</p>
    <p>Observed high / low · {rangeText(observed,digits)}</p>
    <p>Previous observed window{previous.length&&previousBounds?` (${format(previousBounds[0]*1000)})`:''} · {rangeText(previous,digits)}</p>
    <label>Kill zone (local) <input value={kill} maxLength={11} onChange={event=>saveKill(event.target.value)}/></label>
    <label><input type="checkbox" checked={overlay==='on'} onChange={event=>setOverlay(event.target.checked?'on':'off')}/> Show kill zone</label>
  </section>;
}

export function SessionIntelligence({rows,step,cutoff,digits,onZone}: {
  rows:Candle[];step:number;cutoff:number;digits:number;onZone:(id:string,drawing?:Drawing)=>void;
}) {
  const {zone}=useTimezone();
  const bounds=useMemo(()=>{
    const date=new Date(cutoff*1000);
    const day=Math.floor(cutoff/86400)*86400;
    return {day,week:day-((date.getUTCDay()+6)%7)*86400};
  },[cutoff]);
  const daily=rows.find(bar=>bar.time===bounds.day);
  const weekly=rows.find(bar=>bar.time===bounds.week);
  const [opened,setOpened]=useState(false);
  return <details className="data-trust" onToggle={event=>setOpened(event.currentTarget.open)}>
    <summary>Sessions · {zone}</summary>
    {opened&&<p>00:00 UTC open {daily?.open.toFixed(digits)??'Unavailable'} · Monday UTC open {weekly?.open.toFixed(digits)??'Unavailable'}</p>}
    <div className="evidence-pairs">{sessions.map(session=><SessionRow key={session.id} session={session}
      rows={rows} step={step} cutoff={cutoff} digits={digits} onZone={onZone}/>)}</div>
  </details>;
}
