from decimal import Decimal
from heapq import heappush

import pytest

from research_backend.app.causal_timing import TemporalEvidence, strategy_eligible
from research_backend.app.crt import CRTRules, CRTDelivery, evaluate_crt
from research_backend.app.fvg import FVGDelivery, FVGRules, evaluate_fvg
from research_backend.app.indicator_coverage import MarketSchedule
from research_backend.app.indicator_engine import EvidenceBar, Evaluation, evaluate
from research_backend.app.liquidity import LiquidityDelivery, LiquidityRules, LiquidityStream, Pool
from research_backend.app.market_structure import StructureRules, detect_structure
from research_backend.app.order_blocks import Block, OrderBlockDelivery, OrderBlockRules, OrderBlockStream
from research_backend.app.structure_pages import StructureDelivery

ARCHIVE={"source":"MT5_SAVED","symbol":"EURUSD","price_basis":"BID","feed_id":"fixture",
         "context_authority":"CHECKSUM_VERIFIED_ARCHIVE","timing_policy":"STRICT_CAUSAL","timing_policy_version":2}
OPEN=MarketSchedule(kind="continuous")


def bar(start, values=(100,101,99,100), available=None):
    o,h,l,c=values
    stamp=start+300 if available is None else available
    return EvidenceBar(time=start,open=o,high=h,low=l,close=c,available_at=stamp),stamp


@pytest.mark.parametrize("start,dependency,available,cutoff,close_only,expected", [
    (900,900,1200,1200,False,"ELIGIBLE"),(899,900,1199,1199,False,"STRADDLING_OBSERVATION"),
    (901,900,1201,1201,False,"ELIGIBLE"),(900,1020,1200,1200,False,"STRADDLING_OBSERVATION"),
    (900,1200,1200,1200,True,"DEPENDENCY_UNAVAILABLE"),
    (900,1201,1500,1500,True,"DEPENDENCY_UNAVAILABLE"),
    (900,1020,1200,1200,True,"ELIGIBLE"),(901,900,1220,1219,False,"NOT_YET_AVAILABLE"),
])
def test_temporal_boundaries(start,dependency,available,cutoff,close_only,expected):
    time=TemporalEvidence(start,start+300,available,dependency,cutoff,True,"CHECKSUM_VERIFIED_ARCHIVE")
    assert time.interaction(close_only=close_only)==expected
    item={"timing":time.contract(close_only=close_only),"status":"confirmed"}
    assert strategy_eligible(item,cutoff) == (expected=="ELIGIBLE")
    assert not strategy_eligible({**item,"status":"provisional"},cutoff)
    assert not strategy_eligible(item,available-1)


def test_consumer_fails_closed_for_unknown_unrecorded_and_legacy():
    assert not strategy_eligible({"status":"confirmed"},9999)
    for recorded,authority,expected in [
        (False,"CHECKSUM_VERIFIED_ARCHIVE","UNVERIFIED_AVAILABILITY"),
        (True,"CLIENT_SUPPLIED_UNVERIFIED","UNVERIFIED_SOURCE"),
    ]:
        item=TemporalEvidence(0,300,300,0,300,recorded,authority).contract()
        assert item["strategy_eligibility"]==expected
        assert not strategy_eligible({"timing":item},300)
    with pytest.raises(ValueError,match="interval"):
        TemporalEvidence(0,300,299,0,300,True,"CHECKSUM_VERIFIED_ARCHIVE")


def structure(rows, cutoff=3000, scope=ARCHIVE):
    return detect_structure(rows,300,StructureRules(left=1,right=1,algorithm_version=2,
        market_schedule=OPEN),scope,StructureDelivery(),{"cutoff":cutoff},local_witness=True)


@pytest.mark.parametrize("availability,candle,expected", [
    (1020,(100,108,99,107),"BOS"),(1020,(100,108,99,101),"TIMING_REJECTED"),
    (1250,(100,108,99,107),"TIMING_REJECTED"),
])
def test_structural_close_and_wick_have_distinct_time_boundaries(availability,candle,expected):
    rows=[bar(0),bar(300,(102,105,100,103)),bar(600,available=availability),
          bar(900,candle,available=max(1200,availability+1))]
    result=structure(rows)
    observation,=result["breaks"]
    assert (observation["type"],observation["break_time"],observation["level_price"])==(expected,900,105)
    assert strategy_eligible(observation,3000)==(expected=="BOS")
    if expected!="BOS":
        assert observation["state_after"]==observation["state_before"]


