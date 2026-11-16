import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { Candle, CandleResponse, ChartSource, Timeframe } from "./types";
import { useWorkspacePreference } from "./useWorkspacePreference";

type Window = { timezone: string; start: string; end: string };
type Schedule = {kind: "continuous" | "forex_weekly" | "unknown"; timezone?: string;
  sunday_open?: string; friday_close?: string};
type Definition = { id: string; label: string; period: number | null; timeframes: Timeframe[];
  parameters: { session?: Window; schedule?: Schedule["kind"][] } };
type Settings = { id: string; period?: string; session?: Window; schedules?: Record<string, Schedule> };
type Point = { time: number; value: number; pivot_time?: number; status: "confirmed" | "provisional" };
export type IndicatorResult = {
  id: string;
  render: "overlay" | "pane" | "markers";
  warmup_bars: number | null;
  unit: "price" | "percent";
  warnings: string[];
  coverage?: {status: string; confirmation_status: "confirmed" | "provisional" | "unavailable"};
  series: Record<string, Point[]>;
};

const coverageLabels: Record<string, string> = {
  COMPLETE: "Confirmed previous range",
  UNKNOWN_SCHEDULE: "Unknown trading schedule",
  SOURCE_INTEGRITY_FAILURE: "Source integrity failure",
  SOURCE_MISMATCH: "Source identity mismatch",
  MISSING_OPEN_CANDLES: "Insufficient history: expected market-open candles are missing",
  REFERENCE_ABSENT: "Insufficient history: reference period absent",
  MARKET_CLOSED: "Market closed throughout reference period",
  HISTORY_UNAVAILABLE: "Historical context unavailable",
  HISTORY_LIMIT: "Historical context exceeds read limits",
  UNALIGNED_MARKET_BOUNDARY: "Timeframe crosses a market boundary",
  SCHEDULE_MISMATCH: "Observed bars conflict with the selected schedule",
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
  symbol: string, cutoff: number, feed?: CandleResponse, frozen = false, request?: {indicator?: string; sequence: number}) {
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

  const scheduleScope = JSON.stringify([source, symbol, feed?.feed_id, feed?.price_basis]);
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
      const schedule = item.schedules?.[scheduleScope];
      return [{id: item.id, ...(period !== null ? {period} : {}), ...(session ? {session} : {}), ...(schedule ? {schedule} : {})}];
    });
  }, [definitions, periodText, primary, scheduleScope, settings, timeframe]);
  const body = useMemo(() => enabled && active.length && (candles.length || active.some(item => item.id.startsWith("previous_")))
    ? JSON.stringify({source, timeframe, symbol, cutoff, frozen, load_reference: true, feed_id: feed?.feed_id,
        dataset_id: feed?.dataset_id, partitions: feed?.partitions, price_basis: feed?.price_basis, indicators: active, candles: candles.slice(-2000)})
    : "", [active, candles, cutoff, enabled, frozen, feed?.dataset_id, feed?.feed_id, feed?.price_basis, feed?.partitions, source, symbol, timeframe]);

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
  const warnings = [...new Set(data.filter(row => !row.coverage).flatMap(row => row.warnings))];
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
        const schedule: Schedule = item.schedules?.[scheduleScope] ?? {kind: source === "DEMO" || source === "BINANCE_SPOT" ? "continuous" : "unknown"};
        const updateSchedule = (patch: Partial<Schedule>) => update(item.id,
          {schedules:{...item.schedules,[scheduleScope]:{...schedule,...patch}}});
        return <fieldset key={item.id}>
          <legend>{definition.label}</legend>
          {definition.period !== null && <label>Period <input type="number" min={2} max={200} step={1}
            value={item.period ?? ""} placeholder={String(definition.period)} onChange={event => update(item.id,{period:event.target.value})}/></label>}
          {session && <>
            <label>Timezone <input value={session.timezone} onChange={event => update(item.id, {session:{...session, timezone:event.target.value}})}/></label>
            <label>Start <input type="time" value={session.start} onChange={event => update(item.id, {session:{...session, start:event.target.value}})}/></label>
            <label>End <input type="time" value={session.end} onChange={event => update(item.id, {session:{...session, end:event.target.value}})}/></label>
          </>}
          {definition.parameters.schedule && <>
            <label>Market schedule <select value={schedule.kind}
              onChange={event => updateSchedule({kind:event.target.value as Schedule["kind"]})}>
              <option value="unknown">Unknown</option>
              <option value="continuous">Continuous (24/7)</option>
              <option value="forex_weekly">Configured Sunday–Friday</option>
            </select></label>
            {schedule.kind === "forex_weekly" && <>
              <label>Market timezone <input value={schedule.timezone ?? "America/New_York"}
                onChange={event => updateSchedule({timezone:event.target.value})} /></label>
              <label>Sunday open <input type="time" value={schedule.sunday_open ?? "17:00"}
                onChange={event => updateSchedule({sunday_open:event.target.value})} /></label>
              <label>Friday close <input type="time" value={schedule.friday_close ?? "17:00"}
                onChange={event => updateSchedule({friday_close:event.target.value})} /></label>
            </>}
          </>}
          {!definition.timeframes.includes(timeframe) && <small>Unavailable on {timeframe}</small>}
          <button className="small-tool" type="button" onClick={() => setSettings(items => items.filter(row => row.id !== item.id))}>Disable {definition.label}</button>
        </fieldset>;
      })}
    </details>
    {data.flatMap(row => {
      if (!row.coverage) return [];
      const label = definitions.find(item => item.id === row.id)?.label ?? row.id;
      const status = coverageLabels[row.coverage.status] ?? row.coverage.status;
      return <small key={row.id} role="status">{label} · {status}
        {row.coverage.confirmation_status === "provisional" && " · provisional evidence"}</small>;
    })}
    <small role="status">{libraryError || current?.error || (warnings.length ? warnings.map(value => value.toLowerCase().replaceAll("_", " ")).join(" · ") :
      selected === "swings" ? "Markers appear at the pivot after confirmation" : active.length ? "Closed candles · dashed session levels are developing" : "")}</small>
  </div>;
  return {data, controls};
}
