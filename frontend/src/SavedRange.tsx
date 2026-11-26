import {useState} from "react";
import type {CandleRange} from "./types";

export function SavedRange({onChange,range}:{onChange:(range?:CandleRange)=>void;range?:CandleRange}){
  const stamp=(time:number|undefined)=>time===undefined?"":new Date(time*1000).toISOString();
  const [from,setFrom]=useState(()=>stamp(range?.start).slice(0,10));
  const [until,setUntil]=useState(()=>stamp(range?.end).slice(0,10));
  const [fromTime,setFromTime]=useState(()=>stamp(range?.start).slice(11,16));
  const [untilTime,setUntilTime]=useState(()=>stamp(range?.end).slice(11,16));
  const start=Date.parse(`${from}T${fromTime||"00:00"}Z`)/1000;
  const end=Date.parse(`${until}T${untilTime||"00:00"}Z`)/1000;
  return <form className="indicator-controls" onSubmit={event=>{
    event.preventDefault();
    if(start<end)onChange({start,end});
  }}>
    <label>From UTC <input type="date" className="archive-date" value={from} onChange={event=>setFrom(event.target.value)}/></label>
    <label>From time (UTC) <input type="time" value={fromTime} onChange={event=>setFromTime(event.target.value)}/></label>
    <label>Until UTC (exclusive) <input type="date" className="archive-date" value={until} onChange={event=>setUntil(event.target.value)}/></label>
    <label>Until time (UTC) <input type="time" value={untilTime} onChange={event=>setUntilTime(event.target.value)}/></label>
    <button className="small-tool" type="submit" disabled={!(start<end)}>Load saved range</button>
    {range&&<button className="small-tool" type="button" onClick={()=>onChange()}>Use recent archive</button>}
  </form>;
}