def test_late_structure_candle_and_scheduled_order_are_explicit():
    rows=[bar(0),bar(300,available=1300),bar(600,available=1200),bar(900,available=1200)]
    output=structure(rows)
    warnings=output["structure_warnings"]
    assert "LATE_STRUCTURE_CANDLE_IGNORED" in warnings
    assert "STRUCTURE_RESET_AT_GAP" in warnings


@pytest.mark.parametrize("start,close,expiry,expected,reason", [
    (900,99,9,"UNCERTAIN","STRADDLING_OBSERVATION"),
    (1200,99,9,"SWEPT","ELIGIBLE"),
    (900,101,9,"BROKEN","ELIGIBLE"),
    (1200,99,2,"EXPIRED","EXPIRED_REFERENCE"),
])
def test_liquidity_uses_latest_boundary_revision(start,close,expiry,expected,reason):
    stream=LiquidityStream(LiquidityRules(),LiquidityDelivery(),300,timing=(3000,ARCHIVE,OPEN))
    number=Decimal(100)
    pool=Pool("pool","HIGH",number,number,number,expiry,0,600,state="ACTIVE",activated_at=750)
    pool.latest_revision=("boundary-revision",600,1020,"p","e")
    stream.active[pool.id]=pool
    stream.buckets[stream.bucket(pool.side,pool.anchor)]={pool.id}
    stream.index(pool)
    heappush(stream.expirations,(expiry,pool.id))
    candle,stamp=bar(start,(99,103,98,close))
    stream.observe(candle,stamp,2,None,[],[],"input","fixture")
    event,=stream.pools.events
    assert event["state"]==expected
    assert event["timing"]["required_dependencies"]==["boundary-revision"]
    assert event["timing"]["strategy_eligibility"]==reason
    assert not stream.active
    assert bool(stream.sweeps.events)==(expected in {"BROKEN","SWEPT"})


def make_block():
    return OrderBlockStream(OrderBlockRules(),StructureRules(),ARCHIVE,300,OrderBlockDelivery(),timing_cutoff=3000)


@pytest.mark.parametrize("start,values,expiry,state,eligible", [
    (900,(101,102,96,99),9,"UNCERTAIN",False),
    (1200,(101,102,96,99),9,"PARTIALLY_MITIGATED",True),
    (1200,(94,94,92,93),9,"INVALIDATED",False),
    (1200,(101,102,96,99),2,"EXPIRED",False),
])
def test_order_block_wick_lifecycle(start,values,expiry,state,eligible):
    engine=make_block()
    zone=Block("block",1,Decimal(95),Decimal(100),expiry,{"first_visibility_time":1020,
        "formation_fingerprint":"fixture","timing_recorded":True,"bos_id":"break"})
    engine.book[zone.id]=zone
    engine.put(zone)
    engine.publish(zone,"ACTIVE","CONFIRMED_BOS",bar(600)[0],1020)
    heappush(engine.expiry,(expiry,zone.id))
    candle,stamp=bar(start,values)
    engine.advance(candle,stamp,2)
    event,=engine.revisions.events
    assert event["state"]==state
    assert event["timing"]["dependency_available_at"]==1020
    assert strategy_eligible(event,3000)==eligible


def test_order_block_rejects_unattested_structural_dependency():
    engine=make_block()
    candle,stamp=bar(900)
    engine.form({"type":"BOS","status":"confirmed"},candle,stamp,2)
    assert not engine.book
    assert engine.rejections["INELIGIBLE_STRUCTURAL_DEPENDENCY"]==1


def gaps(rows, cutoff=3000, scope=ARCHIVE, **rules):
    return evaluate_fvg(rows,300,cutoff,FVGRules(market_schedule=OPEN,**rules),FVGDelivery(),scope,True)


def fvg_prefix(activation):
    return [bar(0,(98,100,97,99)),bar(300,(100,108,100,108)),bar(600,(106,110,105,109),activation)]


@pytest.mark.parametrize("activation,values,state,reason", [
    (1020,(105,106,103,104),"UNCERTAIN","STRADDLING_OBSERVATION"),
    (900,(105,106,103,104),"PARTIALLY_MITIGATED","ELIGIBLE"),
    (900,(97,99,96,98),"INVALIDATED","AMBIGUOUS_INTRABAR_ORDER"),
])
def test_fvg_overlap_required_even_for_close_mode(activation,values,state,reason):
    output=gaps(fvg_prefix(activation)+[bar(900,values)],mitigation="close")
    event,=output["revisions"]
    assert event["state"]==state
    assert (event["lower"],event["upper"])==(100,105)
    assert event["timing"]["eligibility_reason"]==reason


