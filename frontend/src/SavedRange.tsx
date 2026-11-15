import {useState} from "react";
import type {CandleRange} from "./types";

export function SavedRange({onChange,range}:{onChange:(range?:CandleRange)=>void;range?:CandleRange}){
  const [from,setFrom]=useState(()=>range?new Date(range.start*1000).toISOString().slice(0,10):"");
  const [until,setUntil]=useState(()=>range?new Date(range.end*1000).toISOString().slice(0,10):"");
  const start=Date.parse(from)/1000;
  const end=Date.parse(until)/1000;
  return <form className="indicator-controls" onSubmit={event=>{
    event.preventDefault();
    if(start<end)onChange({start,end});
  }}>
    <label>From UTC <input type="date" className="archive-date" value={from} onChange={event=>setFrom(event.target.value)}/></label>
    <label>Until UTC (exclusive) <input type="date" className="archive-date" value={until} onChange={event=>setUntil(event.target.value)}/></label>
    <button className="small-tool" type="submit" disabled={!(start<end)}>Load saved range</button>
    {range&&<button className="small-tool" type="button" onClick={()=>onChange()}>Use latest partition</button>}
  </form>;
}
