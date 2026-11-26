import {useEffect,useState} from 'react';
import {useTimestampFormat} from './timezone';
import type {BacktestResult,BacktestRun,Timeframe} from './types';
import {frameMinutes} from './chartViews';
import {ReplayControls,visibleAt} from './ReplayControls';
import {CandleChart} from './components';
import {api} from './api';
type QuotePage={items:{time_ms:number;source_order:number;bid_units:number;ask_units:number;scale:number}[];total:number;next_offset:number|null};

export function SimulationEvidence({result,run}: {result:BacktestResult;run?:BacktestRun}) {
  const format=useTimestampFormat();
  const [selected,setSelected]=useState<number>();
  const trade=selected===undefined?undefined:result.trades[selected];
  const [replayIndex,setReplayIndex]=useState<number>();
  const step=(frameMinutes[result.timeframe as Timeframe]??5)*60;
  const replayBar=replayIndex===undefined?undefined:run?.market.candles[replayIndex];
  const edge=replayBar?(replayBar.available_at??replayBar.time+step):undefined;
  const tickExit=trade?.exit_time_ms!==undefined?Math.floor(trade.exit_time_ms/1000):trade?.exit_time;
  const ready=(time:number)=>Math.floor(time/step)*step+step;
  const entryVisible=Boolean(trade&&(edge===undefined||ready(trade.entry_time)<=edge));
  const exitVisible=edge===undefined||(tickExit!==undefined&&ready(tickExit)<=edge);
  const [offset,setOffset]=useState<number>();
  const [quotes,setQuotes]=useState<QuotePage>();
  const [quoteError,setQuoteError]=useState('');
  useEffect(()=>{
    if(!run||selected===undefined||offset===undefined)return;
    let active=true;
    setQuotes(undefined);setQuoteError('');
    api.get<QuotePage>(`/backtest/runs/${run.id}/trades/${selected}/quotes?offset=${offset}`)
      .then(page=>{if(active)setQuotes(page);}).catch(error=>{if(active)setQuoteError(error.message);});
    return ()=>{active=false;};
  },[run,selected,offset]);
  let sum=0;
  const cumulative=[0,...result.trades.map(item=>sum+=item.result_r)];
  const low=Math.min(...cumulative),high=Math.max(...cumulative);
  const range=high-low||1;
  let peak=0;
  const drawdown=cumulative.map(value=>{peak=Math.max(peak,value);return peak-value;});
  const deepest=Math.max(...drawdown);
  const drawdownPoints=drawdown.map((value,index)=>`${index*480/Math.max(1,drawdown.length-1)},${12+90*value/(deepest||1)}`).join(' ');
  const points=cumulative.map((value,index)=>`${index*480/Math.max(1,cumulative.length-1)},${102-(value-low)*90/range}`).join(' ');
  const models=result.trades.map(item=>item.result_r);
  const bins=[{label:'Below -1 R',count:models.filter(value=>value<-1).length},
    {label:'-1 R to <0 R',count:models.filter(value=>value>=-1&&value<0).length},
    {label:'0 R',count:models.filter(value=>value===0).length},
    {label:'>0 R to <2 R',count:models.filter(value=>value>0&&value<2).length},
    {label:'2 R or more',count:models.filter(value=>value>=2).length}];
  return <section className="card">
    <h2>Simulation evidence</h2>{run&&<p className="evidence-id">Run {run.id}</p>}
    <p className="muted">{result.symbol} · {result.timeframe} · {result.source} · {result.execution_model?'Bid/ask model':'OHLC estimate'}</p>
    {result.trades.length>0&&result.total_r!=null&&<figure className="simulation-curve">
      <svg viewBox="0 0 480 115" role="img" aria-label="Modelled cumulative result in R">
        <polyline points={points} stroke="#58dbc3" strokeWidth="2" fill="none" vectorEffect="non-scaling-stroke"/>
      </svg>
      <figcaption>Modelled cumulative R · {result.total_r.toFixed(2)} R across {result.trades.length} trades</figcaption>
    </figure>}
    {result.trades.length>0&&result.total_r!=null&&<figure className="simulation-curve">
      <svg viewBox="0 0 480 115" role="img" aria-label="Modelled drawdown in R">
        <polyline points={drawdownPoints} stroke="#e987a1" strokeWidth="2" fill="none" vectorEffect="non-scaling-stroke"/>
      </svg>
      <figcaption>Modelled peak-to-trough drawdown · {deepest.toFixed(2)} R</figcaption>
    </figure>}
    {models.length>0&&<details><summary>Outcome distribution (recorded fills)</summary>
      <p>{models.filter(value=>value>0).length} wins · {models.filter(value=>value<0).length} losses · {models.filter(value=>value===0).length} flat</p>
      {bins.map(bin=><p key={bin.label}>{bin.label} · {bin.count} <meter min={0} max={models.length} value={bin.count}>{bin.count}</meter></p>)}
    </details>}
    {result.execution_model&&<p>Model v{result.execution_model.version} · {result.execution_model.slippage_bps} bps slippage and {result.execution_model.commission_bps_per_side} bps commission per side · maximum quote gap {result.execution_model.max_quote_gap_ms/1000}s</p>}
    {Boolean(result.candle_gaps?.length)&&<details><summary>{result.candle_gaps?.length} archive gaps</summary>
      <ul>{result.candle_gaps?.map(gap=><li key={gap.start}>{format(gap.start*1000)} to {format(gap.end*1000)}</li>)}</ul>
    </details>}
    {result.trades.length>0&&<label className="field">Inspect a trade<select value={selected??''}
      onChange={event=>{setSelected(event.target.value===''?undefined:Number(event.target.value));setOffset(undefined);setQuotes(undefined);setQuoteError('');setReplayIndex(undefined);}}>
      <option value="">Select a sample trade</option>
      {result.trades.map((item,index)=><option key={index} value={index}>{format(item.entry_time*1000)} · {item.side} · {item.result_r.toFixed(2)} R</option>)}
    </select></label>}
    {trade&&entryVisible&&<dl className="evidence-grid">
      <div><dt>Entry time / price</dt><dd>{format(trade.entry_time*1000)} · {trade.entry}</dd></div>
      <div><dt>Stop / target</dt><dd>{trade.stop} / {trade.target}</dd></div>
      {exitVisible&&<><div><dt>Exit time / price</dt><dd>{trade.exit_time_ms!==undefined?format(trade.exit_time_ms):trade.exit_time!==undefined?format(trade.exit_time*1000):'Unavailable'} · {trade.exit??'Unavailable'}</dd></div>
      <div><dt>Exit reason / result</dt><dd>{trade.exit_reason??'OHLC estimate'} · {trade.result_r.toFixed(2)} R</dd></div></>}
    </dl>}
    {trade&&run&&<>
      <CandleChart candles={edge===undefined?run.market.candles:visibleAt(run.market.candles,edge,step)}
        symbol={result.symbol} timeframe={result.timeframe}
        precision={result.symbol.endsWith('JPY')?3:result.symbol==='XAUUSD'?3:5}
        height={310} focus={edge===undefined?{epoch:Math.floor(trade.entry_time/step)*step,sequence:selected??0}:undefined}
        levels={entryVisible?[{id:'Entry',price:trade.entry},{id:'Stop',price:trade.stop},{id:'Target',price:trade.target}]:[]}/>
      {replayIndex===undefined?<button className="small-tool" onClick={()=>{
        const first=run.market.candles.findIndex(bar=>bar.time>=Math.floor(trade.entry_time/step)*step);
        setReplayIndex(Math.max(0,first-8));setOffset(undefined);setQuotes(undefined);
      }}>Replay saved trade</button>:
        <ReplayControls candles={run.market.candles} index={replayIndex} onSeek={setReplayIndex}
          onExit={()=>setReplayIndex(undefined)} step={step}
          jumps={[{label:'Entry candle',time:ready(trade.entry_time)},
            ...(tickExit!==undefined?[{label:'Exit candle',time:ready(tickExit)}]:[])]}/>}
    </>}
    {trade&&run&&result.execution_model&&edge===undefined&&<div>
      {offset===undefined&&<button className="small-tool" onClick={()=>setOffset(0)}>Inspect bid/ask quote path</button>}
      {offset!==undefined&&!quotes&&!quoteError&&<p role="status">Loading quote path</p>}
      {quoteError&&<p role="alert">{quoteError}</p>}
      {quotes&&<><p>{quotes.total} {result.execution_model.version===2
        ? 'quotes in historical V2 order (not revalidated under V3)'
        : 'quotes in execution order'}</p><div className="table-scroll quote-path">
        <table><thead><tr><th>UTC</th><th>Source order</th><th>Bid</th><th>Ask</th></tr></thead>
        <tbody>{quotes.items.map(quote=><tr key={`${quote.time_ms}:${quote.source_order}`}>
          <td>{new Date(quote.time_ms).toISOString()}</td><td>{quote.source_order}</td>
          <td>{quote.bid_units/quote.scale}</td><td>{quote.ask_units/quote.scale}</td>
        </tr>)}</tbody></table>
        </div><div className="research-controls">
          <button className="small-tool" disabled={!offset} onClick={()=>setOffset(Math.max(0,(offset??0)-250))}>Previous quotes</button>
          <button className="small-tool" disabled={quotes.next_offset===null} onClick={()=>{if(quotes.next_offset!==null)setOffset(quotes.next_offset);}}>Next quotes</button>
        </div></>}
    </div>}
    {result.lineage&&<details><summary>Source lineage</summary>
      {[...result.lineage.candle_partitions,...(result.lineage.tick_partitions??(result.lineage.tick_partition?[result.lineage.tick_partition]:[]))].map(part=><p className="evidence-id" key={part.id}>{part.id}<br/>{part.sha256}</p>)}
    </details>}
  </section>;
}
