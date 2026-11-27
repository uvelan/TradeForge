import {useState} from "react";
import type {CandleRange} from "./types";

export function SavedRange({onChange,range,onFreeze,freezeBusy,onEdit}:{
  onChange:(range?:CandleRange)=>void; range?:CandleRange;
  onFreeze?:()=>void; freezeBusy?:boolean; onEdit?:()=>void;
}){
  const stamp=(time:number|undefined)=>time===undefined?"":new Date(time*1000).toISOString();
  const [from,setFrom]=useState(()=>stamp(range?.start).slice(0,10));
  const [until,setUntil]=useState(()=>stamp(range?.end).slice(0,10));
  const [fromTime,setFromTime]=useState(()=>stamp(range?.start).slice(11,16));
  const [untilTime,setUntilTime]=useState(()=>stamp(range?.end).slice(11,16));
  const start=Date.parse(`${from}T${fromTime||"00:00"}Z`)/1000;
  const end=Date.parse(`${until}T${untilTime||"00:00"}Z`)/1000;
  const loaded=range?.start===start&&range?.end===end;
  const edit=(update:(value:string)=>void,value:string)=>{
    update(value);
    onEdit?.();
  };
  return <form className="indicator-controls saved-range-controls" aria-label="Saved archive range" onSubmit={event=>{
    event.preventDefault();
    if(start<end)onChange({start,end});
  }}>
    <label>From UTC <input type="date" className="archive-date" value={from} onChange={event=>edit(setFrom,event.target.value)}/></label>
    <label>From time (UTC) <input type="time" value={fromTime} onChange={event=>edit(setFromTime,event.target.value)}/></label>
    <label>Until UTC (exclusive) <input type="date" className="archive-date" value={until} onChange={event=>edit(setUntil,event.target.value)}/></label>
    <label>Until time (UTC) <input type="time" value={untilTime} onChange={event=>edit(setUntilTime,event.target.value)}/></label>
    <button className="small-tool" type="submit" disabled={!(start<end)}>Load saved range</button>
    {range&&onFreeze&&<button className="small-tool active" type="button" onClick={onFreeze}
      disabled={!loaded||freezeBusy} title={loaded?'Freeze up to 5,000 closed candles per pane':undefined}>Freeze range for replay</button>}
    {range&&<button className="small-tool" type="button" onClick={()=>onChange()}>Use recent archive</button>}
  </form>;
}
