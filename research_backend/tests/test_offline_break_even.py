"""Service regressions for an offline stop moved to the verified entry price."""
import json
from itertools import count
from uuid import UUID

import pytest

from research_backend.app.analysis_evidence import canonical
from research_backend.app.db import transaction
from test_bounded_crt_smt_successors import canonical_successors
from test_offline_first_exit import (
    BASELINE, ENTRY_LONG, ENTRY_SHORT, LONG_STOP, LONG_TARGET, SHORT_STOP, SHORT_TARGET,
    calculate, position, snapshots,
)
from test_offline_market_entry import Q, publish, quote, tape, validate


def test_fixed_exit_baseline(position,monkeypatch):
    ids = count(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4",lambda:UUID(int=next(ids)))
    cases = {}
    for direction,first,last in (("bullish",ENTRY_LONG,LONG_STOP),("bearish",ENTRY_SHORT,SHORT_TARGET)):
        run,intent = position(direction)
        data = tape([first,last])
        entry, = validate(run,data,intent["id"])["results"]
        for offset in (3,4):
            row, = calculate(run,data,entry,Q+offset)["results"]
            cases[f"{direction}+{offset}"] = canonical(row)
    pinned = json.loads(BASELINE.with_name("merged-fixed-exit-baseline.json").read_text(encoding="utf-8"))
    assert {k:v.encode() for k,v in cases.items()}=={k:v.encode() for k,v in pinned.items()}


@pytest.fixture(params=["bullish","bearish"])
def scenario(position,request):
    long = request.param=="bullish"
    run,intent = position(request.param,management={"break_even_at_r":1})
    return {
        "run":run,"intent":intent,"entry":ENTRY_LONG if long else ENTRY_SHORT,"side":"BID" if long else "ASK",
        "fill":110314 if long else 110306,"stop":110214 if long else 110406,
        "threshold":110414 if long else 110206,"target_units":110514 if long else 110106,
        "arm":quote(Q+4,Q+4,7,110414,110419) if long else quote(Q+4,Q+4,7,110201,110206),
        "miss":quote(Q+3,Q+3,6,110413,110415) if long else quote(Q+3,Q+3,6,110205,110207),
        "gap":quote(Q+5,Q+5,8,110310,110315) if long else quote(Q+5,Q+5,8,110308,110311),
        "gap_units":110310 if long else 110311,"original":LONG_STOP if long else SHORT_STOP,
        "target":LONG_TARGET if long else SHORT_TARGET,
    }


def inspection(case,quotes,intervals=None):
    run = case["run"]
    data = tape(quotes) if intervals is None else tape(quotes,intervals)
    entry, = validate(run,data,case["intent"]["id"])["results"]
    assert entry["fill"]["price_units"]==case["fill"]
    original = snapshots(run,entry)
    queried = set()

    def inspect(offset,status):
        first = calculate(run,data,entry,Q+offset)
        assert calculate(run,data,entry,Q+offset)==first
        row, = first["results"]
        assert row["status"]==status
        assert (first["simulated_orders"],first["simulated_fills"],first["simulated_exits"])==(
            1,1,int(status=="SIMULATED_EXIT"))
        assert row["identity"]["policy"]["id"]=="OFFLINE_BREAK_EVEN_FIRST_EXIT"
        assert row["identity"]["entry_fill_id"]==entry["fill"]["id"]
        queried.add(offset)
        with transaction() as db:
            stored = db.execute("SELECT id,payload FROM offline_first_exit_results WHERE entry_result_id=?",
                                (entry["id"],)).fetchall()
        assert len(stored)==len(queried)
        assert dict(stored)[row["id"]]==canonical(row)
        assert snapshots(run,entry)==original
        return row
    return inspect


def test_paired_future_outcomes_and_earlier_requery(scenario):
    c = scenario
    target = c["target"] | {"time_ms":Q+5,"available_at_ms":Q+5,"source_order":8}
    prefixes = []
    for last,reason,price in ((c["gap"],"BREAK_EVEN_STOP",c["gap_units"]),(target,"REWARD_TARGET",c["target_units"])):
        inspect = inspection(c,[c["entry"],c["miss"],c["arm"],last])
        before = inspect(3,"OPEN_AT_CUTOFF")
        assert (before["active_stop_units"],before["arm_threshold_units"],before["activation"])==(
            c["stop"],c["threshold"],None)
        armed = inspect(4,"OPEN_AT_CUTOFF")
        assert armed["active_stop_units"]==c["fill"] and armed["exit"] is None
        activation = armed["activation"]
        assert activation["id"].startswith("BA-")
        assert activation["quote"]==c["arm"] | {"scale":100000} and activation["side"]==c["side"]
        final = inspect(5,"SIMULATED_EXIT")
        assert final["active_stop_units"]==c["fill"] and final["activation"]==activation
        event = final["exit"]
        assert (event["reason"],event["side"],event["price_units"],event["time_ms"])==(reason,c["side"],price,Q+5)
        assert event["quote"]==last | {"scale":100000}
        assert event["threshold_units"]==(c["fill"] if reason=="BREAK_EVEN_STOP" else c["target_units"])
        assert inspect(6,"SIMULATED_EXIT")["exit"]==event
        assert inspect(3,"OPEN_AT_CUTOFF")==before and inspect(4,"OPEN_AT_CUTOFF")==armed
        prefixes.append((before,armed))
    for left,right in zip(*prefixes):
        assert left["identity"]["execution_data_fingerprint"]!=right["identity"]["execution_data_fingerprint"]
        assert left["active_stop_units"]==right["active_stop_units"]
        assert left["exit"]==right["exit"] is None
        if left["activation"]:
            assert left["activation"]["quote"]==right["activation"]["quote"]


def test_original_stop_and_direct_target(scenario):
    c = scenario
    for last,reason,threshold in ((c["original"],"PROTECTIVE_STOP",c["stop"]),
                                  (c["target"],"REWARD_TARGET",c["target_units"])):
        final = inspection(c,[c["entry"],c["miss"],last])(4,"SIMULATED_EXIT")
        assert final["activation"] is None and final["active_stop_units"]==c["stop"]
        assert (final["exit"]["reason"],final["exit"]["threshold_units"])==(reason,threshold)
        assert final["exit"]["quote"]==last | {"scale":100000}


def test_activation_priority_duplicates_conflict_and_gap(scenario):
    c = scenario
    second = c["gap"] | {"time_ms":Q+4,"available_at_ms":Q+4}
    final = inspection(c,[second,c["arm"],c["entry"],c["arm"]])(4,"SIMULATED_EXIT")
    assert final["activation"]["quote"]==c["arm"] | {"scale":100000}
    assert final["exit"]["reason"]=="BREAK_EVEN_STOP" and final["exit"]["quote"]==second | {"scale":100000}

    miss = c["miss"] | {"time_ms":Q+4,"available_at_ms":Q+4,"source_order":7}
    rows = [c["entry"],miss,c["arm"],second]
    for ordered in (rows,list(reversed(rows))):
        row = inspection(c,ordered)(4,"AMBIGUOUS_QUOTE_ORDER")
        assert row["activation"] is None and row["exit"] is None
    inspect = inspection(c,[c["entry"],c["arm"]],[(Q+1,Q+2),(Q+4,Q+5)])
    assert inspect(2,"OPEN_AT_CUTOFF")["active_stop_units"]==c["stop"]
    row = inspect(4,"INSUFFICIENT_QUOTE_COVERAGE")
    assert row["activation"] is None and row["exit"] is None


def test_fractional_activation_level_is_unsupported(position):
    run,intent = position(management={"break_even_at_r":0.333})
    data = tape([ENTRY_LONG,LONG_TARGET])
    entry, = validate(run,data,intent["id"])["results"]
    assert calculate(run,data,entry,Q+4)["results"][0]["status"]=="UNREPRESENTABLE_EXIT_LEVELS"
