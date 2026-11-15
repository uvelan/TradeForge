import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { TickMarkType, type Time } from "lightweight-charts";
import { useWorkspacePreference } from "./useWorkspacePreference";

const zones = new Set([...Intl.supportedValuesOf("timeZone"), "UTC", "Asia/Kolkata"]);
const choices = ["auto", ...Array.from(zones).sort()];
const ZoneContext = createContext({ zone: "Asia/Kolkata", preference: "Asia/Kolkata", setZone: (_value: string) => {} });

export function TimezoneProvider({ children }: { children: ReactNode }) {
  const [preference, setZone] = useWorkspacePreference("timezone", "Asia/Kolkata", choices);
  const zone = preference === "auto" ? Intl.DateTimeFormat().resolvedOptions().timeZone : preference;
  return <ZoneContext.Provider value={{ zone, preference, setZone }}>{children}</ZoneContext.Provider>;
}

export const useTimezone = () => useContext(ZoneContext);

export function TimezonePicker() {
  const { zone, preference, setZone } = useTimezone();
  const [query, setQuery] = useState("");
  const matches = choices.filter(value => value === preference || (value === "auto" ? "system timezone" : value.replaceAll("_", " ")).toLowerCase().includes(query.trim().toLowerCase()));
  return <div className="timezone-picker">
    <label className="field"><span>Search timezones</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} /></label>
    <label className="field"><span>Display timezone</span><select value={preference} onChange={event => setZone(event.target.value)}>
      {matches.map(value => <option key={value} value={value}>{value === "auto" ? "System timezone" : value.replaceAll("_", " ")}</option>)}
    </select></label>
    <small>{new Intl.DateTimeFormat("en-IN", {timeZone: zone, timeStyle: "short"}).format(new Date())} · {zone}</small>
  </div>;
}

export function useTimestampFormat() {
  const { zone } = useTimezone();
  return useMemo(() => {
    const formatter = new Intl.DateTimeFormat("en-IN", {timeZone: zone, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
    return (value: string | number) => {
      const date = new Date(value);
      return Number.isNaN(date.valueOf()) ? String(value) : formatter.format(date);
    };
  }, [zone]);
}

export function chartTimeOptions(zone: string) {
  const format = (options: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-GB", {timeZone: zone, ...options});
  const time = format({hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
  const seconds = format({hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"});
  const day = format({day: "numeric", month: "short"});
  const full = format({dateStyle: "medium", timeStyle: "short", hourCycle: "h23"});
  return {
    tick: (value: Time, type: TickMarkType) => typeof value !== "number" ? null :
      (type === TickMarkType.Time ? time : type === TickMarkType.TimeWithSeconds ? seconds : day).format(value * 1000),
    crosshair: (value: Time) => typeof value === "number" ? full.format(value * 1000) : String(value),
  };
}