def test_fvg_policy_cursor_replay_and_rolling_formation_identity():
    rows=fvg_prefix(900)+[bar(900,(105,106,103,104))]
    strict=gaps(rows)
    early=gaps(rows[:3],cutoff=900)
    legacy=gaps(rows,scope={key:value for key,value in ARCHIVE.items() if not key.startswith("timing_")})
    assert not early["revisions"]
    assert early["formations"][0]["id"]==strict["formations"][0]["id"]
    assert strict["formations"][0]["id"]!=legacy["formations"][0]["id"]
    assert strict["fvg_page"]["snapshot"]!=legacy["fvg_page"]["snapshot"]
    shifted=[(item.model_copy(update={"time":item.time+300,"available_at":stamp+300}),stamp+300)
             for item,stamp in rows]
    window=gaps(shifted)
    linked=gaps([bar(0,(97,99,95,98))]+shifted)
    assert window["formations"][0]["id"] in {event["id"] for event in linked["formations"]}


@pytest.mark.parametrize("delay,expected", [(120,"UNCERTAIN"),(0,"AMBIGUOUS")])
def test_crt_delayed_reference_and_dual_sweep(delay,expected):
    rows=[bar(stamp,(103,105,100,103),stamp+300+(delay if stamp==3300 else 0))
          for stamp in range(0,3600,300)]
    rows.append(bar(3600,(103,106,99,103)))
    result=evaluate_crt(rows,300,3900,"H1",CRTRules(market_schedule=OPEN),CRTDelivery(),ARCHIVE)
    observations=result["crt_sweeps"]+result["crt_revisions"]
    observation,=observations
    assert observation["state"]==expected
    assert not strategy_eligible(observation,3900)
    assert observation["timing"]["dependency_available_at"]==3600+delay


def test_strict_api_caller_cannot_claim_verified_availability():
    data=dict(source="MT5_SAVED",symbol="EURUSD",timeframe="M5",feed_id="fixture",price_basis="BID",
        cutoff=1200,candles=[row.model_dump() for row,_ in fvg_prefix(900)],
        indicators=[{"id":"fair_value_gap","fvg":{"market_schedule":{"kind":"continuous"}}}])
    strict=evaluate(Evaluation.model_validate({**data,"timing_policy":"STRICT_CAUSAL"}))["results"][0]
    legacy=evaluate(Evaluation.model_validate(data))["results"][0]
    assert strict["formations"][0]["timing"]["strategy_eligibility"]=="UNVERIFIED_SOURCE"
    assert legacy["analysis"]["strategy_eligibility"]=="RESEARCH_ONLY"
    assert strict["analysis"]["timing_policy_version"]==2
    assert legacy["analysis"]["timing_policy_version"]==1
    assert strict["analysis"]["configuration_fingerprint"]!=legacy["analysis"]["configuration_fingerprint"]


def test_complete_eligible_structure_can_form_order_block():
    rows=[bar(0),bar(300,(104,105,100,102)),bar(600,available=1020),
          bar(900,(100,108,99,107))]
    engine=make_block()
    detect_structure(rows,300,StructureRules(left=1,right=1,algorithm_version=2,market_schedule=OPEN),
                     ARCHIVE,StructureDelivery(),{"cutoff":3000},engine.observe,local_witness=True)
    event,=engine.formations.events
    assert (event["candidate_time"],event["lower"],event["upper"])==(300,100,105)
    assert event["timing"]["required_dependencies"]==[event["bos_id"]]
    assert strategy_eligible(event,1199) is False
    assert strategy_eligible(event,1200) is True


def test_strict_forex_closure_preserves_state_across_missing_weekend_slots():
    from datetime import datetime

    friday=int(datetime.fromisoformat("2026-11-06T21:55:00+00:00").timestamp())
    sunday=int(datetime.fromisoformat("2026-11-08T22:00:00+00:00").timestamp())
    schedule=MarketSchedule(kind="forex_weekly")
    detected=detect_structure([bar(friday),bar(sunday)],300,StructureRules(algorithm_version=2,
        market_schedule=schedule),ARCHIVE,StructureDelivery(),{"cutoff":sunday+300},local_witness=True)
    assert detected["continuity"]["counts"]=={"SCHEDULED_CLOSURE":1}
    assert detected["continuity"]["recent"][0]["state_preserved"] is True


def test_crt_delayed_reclaim_does_not_credit_a_straddling_midpoint_candle():
    rows=[bar(stamp,(103,105,100,103)) for stamp in range(0,3600,300)]
    rows += [bar(3600,(104,106,103,104),4030),bar(3900,(104,104.5,102,103))]
    result=evaluate_crt(rows,300,4200,"H1",CRTRules(market_schedule=OPEN),CRTDelivery(),ARCHIVE)
    sweep,=result["crt_sweeps"]
    revision,=result["crt_revisions"]
    assert sweep["state"]=="HIGH_SWEPT_RECLAIMED"
    assert revision["state"]=="UNCERTAIN"
    assert revision["timing"]["dependency_available_at"]==4030
    assert not strategy_eligible(revision,4200)


