"""Verify local licensed web exports and record market-data-derived checks without prices."""
import argparse
import csv
from datetime import UTC, date, datetime, time
from hashlib import sha256
import json
from math import isclose
from pathlib import Path
from statistics import median
from time import perf_counter

from fastapi.testclient import TestClient

from research_backend.app.arrival_policy import SIMULATION_AUTHORITY, SIMULATION_MODE
from research_backend.app.db import one, rows
from research_backend.app.historical_csv import CSV_FEED
from research_backend.app.indicator_coverage import MarketSchedule, open_intervals
from research_backend.app.main import app
from research_backend.app.storage import iter_complete_partition


SYMBOLS = ("EURUSD", "GBPUSD")
DAY = 86400
WEEKLY = {"kind":"forex_weekly"}
SCENARIOS = ((0,0),(2,2),(2,15),(15,30))
PRICE_KEYS = ("open","high","low","close")
EVAL = "/api/indicators/evaluate"
SMT = "/api/indicators/smt/strict"
READY = "/api/market/arrival/readiness"


def instant(day):
    return int(datetime.combine(day,time(),UTC).timestamp())


def fingerprint(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def market_slots(start,end,step):
    intervals = open_intervals(start,end,MarketSchedule(**WEEKLY))
    return {stamp for left,right in intervals for stamp in range(left,right,step)}


def get_data(manifests, frame):
    partitions, bars = [], {}
    for item in manifests:
        selected = [part for part in item["candle_datasets"] if part["timeframe"]==frame]
        for part in selected:
            row = one("SELECT * FROM datasets WHERE id=?", (part["id"],))
            assert row and row["sha256"] == part["sha256"]
            partitions.append({key:part[key] for key in ("id","sha256")})
            for bar in iter_complete_partition(row,max_rows=2000):
                assert bar["time"] not in bars
                bars[bar["time"]] = bar
    return partitions,bars


def scope(symbol, frame, parts, start, end, delay=0):
    return dict(symbol=symbol,timeframe=frame,source="DUKASCOPY_SAVED",feed_id=CSV_FEED["BID"],
                price_basis="BID",calendar=WEEKLY,partitions=parts,start=start,end=end,delay_seconds=delay)


def coverage(bars, start, end, step):
    expected = market_slots(start,end,step)
    actual = set(bars)
    return {"expected":len(expected),"observed":len(actual & expected),"missing_utc_epochs":sorted(expected-actual),
            "outside_schedule":sorted(actual-expected),"scheduled_closed_slots":(end-start)//step-len(expected),
            "coverage_pct":round(100*len(actual & expected)/len(expected),4) if expected else None}


def post(client, route, request):
    r = client.post(route,json=request)
    if not r.is_success:
        raise RuntimeError(f"{route} {r.status_code}: {r.text[:1200]}")
    return r.json()


def indicator_request(feed, cutoff):
    settings = {"market_schedule":WEEKLY,"history_start":feed["start"],"history_bars":10000}
    structure = {**settings,"algorithm_version":2}
    options = [{"id":"market_structure","structure":structure},
               {"id":"liquidity_sweeps","structure":structure},
               {"id":"fair_value_gap","fvg":settings},
               {"id":"order_blocks","structure":structure}]
    if feed["timeframe"] == "M5":
        options.append({"id":"candle_range_theory","crt":settings})
    return {**{k:v for k,v in feed.items() if k not in ("start","end")},
            "cutoff":cutoff,"availability_start":feed["start"],"availability_mode":SIMULATION_MODE,
            "frozen":True,"load_reference":True,"candles":[],"indicators":options}


def export_quotes(path):
    with path.open(encoding="utf-8-sig",newline="") as handle:
        return [(int(datetime.fromisoformat(row["Etc/UTC"]).timestamp()),
                 tuple(float(row[name.title()]) for name in PRICE_KEYS))
                for row in csv.DictReader(handle)]


def independent_bucket(quotes, step):
    bins = {}
    for stamp, prices in quotes:
        bins.setdefault(stamp//step*step, []).append(prices)
    return {stamp:(values[0][0],max(item[1] for item in values),min(item[2] for item in values),values[-1][3])
            for stamp,values in bins.items()}


def numerical_market(source_dir, symbol, day, stored_m5):
    stem = f"{symbol}_{day}"
    minute_path = source_dir / f"{stem}_M1_BID.csv"
    m5_path = source_dir / f"{stem}_M5_BID.csv"
    tick_path = source_dir / f"{stem}_T12_BID.csv"
    m1 = export_quotes(minute_path)
    native_m5 = dict(export_quotes(m5_path))
    tick = export_quotes(tick_path)
    assert all(a[0] <= b[0] for a,b in zip(tick,tick[1:]))
    assert all(len(set(value)) == 1 for _,value in tick)
    independent_m5 = independent_bucket(m1,300)
    tick_m5 = independent_bucket(tick,300)
    assert native_m5 == independent_m5 == {k:tuple(v[key] for key in PRICE_KEYS) for k,v in stored_m5.items()}
    assert tick_m5 and all(value == native_m5[key] for key,value in tick_m5.items())
    return {"independent_m5_bars":len(native_m5),"direct_tick_m5_bars":len(tick_m5),
            "direct_bid_tick_rows":len(tick),"mismatched_ohlc":0,
            "first_tick_epoch":tick[0][0],"last_tick_epoch":tick[-1][0],
            "same_second_additional_rows":len(tick)-len({stamp for stamp,_ in tick}),
            "provider_m5_sha256":sha256(m5_path.read_bytes()).hexdigest(),
            "provider_tick_csv_sha256":sha256(tick_path.read_bytes()).hexdigest(),
            "tick_resolution":"seconds and provider file row order, BID only",
            "bid_ask_spread":"unavailable from independently exported OHLC/one-side tick files"},independent_bucket(m1,3600)


def confirm_pivot(bars, stamp, side, width, step):
    others = [bars[stamp+offset*step][side] for offset in range(-width,width+1) if offset]
    price = bars[stamp][side]
    assert price > max(others) if side=="high" else price < min(others)
    return price


def numerical_indicators(result, bars, independent_h1, delay):
    found = {item["id"]:item for item in result["results"]}
    fvg = found["fair_value_gap"]["formations"][0]
    a,b,c = (bars[fvg[axis+"_time"]] for axis in "abc")
    assert b["time"] == a["time"]+300 and c["time"] == b["time"]+300
    expected = (a["high"],c["low"]) if fvg["direction"]=="BULLISH" else (c["high"],a["low"])
    assert expected == (fvg["lower"],fvg["upper"]) and expected[0]<expected[1]
    assert fvg["formation_available_at"] == c["time"]+300+delay
    structure = found["market_structure"]
    pivot = structure["pivots"][0]
    assert confirm_pivot(bars,pivot["pivot_time"],pivot["side"].lower(),3,300) == pivot["price"]
    assert pivot["available_at"] == pivot["pivot_time"]+4*300+delay
    broken = next((entry for entry in structure["breaks"] if entry["status"]=="confirmed"),None)
    if broken:
        breach = bars[broken["break_time"]]["close"]
        assert breach == broken["break_price"]
        assert breach > broken["level_price"] if broken["direction"]=="BULLISH" else breach < broken["level_price"]
        assert broken["available_at"] == broken["break_time"]+300+delay
    formations = found["candle_range_theory"]["crt_formations"]
    for entry in formations:
        expected_h1 = independent_h1[entry["reference_start"]]
        assert tuple(entry["reference_ohlc"]) == expected_h1
        assert isclose(entry["midpoint"],(expected_h1[1]+expected_h1[2])/2,abs_tol=1e-12)
        assert entry["reference_available_at"] == entry["reference_end"]+delay
    assert formations
    return {"fvg_bounds_and_availability":"PASS","fvg_c_open":c["time"],
            "swing_extreme_and_confirmation":"PASS","swing_open":pivot["pivot_time"],
            "structural_break":"PASS" if broken else "NOT_OBSERVED",
            "structural_break_open":broken["break_time"] if broken else None,
            "h1_ohlc_midpoint_and_availability_matches":len(formations)}


def numerical_smt(result, bars, step, delays):
    verified = 0
    for event in result["events"]:
        assert not event["historically_eligible"] and not event["current_eligible"]
        extrema = "high" if event["direction"]=="bearish" else "low"
        slopes = []
        availability = []
        for (name,pair), symbol, delay in zip(sorted(event["pivots"].items(),reverse=True),SYMBOLS,delays,strict=True):
            assert name == ("primary" if symbol==SYMBOLS[0] else "comparison")
            values = []
            for role in ("previous","current"):
                pivot = pair[role]
                observed = confirm_pivot(bars[symbol],pivot["time"],extrema,3,step)
                assert observed == pivot["price"]
                assert pivot["nominal_confirmation"] == pivot["time"]+4*step
                assert pivot["available_at"] == pivot["nominal_confirmation"]+delay
                availability.append(pivot["available_at"])
                values.append(observed)
            sign = 1 if extrema=="high" else -1
            slopes.append(sign*(values[1]-values[0])/values[0]*10_000)
        for role in ("previous","current"):
            assert abs(event["pivots"]["primary"][role]["time"] -
                       event["pivots"]["comparison"][role]["time"]) <= result["alignment_bars"]*step
        winner = int(event["leader"]=="comparison")
        assert slopes[winner] > result["tolerance_bps"] and slopes[1-winner] < -result["tolerance_bps"]
        assert max(availability) <= event["candidate_at"]
        if event["confirmed_at"] is not None:
            first_shared = (event["candidate_at"]+step-1)//step*step
            closing = first_shared+result["confirmation_bars"]*step
            assert event["confirmed_at"] == closing+max(delays) <= result["cutoff"]
        verified += 1
    return verified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("day",type=date.fromisoformat)
    parser.add_argument("--sources",type=Path,required=True)
    parser.add_argument("--public",type=Path,required=True)
    parser.add_argument("--private",type=Path,required=True)
    args = parser.parse_args()
    args.public.mkdir(parents=True,exist_ok=True)
    args.private.mkdir(parents=True,exist_ok=True)
    registered = [json.loads(row["manifest"]) for row in rows("SELECT manifest FROM historical_imports ORDER BY symbol,source_start,feed_id")]
    quality = []
    for entry in registered:
        if entry["feed_id"] not in CSV_FEED.values():
            continue
        meta = entry["source"]
        local_source = args.sources / (meta["symbol"]+"_"+meta["day"]+"_M1_"+meta["price_basis"]+".csv")
        if sha256(local_source.read_bytes()).hexdigest() != meta["sha256"]:
            raise ValueError("Original provider export checksum differs from the registered source")
        frames = {}
        for frame,step in (("M1",60),("M5",300)):
            parts,bars = get_data([entry],frame)
            frames[frame] = {"partitions":parts,**coverage(bars,meta["start"],meta["end"],step)}
        quality.append({"source":meta,"feed_id":entry["feed_id"],"aggregation_policy":entry["aggregation_policy"],
                        "counts":entry["quality"],"coverage":frames})
    (args.public/"data-quality.json").write_text(json.dumps(quality,indent=2)+"\n",encoding="utf-8")
    start = instant(args.day)
    report_end = start+DAY
    end = max(right for _,right in open_intervals(start,report_end,MarketSchedule(**WEEKLY)))
    selected = {symbol:[entry for entry in registered if entry["feed_id"]==CSV_FEED["BID"]
                        and entry["source"]["symbol"]==symbol and entry["source"]["start"]==start] for symbol in SYMBOLS}
    requests, summaries, numbers = {}, {}, {}
    references = {}
    client = TestClient(app)
    try:
        for frame,step in (("M5",300),("M1",60)):
            archives = {symbol:get_data(selected[symbol],frame) for symbol in SYMBOLS}
            bars = {symbol:values for symbol,(_,values) in archives.items()}
            for symbol in SYMBOLS:
                assert set(bars[symbol]) == market_slots(start,end,step)
                if frame=="M5":
                    numbers[symbol],references[symbol] = numerical_market(args.sources,symbol,args.day,bars[symbol])
            identifiers = set()
            for i,delays in enumerate(SCENARIOS):
                feeds = [scope(symbol,frame,archives[symbol][0],start,end,delay)
                         for symbol,delay in zip(SYMBOLS,delays,strict=True)]
                cutoff = end + max(delays)
                pair = {"primary":feeds[0],"comparison":feeds[1],"cutoff":cutoff}
                readiness = post(client,READY,pair)
                assert SIMULATION_MODE in readiness["joint_modes"]
                body = {**pair,"availability_mode":SIMULATION_MODE,"limit":100}
                key = f"{frame}-S{i}"
                answer = post(client,SMT,body)
                repeat = post(client,SMT,body)
                assert (answer["calculation_id"],answer["snapshot"],answer["events"]) == (
                    repeat["calculation_id"],repeat["snapshot"],repeat["events"])
                identifiers.add(answer["calculation_id"])
                assert not answer["alerts"]
                paged,ids = {**body,"limit":7},[]
                while True:
                    page = post(client,SMT,paged)
                    assert page["snapshot"] == answer["snapshot"]
                    ids[0:0] = [event["id"] for event in page["events"]]
                    if not page["delivery"]["next_cursor"]:
                        break
                    paged["cursor"] = page["delivery"]["next_cursor"]
                assert (ids[-len(answer["events"]):] if answer["events"] else []) == [event["id"] for event in answer["events"]]
                rewind_body = {**body,"cutoff":end-1800+max(delays)}
                rewind = post(client,SMT,rewind_body)
                assert all(rev["available_at"]<=rewind["cutoff"] for event in rewind["events"] for rev in event["revisions"])
                assert post(client,SMT,body)["snapshot"] == answer["snapshot"]
                verified_pivots = numerical_smt(answer,bars,step,delays)
                recorded = post(client,SMT,{**body,"availability_mode":"STRICT_RECORDED"})
                assert not recorded["events"] and recorded["reason"] == "UNVERIFIED_AVAILABILITY"
                artifact = {"request":body,"indicator_requests":[],"rewind":rewind_body,"crt_early":[]}
                detail = {"smt":answer,"readiness":readiness,"rewind":rewind,"strict_recorded":recorded,"indicators":[]}
                item = {"calculation_id":answer["calculation_id"],"snapshot":answer["snapshot"],
                        "response_fingerprint":fingerprint({k:v for k,v in answer.items() if k!="resources"}),
                        "joint_ready_modes":readiness["joint_modes"],
                        "bar_coverage":{side:value["coverage"] for side,value in answer["sources"].items()},
                        "smt_events":len(ids),"verified_pivot_pair_events":verified_pivots,"status":answer["status"],
                        "reason":answer["reason"],"simulation_eligible":sum(e["historically_simulation_eligible"] for e in answer["events"]),
                        "recorded_result":recorded["reason"],"paginated_snapshot_match":True,"replay_rewind_and_advance":"PASS",
                        "indicator_fingerprints":[],"indicators":[]}
                for symbol,feed in zip(SYMBOLS,feeds,strict=True):
                    request = indicator_request(feed,cutoff)
                    result = post(client,EVAL,request)
                    indicator_results = result["results"]
                    assert all(row["provenance"]["context_authority"]==SIMULATION_AUTHORITY for row in indicator_results)
                    for row in indicator_results:
                        for values in row.values():
                            if isinstance(values,list):
                                for event in values:
                                    if isinstance(event,dict) and event.get("available_at") is not None:
                                        assert event["available_at"] <= cutoff
                    assert fingerprint(indicator_results) == fingerprint(post(client,EVAL,request)["results"])
                    if frame=="M5":
                        checked = numerical_indicators(result,bars[symbol],references[symbol],feed["delay_seconds"])
                        early_request = {**request,"cutoff":start+3599,"indicators":[request["indicators"][-1]]}
                        early = post(client,EVAL,early_request)["results"][0]
                        assert not early["crt_formations"]
                        checked["no_h1_before_first_close"] = "PASS"
                        numbers[symbol][f"scenario_{i}"] = checked
                        artifact["crt_early"].append(early_request)
                    artifact["indicator_requests"].append(request)
                    detail["indicators"].append(result)
                    item["indicator_fingerprints"].append(fingerprint(indicator_results))
                    item["indicators"].append([row["id"] for row in indicator_results])
                requests[key] = artifact
                summaries[key] = item
                (args.private/f"{key}.json").write_text(json.dumps(detail,indent=2)+"\n",encoding="utf-8")
            assert len(identifiers)==len(SCENARIOS)
    finally:
        client.close()
    for name,value in (("frozen-requests",requests),("evaluations",summaries),("numerical-checks",numbers)):
        (args.public/f"{name}.json").write_text(json.dumps(value,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"start":start,"end":end,"accepted_day":str(args.day),"datasets":len(rows("SELECT id FROM datasets")),
                      "sealed_manifests":len(rows("SELECT id FROM arrival_manifests")),"analytical_scenarios":len(summaries)}))


if __name__ == "__main__":
    main()
