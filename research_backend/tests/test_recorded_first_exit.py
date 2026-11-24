"""Recorded first-exit verification via a persisted published entry and original BI5."""
import pytest

from research_backend.app.db import transaction
from research_backend.app.offline_first_exit import validate_first_exit
from research_backend.app.offline_market_entry import validate_market_entries
from test_bounded_crt_smt_successors import canonical_successors
from test_offline_market_entry import publish, source_records
from test_recorded_limit_entry import QUAL, bi5
from test_recorded_market_entry import BASE, controlled, prepare, shard

pytestmark = pytest.mark.parametrize("controlled", [(BASE+3600, 90)], indirect=True)

FIXED = {"initial_stop":{"kind":"FIXED_PIPS","distance":10},
         "target":{"kind":"REWARD_MULTIPLE","multiple":3}}


def totals():
    with transaction() as db:
        return [list(db.execute("SELECT id,CAST(payload AS BLOB) FROM "+table+" ORDER BY id"))
                for table in ("offline_market_entry_results","offline_first_exit_results")]


@pytest.mark.parametrize("direction,kind,threshold,open_quote,exit_quote,reason,side,units", [
    ("bullish","MARKET",None,(116270,116274),(116165,116170),"PROTECTIVE_STOP","BID",116165),
    ("bearish","MARKET",None,(116270,116274),(116367,116374),"PROTECTIVE_STOP","ASK",116374),
    ("bullish","LIMIT",1.16274,(116270,116274),(116575,116580),"REWARD_TARGET","BID",116575),
    ("bearish","STOP",1.16272,(116270,116274),(115960,115965),"REWARD_TARGET","ASK",115965),
])
def test_recorded_first_exit_uses_pinned_original_after_entry_shard(
    controlled,direction,kind,threshold,open_quote,exit_quote,reason,side,units
):
    run,intent = controlled(direction=direction,kind=kind,entry_price=threshold,management=FIXED)
    original = source_records(run)
    spec,rows = prepare(bi5([(-1000,*open_quote),(400,*open_quote),
                            (700,*exit_quote),(1000,*open_quote)]),14,QUAL)
    # Entry shards need not contain the later exit observation.
    spec = spec | {"partitions":[shard(rows[:2])]}
    entry_response = validate_market_entries(run,recorded=spec)
    entry, = entry_response["results"]
    assert entry_response["simulated_fills"] == 1 and entry["source"]==intent
    prior, = totals()[0]
    kwargs = {"recorded":spec,"entry_result_id":entry["id"]}
    at_entry = validate_first_exit(run,cutoff_ms=QUAL+400,**kwargs)
    assert at_entry["simulated_exits"]==0 and at_entry["results"][0]["status"]=="OPEN_AT_CUTOFF"
    gap = validate_first_exit(run,cutoff_ms=QUAL+699,**kwargs)
    assert gap["simulated_exits"]==0 and gap["results"][0]["status"]=="INSUFFICIENT_QUOTE_COVERAGE"
    at_exit = validate_first_exit(run,cutoff_ms=QUAL+700,**kwargs)
    r, = at_exit["results"]
    assert at_exit["simulated_orders"]==at_exit["simulated_fills"]==at_exit["simulated_exits"]==1
    assert r["identity"]["policy"]["id"]=="OFFLINE_RECORDED_FIXED_FIRST_EXIT"
    assert r["recorded_evidence"]["source"]==spec["source"]
    fill = r["exit"]
    assert (fill["reason"],fill["side"],fill["price_units"],fill["time_ms"],fill["quote"]["source_order"])==(
        reason,side,units,QUAL+700,2)
    later = validate_first_exit(run,cutoff_ms=QUAL+1000,**kwargs)
    assert later["results"][0]["exit"]==fill and later["results"][0]["id"]!=r["id"]
    assert validate_first_exit(run,cutoff_ms=QUAL+700,**kwargs)==at_exit
    assert validate_first_exit(run,cutoff_ms=QUAL+400,**kwargs)==at_entry
    assert totals()[0]==[prior] and len(totals()[1])==4 and source_records(run)==original
    with pytest.raises(ValueError,match="does not match verified replay"):
        validate_first_exit(run,cutoff_ms=QUAL+700,**(kwargs | {"recorded": spec | {"end":spec["end"]-1}}))


def test_recorded_first_exit_does_not_bridge_missing_interval(controlled):
    run,_ = controlled(direction="bullish",management=FIXED)
    spec,_ = prepare(bi5([(-1000,116270,116274),(400,116270,116274),(61000,116160,116165)]),14,QUAL)
    saved, = validate_market_entries(run,recorded=spec)["results"]
    assert saved["fill"]["time_ms"]==QUAL+400
    result = validate_first_exit(run,recorded=spec,entry_result_id=saved["id"],cutoff_ms=QUAL+61000)
    assert result["simulated_exits"]==0 and result["results"][0]["status"]=="INSUFFICIENT_QUOTE_COVERAGE"
    assert result["results"][0]["exit"] is None


def test_recorded_first_exit_rejects_unapproved_management(controlled):
    run,_ = controlled(direction="bullish",management=FIXED | {"break_even_at_r":1})
    spec,_ = prepare(bi5([(-1000,116270,116274),(400,116270,116274)]),14,QUAL)
    saved, = validate_market_entries(run,recorded=spec)["results"]
    with pytest.raises(ValueError,match="fixed protective stop and target only"):
        validate_first_exit(run,recorded=spec,entry_result_id=saved["id"],cutoff_ms=QUAL+400)
    assert not totals()[1]


def test_later_ordinal_at_fill_millisecond_can_exit(controlled):
    run,_ = controlled(direction="bullish",management=FIXED)
    spec,rows = prepare(bi5([(-1000,116270,116274),(400,116270,116274),(400,116174,116175)]),14,QUAL)
    spec = spec | {"partitions":[shard(rows[:2])]}
    saved, = validate_market_entries(run,recorded=spec)["results"]
    assert saved["fill"]["quote"]["source_order"] == 1
    result = validate_first_exit(run,recorded=spec,entry_result_id=saved["id"],cutoff_ms=QUAL+400)
    close = result["results"][0]["exit"]
    assert (close["reason"],close["price_units"],close["time_ms"],close["quote"]["source_order"])==(
        "PROTECTIVE_STOP",116174,QUAL+400,2)