def test_price_correction_revises_strict_identity_even_when_extrema_are_equal():
    original=fvg_prefix(900)
    amended=[original[0],(original[1][0].model_copy(update={"close":106}),600),original[2]]
    first,second=[gaps(rows)["formations"][0] for rows in (original,amended)]
    assert (first["lower"],first["upper"])==(second["lower"],second["upper"])
    assert first["id"]!=second["id"]


@pytest.mark.parametrize("duplicate", [True,False])
def test_strict_caller_rejects_duplicate_and_preclose_recorded_evidence(duplicate):
    data=dict(source="DEMO",symbol="EURUSD",timeframe="M5",cutoff=1200,timing_policy="STRICT_CAUSAL",
              indicators=[{"id":"fair_value_gap"}])
    row=fvg_prefix(900)[0][0].model_dump()
    data["candles"]=[row,row] if duplicate else [{**row,"available_at":299}]
    with pytest.raises(ValueError,match="duplicate|precedes"):
        evaluate(Evaluation.model_validate(data))


@pytest.mark.parametrize("status", ["MISSING_OPEN_CANDLES","SOURCE_INTEGRITY_FAILURE"])
def test_failed_frozen_archive_never_produces_eligible_observations(status):
    data=dict(source="MT5_SAVED",symbol="EURUSD",timeframe="M5",feed_id="fixture",price_basis="BID",
              cutoff=1200,frozen=True,timing_policy="STRICT_CAUSAL",candles=[],
              indicators=[{"id":"fair_value_gap","fvg":{"history_bars":10000,"history_start":0}}])
    def missing(*args):
        return {"rows":[],"evidence":{"status":status,"context_authority":"UNAVAILABLE"}}
    result=evaluate(Evaluation.model_validate(data),missing)["results"][0]
    assert result["formations"]==[]
    assert result["analysis"]["strategy_eligibility"]==status


def test_fvg_legacy_cursor_rejected_in_strict_and_delivery_keeps_formation_ids():
    rows=fvg_prefix(900)+[bar(900,(109,112,108,111)),bar(1200,(116,118,115,117))]
    rules=FVGRules(market_schedule=OPEN)
    legacy={key:value for key,value in ARCHIVE.items() if not key.startswith("timing_")}
    def run(scope,delivery):
        return evaluate_fvg(rows,300,1500,rules,delivery,scope,True)
    archived=run(legacy,FVGDelivery(formation_limit=1))
    cursor=archived["fvg_page"]["formations"]["next_cursor"]
    assert cursor
    with pytest.raises(ValueError,match="evidence changed"):
        run(ARCHIVE,FVGDelivery(formation_cursor=cursor))
    projected=run(ARCHIVE,FVGDelivery(formation_limit=1))["formations"][0]
    complete=run(ARCHIVE,FVGDelivery())["formations"]
    assert projected in complete

def test_strict_shared_context_filters_unverified_structure():
    from test_analysis_evidence import configured_request

    data=configured_request()
    legacy=evaluate(Evaluation.model_validate(data))["results"][0]
    strict=evaluate(Evaluation.model_validate({**data,"timing_policy":"STRICT_CAUSAL"}))["results"][0]
    assert "BOS" in legacy["crt_sweeps"][0]["context"]
    assert "BOS" not in strict["crt_sweeps"][0]["context"]
    assert strict["context_configuration"]["strategy_context_policy"]=="ONLY_ELIGIBLE_AT_OBSERVATION_OPEN"


def test_wider_late_boundary_cannot_hide_possible_interaction_with_old_boundary():
    stream=LiquidityStream(LiquidityRules(),LiquidityDelivery(),300,timing=(3000,ARCHIVE,OPEN))
    pool=Pool("pool","HIGH",Decimal(100),Decimal(100),Decimal(104),9,0,600,state="ACTIVE",activated_at=750)
    pool.latest_revision=("wider-boundary",600,1020,"p","e")
    stream.active[pool.id]=pool
    stream.buckets[stream.bucket(pool.side,pool.anchor)]={pool.id}
    stream.index(pool)
    candle,stamp=bar(900,(99,102,98,99))
    stream.observe(candle,stamp,2,None,[],[],"input","fixture")
    event,=stream.pools.events
    assert event["state"]=="UNCERTAIN"
    assert event["timing"]["eligibility_reason"]=="STRADDLING_OBSERVATION"
    assert not stream.sweeps.events
