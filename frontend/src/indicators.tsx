import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { Candle, CandleResponse, ChartSource, Timeframe } from "./types";
import { useWorkspacePreference } from "./useWorkspacePreference";

type Window = { timezone: string; start: string; end: string };
type Definition = { id: string; label: string; period: number | null; timeframes: Timeframe[];
  parameters: { session?: Window } };
type Settings = { id: string; period?: string; session?: Window };
type Point = { time: number; value: number; pivot_time?: number; status: "confirmed" | "provisional" };
export type IndicatorResult = {
  id: string;
  render: "overlay" | "pane" | "markers";
  warmup_bars: number | null;
  unit: "price" | "percent";
  warnings: string[];
  series: Record<string, Point[]>;
};

const preference = "tradeforge.workspace.indicators-v1";
const emptyResults: IndicatorResult[] = [];
function stored(): Settings[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(preference) ?? "[]");
    if (Array.isArray(value)) return value.filter((item): item is Settings =>
      Boolean(item && typeof item.id === "string" && item.id.length <= 40)).slice(0, 8);
  } catch { /* Use the primary indicator setting when storage is unavailable. */ }
  return [];
}

export function useChartIndicator(enabled: boolean, candles: Candle[], source: ChartSource, timeframe: Timeframe,
  symbol: string, cutoff: number, feed?: CandleResponse, request?: {indicator?: string; sequence: number}) {
  const [selected, setSelected] = useWorkspacePreference<string>("indicator", "none");
  const [periodText, setPeriod] = useWorkspacePreference<string>("indicator-period", "");
  const [settings, setSettings] = useState(stored);
  const [search, setSearch] = useState("");
  const [candidate, setCandidate] = useState("");
  const [definitions, setDefinitions] = useState<Definition[]>([]);
  const [libraryError, setLibraryError] = useState("");
  const [response, setResponse] = useState<{key: string; results?: IndicatorResult[]; error?: string}>();
  useEffect(() => { if (request?.indicator) { setSelected(request.indicator); setPeriod(""); } },
    [request, setSelected, setPeriod]);
  useEffect(() => {
    try { localStorage.setItem(preference, JSON.stringify(settings)); } catch { /* Keep editing in memory. */ }
  }, [settings]);

  const primary = definitions.find(item => item.id === selected && item.period !== null);
  const active = useMemo(() => {
    const config = primary ? [{id: primary.id, period: periodText}, ...settings.filter(item => item.id !== primary.id)] : settings;
    return config.slice(0, 8).flatMap(item => {
      const definition = definitions.find(row => row.id === item.id && row.timeframes.includes(timeframe));
      if (!definition) return [];
      const period = item.period ? Number(item.period) : definition.period;
      if (period !== null && !(Number.isInteger(period) && period >= 2 && period <= 200)) return [];
      const session = item.session ?? definition.parameters.session;
      if (session && (!session.timezone || !session.start || !session.end || session.start === session.end)) return [];
      return [{id: item.id, ...(period !== null ? {period} : {}), ...(session ? {session} : {})}];
    });
  }, [definitions, periodText, primary, settings, timeframe]);
  const body = useMemo(() => enabled && active.length && candles.length
    ? JSON.stringify({source, timeframe, symbol, cutoff, feed_id: feed?.feed_id,
        dataset_id: feed?.dataset_id, partitions: feed?.partitions, price_basis: feed?.price_basis, indicators: active, candles: candles.slice(-2000)})
    : "", [active, candles, cutoff, enabled, feed?.dataset_id, feed?.feed_id, feed?.price_basis, feed?.partitions, source, symbol, timeframe]);

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
        const result = await fetch("/research/api/indicators/evaluate", {
          method: "POST", headers: {"Content-Type": "application/json"}, body, signal: controller.signal,
        });
        const data = await result.json();
        if (!result.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Invalid indicator settings");
        if (!controller.signal.aborted) setResponse({key: body, results: data.results});
      } catch (error) {
        if (!controller.signal.aborted) setResponse({key: body, error: (error as Error).message});
      }
    }, 80);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [body]);

  const current = response?.key === body ? response : undefined;
  const data = current?.results ?? emptyResults;
  const warnings = [...new Set(data.flatMap(row => row.warnings))];
  const choices = definitions.filter(item => item.timeframes.includes(timeframe) && item.id !== selected &&
    !settings.some(config => config.id === item.id) && (item.label + " " + item.id).toLowerCase().includes(search.toLowerCase()));
  function update(id: string, patch: Partial<Settings>) {
    setSettings(items => items.map(item => item.id === id ? {...item, ...patch} : item));
  }
  const controls = enabled && <div className="indicator-controls">
    <label>Indicator <select value={primary ? selected : "none"} onChange={event => {
      setSelected(event.target.value); setPeriod("");
    }}>
      <option value="none">None</option>
      {definitions.filter(item => item.period !== null).map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
    </select></label>
    {primary && <label>Period <input type="number" min={2} max={200} step={1}
      value={periodText} placeholder={String(primary.period)} onChange={event => setPeriod(event.target.value)}/></label>}
    <details className="indicator-library">
      <summary>Indicator library</summary>
      <label>Search indicators <input type="search" value={search} onChange={event => {setSearch(event.target.value); setCandidate("");}} /></label>
      <label>Add indicator <select value={candidate} onChange={event => setCandidate(event.target.value)}>
        <option value="">Choose indicator</option>
        {choices.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
      </select></label>
      <button type="button" className="small-tool" disabled={!choices.some(item => item.id === candidate) || settings.length + Number(Boolean(primary)) >= 8}
        onClick={() => { setSettings(items => [...items, {id: candidate}]); setCandidate(""); }}>Enable indicator</button>
      {settings.filter(item => item.id !== selected).map(item => {
        const definition = definitions.find(row => row.id === item.id);
        if (!definition) return null;
        const session = item.session ?? definition.parameters.session;
        return <fieldset key={item.id}>
          <legend>{definition.label}</legend>
          {definition.period !== null && <label>Period <input type="number" min={2} max={200} step={1}
            value={item.period ?? ""} placeholder={String(definition.period)} onChange={event => update(item.id,{period:event.target.value})}/></label>}
          {session && <>
            <label>Timezone <input value={session.timezone} onChange={event => update(item.id, {session:{...session, timezone:event.target.value}})}/></label>
            <label>Start <input type="time" value={session.start} onChange={event => update(item.id, {session:{...session, start:event.target.value}})}/></label>
            <label>End <input type="time" value={session.end} onChange={event => update(item.id, {session:{...session, end:event.target.value}})}/></label>
          </>}
          {!definition.timeframes.includes(timeframe) && <small>Unavailable on {timeframe}</small>}
          <button className="small-tool" type="button" onClick={() => setSettings(items => items.filter(row => row.id !== item.id))}>Disable {definition.label}</button>
        </fieldset>;
      })}
    </details>
    <small role="status">{libraryError || current?.error || (warnings.length ? warnings.map(value => value.toLowerCase().replaceAll("_", " ")).join(" · ") :
      selected === "swings" ? "Markers appear at the pivot after confirmation" : active.length ? "Closed candles · dashed session levels are developing" : "")}</small>
  </div>;
  return {data, controls};
}
