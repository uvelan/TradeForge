import { useEffect, useState } from "react";
import { api } from "./api";
import { CandleChart, Select } from "./components";
import type { CandleResponse, ChartSource, Timeframe } from "./types";
import { useWorkspacePreference } from "./useWorkspacePreference";

export const frameMinutes: Record<Timeframe, number> = {M1:1,M3:3,M5:5,M15:15,M30:30,H1:60,H4:240,D1:1440};

export function useViewCandles(symbol: string, frame: Timeframe, source: ChartSource, refresh: number, cutoff?: number, follow=false) {
  const key = JSON.stringify([symbol, frame, source, refresh]);
  const [result, setResult] = useState<{key: string; data?: CandleResponse; error?: string}>();
  useEffect(() => {
    if (!symbol) return;
    let active = true;
    api.candles(symbol, frame, source, 600).then(data => {
      if (active) setResult({key, data});
    }).catch(error => {
      if (active) setResult({key, error: error.message});
    });
    return () => { active = false; };
  }, [key, symbol, frame, source]);
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
          if (current?.key !== key || !current.data) return current;
          if (current.data.feed_id !== update.feed_id || current.data.price_basis !== update.price_basis) {
            return {key, data:update};
          }
          const first = update.candles[0].time;
          if (update.candles.at(-1)!.time < (current.data.candles.at(-1)?.time ?? 0)) return current;
          const candles = [...current.data.candles.filter(bar => bar.time < first), ...update.candles].slice(-600);
          return {key, data: {...update, candles}};
        });
      } catch {
        // Retain the last confirmed comparison history during a transient disconnection.
      } finally { pending = false; }
    }, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [key, symbol, frame, source, follow, replay]);
  const current = result?.key === key ? result : undefined;
  const candles = current?.data?.candles;
  return {
    candles: cutoff === undefined ? candles : candles?.filter(bar => bar.time + frameMinutes[frame] * 60 <= cutoff),
    error: current?.error,
    feed: current?.data,
  };
}

export function ChartView({number, mainSymbol, source, options, refresh, cutoff, precision}: {
  number: number; mainSymbol: string; source: ChartSource; options: string[]; refresh: number;
  cutoff?: number; precision: (symbol: string) => number;
}) {
  const [savedSymbol, setSymbol] = useWorkspacePreference<string>(`view-${number}-symbol`, "");
  const defaultFrame = number === 2 ? "H1" : number === 3 ? "M15" : "H4";
  const [frame, setFrame] = useWorkspacePreference<Timeframe>(
    number === 2 ? "secondary-timeframe" : `view-${number}-frame`,
    defaultFrame, Object.keys(frameMinutes) as Timeframe[],
  );
  const symbol = options.includes(savedSymbol) ? savedSymbol : mainSymbol;
  const {candles, error} = useViewCandles(symbol, frame, source, refresh, cutoff);
  return <section className="secondary-chart" aria-label={`Chart view ${number}`}>
    <div className="pane-label view-controls">
      <Select label={`View ${number} instrument`} value={symbol} options={options} onChange={setSymbol}/>
      <Select label={`View ${number} timeframe`} value={frame} options={Object.keys(frameMinutes)} onChange={value=>setFrame(value as Timeframe)}/>
    </div>
    {candles?.length ? <CandleChart key={`${symbol}-${frame}-${source}`} candles={candles} height={360}
      symbol={symbol} timeframe={frame} precision={precision(symbol)} showSnapshot/> :
      <div className="chart-loading" role="status">{error ?? (candles ? "No completed candles at this time" : "Loading candles…")}</div>}
  </section>;
}
