import type {ChartLink} from "./ChartLink";
import { useEffect, useMemo, useState } from "react";
import { useStableCandles } from "./candleSeriesSync";
import { visibleAt } from "./ReplayControls";
import {drawingChartProps,type DrawingBook} from './Drawings';
import { api } from "./api";
import {useChartIndicator} from "./indicators";
import { CandleChart, Select } from "./components";
import type { CandleRange, CandleResponse, ChartSource, ReplayPane, Timeframe } from "./types";
import { useWorkspacePreference } from "./useWorkspacePreference";

export const frameMinutes: Record<Timeframe, number> = {M1:1,M3:3,M5:5,M15:15,M30:30,H1:60,H4:240,D1:1440};

export function useViewCandles(symbol: string, frame: Timeframe, source: ChartSource, refresh: number, cutoff?: number, follow=false, range?:CandleRange, frozen?:ReplayPane) {
  const key = JSON.stringify([symbol, frame, source, refresh,range]);
  // A return to the same key is a new request, not proof that its old pane is current.
  const generation = useMemo(() => ({}), [key, range, frozen]);
  const [result, setResult] = useState<{generation: object; data?: CandleResponse; error?: string}>();
  useEffect(() => {
    if (!symbol||frozen) return;
    let active = true;
    api.candles(symbol, frame, source, 600,false,range).then(data => {
      if (active) setResult({generation, data});
    }).catch(error => {
      if (active) setResult({generation, error: error.message});
    });
    return () => { active = false; };
  }, [generation, symbol, frame, source,range,frozen]);
  const replay = cutoff !== undefined;
  useEffect(() => {
    if (!follow || !symbol || source !== "MT5" || replay) return;
    let active = true;
    let pending = false;
    const timer = window.setInterval(async () => {
      if (document.hidden || pending) return;
      pending = true;
      try {
        const update = await api.candles(symbol, frame, source, 10);
        if (!active || !update.candles.length) return;
        setResult(current => {
          if (current?.generation !== generation || !current.data) return current;
          if (current.data.feed_id !== update.feed_id || current.data.price_basis !== update.price_basis) {
            return {generation, data:update};
          }
          const first = update.candles[0].time;
          if (update.candles.at(-1)!.time < (current.data.candles.at(-1)?.time ?? 0)) return current;
          const candles = [...current.data.candles.filter(bar => bar.time < first), ...update.candles].slice(-600);
          return {generation, data: {...update, candles}};
        });
      } catch {
        // Retain the last confirmed comparison history during a transient disconnection.
      } finally { pending = false; }
    }, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [generation, symbol, frame, source, follow, replay]);
  const current = frozen ? ('error' in frozen ? {error:frozen.error,data:undefined} : {data:frozen,error:undefined}) :
    result?.generation === generation ? result : undefined;
  const bars = current?.data?.candles;
  const rawCandles = useMemo(()=>cutoff === undefined ? bars : bars && visibleAt(bars, cutoff, frameMinutes[frame] * 60),
    [bars,cutoff,frame]);
  const candles = useStableCandles(rawCandles);
  return {
    candles,
    error: current?.error,
    feed: current?.data,
  };
}

export function ChartView({number, mainSymbol, source, options, refresh, cutoff, precision,range,link,linkedFrame,linkedSymbol,frozen,
  drawingBook, inspecting, selectedCandle, onCandleSelect, onConfigurationChange}: {
  number: number; mainSymbol: string; source: ChartSource; options: string[]; refresh: number;
  cutoff?: number; precision: (symbol: string) => number;
  range?:CandleRange; link?:ChartLink; linkedFrame?:Timeframe; linkedSymbol?:string; frozen?:ReplayPane;
  drawingBook?:DrawingBook; inspecting?:boolean; selectedCandle?:number; onCandleSelect?:(time:number)=>void; onConfigurationChange?:()=>void;
}) {
  const [savedSymbol, setSymbol] = useWorkspacePreference<string>(`view-${number}-symbol`, "");
  const defaultFrame = number === 2 ? "H1" : number === 3 ? "M15" : "H4";
  const [savedFrame, setFrame] = useWorkspacePreference<Timeframe>(
    number === 2 ? "secondary-timeframe" : `view-${number}-frame`,
    defaultFrame, Object.keys(frameMinutes) as Timeframe[],
  );
  const frame=frozen?.timeframe??linkedFrame??savedFrame;
  const symbol = frozen?.symbol ?? linkedSymbol ?? (options.includes(savedSymbol) ? savedSymbol : mainSymbol);
  const {candles, error, feed} = useViewCandles(symbol, frame, source, refresh, cutoff,false,range,frozen);
  const [showIndicators,setShowIndicators]=useState(false);
  const closed = feed?.latest_unconfirmed && !frozen ? candles?.filter(bar=>bar.time!==feed.candles.at(-1)?.time) : candles;
  const last=closed?.at(-1);
  const asOf=cutoff??Math.min(Math.floor(Date.now()/1000),Math.max((last?.time??0)+frameMinutes[frame]*60,last?.available_at??0));
  const indicators=useChartIndicator(Boolean(last)&&source!=="BINANCE_SPOT",closed??[],source,frame,symbol,asOf,feed,Boolean(frozen),undefined,"view-"+number);
  return <section className="secondary-chart" aria-label={`Chart view ${number}`} data-inspecting={inspecting||undefined}>
    <div className="pane-label view-controls">
      {(frozen||linkedSymbol)?<span>{symbol}</span>:<Select label={`View ${number} instrument`} value={symbol} options={options} onChange={value=>{
        onConfigurationChange?.();
        setSymbol(value);
      }}/>}
      {(frozen||linkedFrame)?<span>{frame}</span>:<Select label={`View ${number} timeframe`} value={frame} options={Object.keys(frameMinutes)} onChange={value=>{
        onConfigurationChange?.();
        setFrame(value as Timeframe);
      }}/>}
    </div>
    <details onToggle={e=>setShowIndicators(e.currentTarget.open)}><summary>View {number} indicators</summary>
      {showIndicators&&indicators.controls}</details>
    {candles?.length ? <CandleChart key={`${symbol}-${frame}-${source}`} candles={candles} height={360}
      symbol={symbol} timeframe={frame} indicators={indicators.data} precision={precision(symbol)} link={link} linkId={String(number)}
      {...(drawingBook?drawingChartProps(drawingBook,precision(symbol),Boolean(inspecting)):undefined)} drawings={drawingBook?.items}
      replayCandleTime={selectedCandle} onReplayCandleSelect={onCandleSelect} showSnapshot showDataLegend/> :
      <div className="chart-loading" role="status">{error ?? (candles ? "No completed candles at this time" : "Loading candles…")}</div>}
  </section>;
}
