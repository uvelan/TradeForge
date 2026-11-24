"""Check observation-time stops through the published offline service."""
import json
from itertools import count
from pathlib import Path
from uuid import UUID

import pytest

from research_backend.app.db import transaction
from test_bounded_crt_smt_successors import canonical_successors
from test_offline_first_exit import ENTRY_LONG, ENTRY_SHORT, calculate, position, snapshots
from test_offline_market_entry import Q, publish, quote, tape, validate

BASELINE = Path(__file__).resolve().parents[2]/"docs/implementation/evidence/TF-I10A2-management-ordering/reviewed-timing.json"


def closing(long,obs,delivery,seq,units):
    return quote(Q+obs,Q+delivery,seq,units if long else units-3,units+3 if long else units)


@pytest.mark.parametrize("long",[True,False])
@pytest.mark.parametrize("kind",["break_even","trailing","combined"])
def test_reviewed_timing(position,monkeypatch,long,kind):
    ids = count(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4",lambda:UUID(int=next(ids)))
    settings = {"break_even_at_r":1} if kind=="break_even" else {"trailing_pips":5}
    if kind=="combined":
        settings["break_even_at_r"]=1
    run,intent = position("bullish" if long else "bearish",management=settings)
    entry_quote = ENTRY_LONG if long else ENTRY_SHORT
    arm = closing(long,4,4,7,110414 if long else 110206)
    for obs in (3,4,5):
        gap = closing(long,obs,5,8,110310 if long else 110311)
        data = tape([entry_quote,arm,gap])
        entry, = validate(run,data,intent["id"])["results"]
        prior = json.loads(BASELINE.read_text())[f"{kind}-{long}-{obs}"]
        legacy = json.loads(prior)
        assert legacy["identity"]["entry_result_id"]==entry["id"]
        with transaction() as db:
            db.execute("INSERT INTO offline_first_exit_results VALUES (?,?,?)",(legacy["id"],entry["id"],prior))
        before = snapshots(run,entry)
        armed, = calculate(run,data,entry,Q+4)["results"]
        row, = calculate(run,data,entry,Q+5)["results"]
        assert calculate(run,data,entry,Q+5)["results"]==[row]
        assert calculate(run,data,entry,Q+4)["results"]==[armed]
        assert snapshots(run,entry)==before
        assert legacy["status"]==("UNSUPPORTED_EXIT_MANAGEMENT" if kind=="combined" else "SIMULATED_EXIT")
        assert row["id"]!=legacy["id"]
        with transaction() as db:
            stored = dict(db.execute("SELECT id,payload FROM offline_first_exit_results WHERE entry_result_id=?",
                                     (entry["id"],)))
        assert set(stored)=={legacy["id"],armed["id"],row["id"]} and stored[legacy["id"]]==prior
        assert armed["status"]=="OPEN_AT_CUTOFF"
        assert row["status"]==("OPEN_AT_CUTOFF" if obs==3 else "SIMULATED_EXIT")
        if obs!=3:
            assert row["exit"]["stop_quote"]==arm | {"scale":100000}
            assert row["exit"]["quote"]==gap | {"scale":100000}
            assert row["exit"]["reason"]==("BREAK_EVEN_STOP" if kind=="break_even" else "TRAILING_STOP")


@pytest.mark.parametrize("long",[True,False])
def test_delayed_quotes_use_applicable_ratchet_and_original_limits(position,long):
    run,intent = position("bullish" if long else "bearish",management={"trailing_pips":5})
    first = ENTRY_LONG if long else ENTRY_SHORT
    move1 = closing(long,4,4,7,110400 if long else 110200)
    move2 = closing(long,6,6,8,110440 if long else 110160)
    for obs,units,reason,threshold,establishing in (
        (5,110340 if long else 110260,"TRAILING_STOP",110350 if long else 110250,move1),
        (5,110370 if long else 110230,None,None,None),
        (3,110205 if long else 110410,"PROTECTIVE_STOP",110214 if long else 110406,first),
        (3,110520 if long else 110100,"REWARD_TARGET",110514 if long else 110106,None),
    ):
        close = closing(long,obs,7,9,units)
        data = tape([first,move1,move2,close])
        entry, = validate(run,data)["results"]
        earlier = calculate(run,data,entry,Q+6)
        result = calculate(run,data,entry,Q+7)
        assert calculate(run,data,entry,Q+6)==earlier
        row, = result["results"]
        assert row["ratchet"]["quote"]==move2 | {"scale":100000}
        assert result["simulated_exits"]==int(reason is not None)
        if reason:
            assert (row["exit"]["reason"],row["exit"]["threshold_units"])==(reason,threshold)
            assert row["exit"]["quote"]==close | {"scale":100000}
            if establishing:
                assert row["exit"]["stop_quote"]==establishing | {"scale":100000}


@pytest.mark.parametrize("long",[True,False])
def test_all_four_rules_precedence_and_exposure(position,long):
    management = {"trailing_pips":5,"break_even_at_r":1,"partial_exit":{"at_r":1,"percent":25},
                  "target":{"kind":"REWARD_MULTIPLE","multiple":3},"time_exit":{"unit":"SECONDS","count":1}}
    run,intent = position("bullish" if long else "bearish",management=management)
    first = ENTRY_LONG if long else ENTRY_SHORT
    cross = closing(long,4,4,7,110414 if long else 110206)
    deadline = 1002
    for label,units,expected in (
        ("stop",110340 if long else 110260,"TRAILING_STOP"),
        ("time",110400 if long else 110210,"TIME_EXIT"),
        ("target",110620 if long else 110000,"REWARD_TARGET"),
    ):
        last = closing(long,deadline,deadline,8,units)
        data = tape([first,cross,last])
        entry, = validate(run,data)["results"]
        reduced = calculate(run,data,entry,Q+4)
        middle, = reduced["results"]
        assert middle["partial_exit"]["quote"]==middle["activation"]["quote"]==middle["ratchet"]["quote"]==cross | {"scale":100000}
        assert middle["active_stop_units"]==(110364 if long else 110256)
        assert middle["remaining_fraction"]=={"numerator":3,"denominator":4}
        response = calculate(run,data,entry,Q+deadline)
        row, = response["results"]
        assert (response["simulated_partial_exits"],response["simulated_exits"])==(1,1)
        assert row["exit"]["reason"]==expected
        assert row["partial_exit"]["closed_fraction"]=={"numerator":1,"denominator":4}
        assert row["exit"]["closed_fraction"]==middle["remaining_fraction"]
        assert row["remaining_fraction"]=={"numerator":0,"denominator":1}
        assert calculate(run,data,entry,Q+deadline)==response
        assert calculate(run,data,entry,Q+4)==reduced
    for rows,intervals,expected in (
        ([first,cross],[(Q+1,Q+2),(Q+4,Q+10)],"INSUFFICIENT_QUOTE_COVERAGE"),
        ([first,cross,cross | {"ask_units":cross["ask_units"]+1}],[(Q+1,Q+10)],"AMBIGUOUS_QUOTE_ORDER"),
        ([first,closing(long,deadline,deadline,9,110414 if long else 110206)],[(Q+1,Q+deadline)],"SIMULATED_EXIT"),
    ):
        for ordered in (rows,list(reversed(rows))):
            data = tape(ordered,intervals)
            entry, = validate(run,data)["results"]
            row, = calculate(run,data,entry,Q+deadline)["results"]
            assert row["status"]==expected and row["activation"] is None and row["partial_exit"] is None
            if row["exit"]:
                assert row["exit"]["reason"]=="TIME_EXIT"
                assert row["exit"]["closed_fraction"]=={"numerator":1,"denominator":1}
