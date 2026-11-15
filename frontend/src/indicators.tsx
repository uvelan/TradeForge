import { useEffect, useState } from "react";
import { api } from "./api";
import type { Candle, ChartSource, Timeframe } from "./types";
import { useWorkspacePreference } from "./useWorkspacePreference";

type Definition = { id: string; label: string; period: number };
export type IndicatorResult = {
  id: string;
  render: "overlay" | "pane" | "markers";
  warmup_bars: number;
  series: Record<string, { time: number; value: number; pivot_time?: number }[]>;
};

export function useChartIndicator(enabled: boolean, candles: Candle[], source: ChartSource, timeframe: Timeframe, request?:{indicator?:string;sequence:number}) {
  const [selected, setSelected] = useWorkspacePreference<string>("indicator", "none");
  const [periodText, setPeriod] = useWorkspacePreference<string>("indicator-period", "");
  useEffect(()=>{if(request?.indicator){setSelected(request.indicator);setPeriod('');}},[request,setSelected,setPeriod]);
  const [definitions, setDefinitions] = useState<Definition[]>([]);
  const [libraryError, setLibraryError] = useState("");
  const [result, setResult] = useState<{ key: string; data: IndicatorResult }>();
  const [failure, setFailure] = useState<{ key: string; message: string }>();
  const definition = definitions.find(item => item.id === selected);
  const period = periodText ? Number(periodText) : definition?.period;
  const validPeriod = period !== undefined && Number.isInteger(period) && period >= 2 && period <= 200;
  // Use candle content as the request identity. Polling commonly returns the same closed bars.
  const body = enabled && definition && validPeriod && candles.length
    ? JSON.stringify({ indicator: selected, period, source, timeframe, candles })
    : "";

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    api.get<Definition[]>("/indicators").then(items => {
      if (active) { setDefinitions(items); setLibraryError(""); }
    }).catch(error => {
      if (active) setLibraryError(error.message);
    });
    return () => { active = false; };
  }, [enabled]);

  useEffect(() => {
    if (!body) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch("/research/api/indicators/calculate", {
          method: "POST", headers: { "Content-Type": "application/json" }, body, signal: controller.signal,
        });
        if (!response.ok) throw new Error("Indicator calculation failed");
        const data = await response.json() as IndicatorResult;
        if (!controller.signal.aborted) { setResult({ key: body, data }); setFailure(undefined); }
      } catch (error) {
        if (!controller.signal.aborted) setFailure({ key: body, message: (error as Error).message });
      }
    }, 80);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [body]);

  const data = body && result?.key === body ? result.data : undefined;
  const controls = enabled && <div className="indicator-controls">
    <label>Indicator <select value={definition ? selected : "none"} onChange={event => {
      setSelected(event.target.value);
      setPeriod("");
    }}>
      <option value="none">None</option>
      {definitions.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
    </select></label>
    {definition && <label>Period <input type="number" min={2} max={200} step={1}
      value={periodText} placeholder={String(definition.period)} onChange={event => setPeriod(event.target.value)} /></label>}
    <small role="status">{libraryError || (definition && !validPeriod ? "Enter a whole period from 2 to 200" :
      failure?.key === body ? failure.message :
      data && !Object.values(data.series).some(points => points.length) && data.render !== "markers" ? `Needs ${data.warmup_bars} candles` :
      selected === "swings" ? "Markers appear at the pivot after confirmation" : definition ? "Closed candles" : "")}</small>
  </div>;
  return { data, controls };
}
