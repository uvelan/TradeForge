"""Historical source inventory for the November 2025 evidence bundle.

MT5 counts describe the raw epoch domain. The application's month_mt5_audit
performs the separate feed and clock admission checks for monthly replay.
"""
import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from research_backend.app.indicator_coverage import MarketSchedule, open_intervals
from research_backend.app.storage import decode_bi5
from scripts.benchmark_t06 import PINS


START = int(datetime(2025, 11, 1, tzinfo=UTC).timestamp())
END = int(datetime(2025, 12, 1, tzinfo=UTC).timestamp())
HOURS = range(START, END, 3600)


def ranges(hours):
    runs = []
    for start in sorted(hours):
        if runs and runs[-1][1] == start:
            runs[-1][1] += 3600
        else:
            runs.append([start, start+3600])
    return runs


def summarize(samples, opened):
    stamps = np.concatenate(samples) if samples else np.array([], dtype=np.int64)
    if np.any(stamps[1:] < stamps[:-1]) or np.any(stamps < START*1000) or np.any(stamps >= END*1000):
        raise ValueError("Source observations are out of order or outside the month")
    hour_starts = stamps // 3600000 * 3600
    unique, counts = np.unique(hour_starts, return_counts=True)
    observed = set(map(int, unique))
    missing = opened - observed
    # Keep calendar labels separate from actual measured silences on available source hours.
    silences = []
    for first, end in ranges(observed):
        block = stamps[(stamps >= first*1000) & (stamps < end*1000)]
        if block[0] > first*1000:
            silences.append([first*1000, int(block[0])])
        for index in np.flatnonzero(np.diff(block) > 60000):
            silences.append([int(block[index])+1, int(block[index+1])])
        if end*1000-1-block[-1] > 60000:
            silences.append([int(block[-1])+1, end*1000])
    return dict(rows=len(stamps), first_ms=int(stamps[0]) if len(stamps) else None,
                last_ms=int(stamps[-1]) if len(stamps) else None, observed_hours=len(observed),
                observed_open_hours=len(observed & opened), missing_open_hours=len(missing),
                missing_open_ranges=ranges(missing), observations_in_closed_ranges=ranges(observed-opened),
                rows_in_closed_hours=sum(int(count) for slot, count in zip(unique, counts) if int(slot) not in opened),
                hourly_rows=[[int(slot), int(count)] for slot, count in zip(unique, counts)],
                silences_in_observed_ranges_ms=silences)


def report(directory):
    sessions = open_intervals(START, END, MarketSchedule(kind="forex_weekly"))
    opened = {hour for hour in HOURS if any(a <= hour < b for a,b in sessions)}
    capture = json.loads((directory / "mt5/capture.json").read_text(encoding="utf-8"))
    mt_samples, bi5_samples, pins = [], [], []
    bad_bid_ask = inconsistent_seconds = 0
    for i, record in enumerate(capture["days"]):
        if (record["start"],record["end"]) != (START+i*86400, START+(i+1)*86400):
            raise ValueError("Noncontiguous MT5 day manifest")
        if "sha256" not in record:
            continue
        path = directory / "mt5" / (record["day"] + ".npy")
        rows = np.load(path, allow_pickle=False)
        if sha256(path.read_bytes()).hexdigest() != record["sha256"] or len(rows) != record["rows"]:
            raise ValueError(f"MT5 day checksum or count differs for {record['day']}")
        ms = rows["time_msc"]
        if np.any(ms < record["start"]*1000) or np.any(ms >= record["end"]*1000):
            raise ValueError("MT5 tick outside declared day")
        inconsistent_seconds += int(np.sum(rows["time"] != ms//1000))
        valid = (np.isfinite(rows["bid"]) & np.isfinite(rows["ask"]) & (rows["bid"] > 0) &
                 (rows["ask"] >= rows["bid"]))
        bad_bid_ask += int(np.sum(~valid))
        mt_samples.append(ms)
    if len(capture["days"]) != 30:
        raise ValueError("Missing MT5 day response")
    binding = json.loads((directory / "final-inventory.json").read_text(encoding="utf-8-sig"))
    registered = {h["start"]:h["pin"] for h in binding["hours"] if h["pin"]}
    for path in sorted((directory / "raw").glob("*/*h_ticks.bi5")):
        day = datetime.fromisoformat(path.parent.name).replace(hour=int(path.name[:2]), tzinfo=UTC)
        raw = path.read_bytes()
        digest = sha256(raw).hexdigest()
        if day.day == 13 and day.hour in PINS and PINS[day.hour] != digest:
            raise ValueError("Control checksum mismatch")
        quotes = decode_bi5(raw, "EURUSD", day, day.hour)
        ms = np.array(sorted(q["time_ms"] for q in quotes), dtype=np.int64)
        catalog_pin = registered.pop(int(day.timestamp()))
        if catalog_pin["source_sha256"] != digest:
            raise ValueError("Raw file differs from frozen monthly catalog")
        pins.append(dict(start=int(day.timestamp()), sha256=digest, partition=catalog_pin["partition"], bytes=len(raw), rows=len(ms),
                         first_ms=int(ms[0]), last_ms=int(ms[-1])))
        bi5_samples.append(ms)
    if registered:
        raise ValueError("Missing raw files for registered BI5 hours")
    return {"symbol":"EURUSD", "range":[START,END],
            "range_encoding":"Half-open epoch seconds; suffix _ms denotes epoch milliseconds.",
            "calendar":"Sunday 17:00 to Friday 17:00 America/New_York, no holiday overrides. MT5 raw-domain comparison only.",
            "open_hours":len(opened), "closed_hours":len(HOURS)-len(opened),
            "scheduled_closed_ranges":ranges(set(HOURS)-opened),
            "silence_rule":"Within runs of observed hours, record the prefix before the first quote, gaps between adjacent quote milliseconds more than 60000 ms apart, and trailing silence over 60000 ms. Intervals begin after the last observed millisecond.",
            "dukascopy":{"feed":"dukascopy-bi5-v2", "clock":"UTC hour + BI5 millisecond offset; file ordinal breaks ties.",
                         "price_basis":"BID and ASK integers at scale 100000",
                         "delivery":"Zero modeled delivery delay. The source exposes no historical arrival or fill clock.",
                         "files":pins, **summarize(bi5_samples, opened)},
            "mt5":{"feed":"MT5_COPY_TICKS_ALL", "server_hash":capture["server_hash"], "digits":capture["digits"],
                   "clock":"Raw time_msc rendered as epoch milliseconds; historical mapping to UTC unverified. Sunday openings and Friday observations conflict with the stated UTC schedule.",
                   "delivery":"Native response contains time, time_msc, bid, ask, last, volume, flags and volume_real. No broker arrival or execution timestamp.",
                   "utc_coverage_status":"UNVERIFIED_TIMESTAMP_DOMAIN",
                   "utc_open_ranges_without_verified_quotes":ranges(opened),
                   "price_basis":"Native BID/ASK; neither trades nor derived candles used.",
                   "inconsistent_time_seconds":inconsistent_seconds,"invalid_bid_ask_rows":bad_bid_ask,
                   "capture_manifest_sha256":sha256((directory / "mt5/capture.json").read_bytes()).hexdigest(),
                   "raw_epoch_comparison":summarize(mt_samples,opened)}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(exist_ok=True, parents=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(report(args.directory), stream, separators=(",", ":"))
        stream.write("\n")
