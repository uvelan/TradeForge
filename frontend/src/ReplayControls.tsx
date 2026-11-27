import {useEffect, useState} from 'react';
import type {Candle, CandleResponse} from './types';

export function closedCandles(data: CandleResponse): Candle[] {
  return data.latest_unconfirmed ? data.candles.slice(0, -1) : data.candles.slice();
}

export function visibleAt(candles: Candle[], cutoff: number, step: number): Candle[] {
  return candles.filter(bar => (bar.available_at ?? bar.time + step) <= cutoff);
}

export function ReplayControls({candles,cutoffs,index,onSeek,onExit,step,initialBookmarks,onCheckpoint,jumps=[]}: {
  candles:Candle[]; cutoffs?:number[]; index:number; onSeek:(index:number)=>void; onExit?:()=>void; step:number; initialBookmarks?:number[]; onCheckpoint?:(index:number,bookmarks:number[])=>Promise<void>;
  jumps?:{label:string;time:number}[];
}) {
  const [playing,setPlaying] = useState(false);
  const [speed,setSpeed] = useState(1);
  const [jump,setJump] = useState('');
  const [jumpError,setJumpError] = useState('');
  const [bookmarks,setBookmarks] = useState<number[]>(initialBookmarks??[]);
  const [checkpointStatus,setCheckpointStatus]=useState("");
  const [saving,setSaving]=useState(false);
  const count = cutoffs?.length ?? candles.length;
  const last = count - 1;
  const seek = (next:number) => {setPlaying(false);onSeek(Math.max(0,Math.min(last,next)));};
  useEffect(()=>{
    if (!playing || index>=last) return;
    const timer = window.setTimeout(()=>onSeek(index+1),750/speed);
    return ()=>window.clearTimeout(timer);
  },[playing,index,last,speed,onSeek]);
  useEffect(()=>{
    function shortcut(event:KeyboardEvent) {
      if (event.repeat || event.defaultPrevented || (event.target instanceof Element &&
        event.target.closest('input,textarea,select,button,[contenteditable],dialog,[role="dialog"]'))) return;
      if (event.code==='Space' && index<last) {event.preventDefault();setPlaying(value=>!value);}
      if (event.shiftKey && ['ArrowRight','ArrowLeft'].includes(event.key)) {
        event.preventDefault();
        onSeek(Math.max(0,Math.min(last,index+(event.key==='ArrowRight'?1:-1))));
        setPlaying(false);
      }
    }
    window.addEventListener('keydown',shortcut);
    return ()=>window.removeEventListener('keydown',shortcut);
  },[index,last,onSeek]);
  const canBookmark = Number.isInteger(index)&&index>=0&&index<=last;
  const cutoff = cutoffs ? cutoffs[index] : candles[index] && (candles[index].available_at ?? candles[index].time+step);
  function positionAt(time:number){
    if (!cutoffs) return visibleAt(candles,time,step).length-1;
    for (let cursor=cutoffs.length-1;cursor>=0;cursor--) if (cutoffs[cursor]<=time) return cursor;
    return -1;
  }
  return <div className="research-controls" role="region" aria-label="Historical replay">
    {onExit && <button className="small-tool" onClick={onExit}>Exit replay</button>}
    <button className="small-tool" disabled={index===0} onClick={()=>seek(0)}>Restart</button>
    <button className="small-tool" disabled={index===0} onClick={()=>seek(index-1)}>Previous candle</button>
    <button className="small-tool" disabled={index>=last} onClick={()=>setPlaying(index<last&&!playing)}>{playing&&index<last?'Pause':'Play'}</button>
    <button className="small-tool" disabled={index>=last} onClick={()=>seek(index+1)}>Next candle</button>
    <button className="small-tool" disabled={index>=last} onClick={()=>seek(last)}>Last candle</button>
    <label>Speed <select value={speed} onChange={event=>setSpeed(Number(event.target.value))}>{[0.5,1,2,5,10].map(rate=><option key={rate}>{rate}</option>)}</select></label>
    <label className="replay-seek">Replay timeline <input type="range" min={0} max={last} value={index} onChange={event=>seek(Number(event.target.value))}/></label>
    <output>{index+1} / {count} {cutoff!==undefined&&new Date(cutoff*1000).toISOString()}</output>
    {cutoffs && index===last && count>candles.length && <span>End of the requested range</span>}
    <label>Jump to UTC <input type="datetime-local" value={jump} onChange={event=>{setJump(event.target.value);setJumpError('');}}/></label>
    <button className="small-tool" disabled={!jump} onClick={()=>{
      const upto=Date.parse(jump+'Z')/1000;
      if(!Number.isFinite(upto))return;
      const position=positionAt(upto);
      if(position>=0)seek(position);
      else setJumpError('No completed candle is available at that time.');
    }}>Seek</button>
    {jumpError&&<span role="alert">{jumpError}</span>}
    {jumps.length>0&&<label>Jump to event <select value="" onChange={event=>{
      if(event.target.value){const item=jumps[Number(event.target.value)];seek(positionAt(item.time));}
    }}><option value="">Choose event</option>{jumps.map((item,itemIndex)=>{
      const position=positionAt(item.time);
      return <option key={item.label} value={itemIndex} disabled={position<0}>{item.label}</option>;
    })}</select></label>}
    {onCheckpoint&&<button className="small-tool" disabled={saving} onClick={()=>{
      setSaving(true);setCheckpointStatus('');
      void onCheckpoint(index,bookmarks).then(()=>setCheckpointStatus('Replay position saved'))
        .catch(reason=>setCheckpointStatus(reason.message)).finally(()=>setSaving(false));
    }}>Save replay position</button>}
    {checkpointStatus&&<span role="status">{checkpointStatus}</span>}
    <button className="small-tool" disabled={!canBookmark||bookmarks.includes(index)||bookmarks.length>=50}
      onClick={()=>setBookmarks(current=>!canBookmark||current.includes(index)||current.length>=50
        ?current:[...current,index].sort((a,b)=>a-b))}>Bookmark candle</button>
    {bookmarks.length>0&&<label>Bookmarks <select value="" onChange={event=>{if(event.target.value)seek(Number(event.target.value)-1);}}>
      <option value="">Choose candle</option>{bookmarks.map(value=><option key={value} value={value+1}>{new Date((cutoffs?.[value]??candles[value].time)*1000).toISOString()}</option>)}
    </select></label>}
  </div>;
}
