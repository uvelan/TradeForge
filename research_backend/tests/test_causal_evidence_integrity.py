import pytest

from test_strict_causal_timing import ARCHIVE, OPEN, bar, canonical_seed, fvg_prefix, gaps, make_block
from research_backend.app.crt import CRTDelivery, CRTRules, evaluate_crt
from research_backend.app.causal_timing import strategy_eligible
from research_backend.app.strategy_evidence import EvidenceRegistry


def test_unrecorded_midpoint_taints_opposite_boundary():
    rows = [bar(t, (103,105,100,103)) for t in range(36000,39600,300)]
    midpoint, stamp = bar(39900, (104,104.5,102,103))
    rows += [bar(39600,(104,106,103,104)), (midpoint.model_copy(update={"available_at":None}),stamp),
             bar(40200,(103,104,100,101))]
    registry = EvidenceRegistry()
    result = evaluate_crt(rows,300,40500,"H1",CRTRules(market_schedule=OPEN),CRTDelivery(),ARCHIVE,registry=registry)
    events = sorted(result["crt_sweeps"] + result["crt_revisions"], key=lambda event:event["available_at"])
    assert [e["state"] for e in events] == ["HIGH_SWEPT_RECLAIMED","EXPANSION_OBSERVED","OPPOSITE_BOUNDARY_REACHED"]
    print([(e["state"],e["id"],e["available_at"],e["timing"]["strategy_eligibility"]) for e in events])
    assert events[-1]["timing"]["strategy_eligibility"] == "UNVERIFIED_AVAILABILITY"
    assert strategy_eligible(events[0],40500,registry=registry)
    assert not strategy_eligible(events[-1],40500,registry=registry)
    assert events[-1]["timing"]["lineage_dependencies"]==[events[1]["id"]]
    assert events[-1]["timing"]["required_dependencies"]==[events[0]["id"]]



def unrecorded(row):
    candle, stamp = row
    return candle.model_copy(update={"available_at":None}), stamp


@pytest.mark.parametrize("middle,reason", [
    ((104,107,103,104),"NEW_SWEEP_EXTREME"),
    ((103,104,103,103.5),None),
])
def test_crt_taints_later_progression_after_extreme_or_quiet_age(middle,reason):
    rows = [bar(t,(103,105,100,103)) for t in range(36000,39600,300)]
    rows.extend([bar(39600,(104,106,103,104)),unrecorded(bar(39900,middle)),
                 bar(40200,(103,104,100,101))])
    registry=EvidenceRegistry()
    result=evaluate_crt(rows,300,40500,"H1",CRTRules(market_schedule=OPEN),CRTDelivery(),ARCHIVE,registry=registry)
    revisions=result["crt_revisions"]
    assert ([e["reason"] for e in revisions[:-1]]) == ([reason] if reason else [])
    assert revisions[-1]["available_at"]==40500
    assert revisions[-1]["timing"]["eligibility_reason"]=="UNVERIFIED_AVAILABILITY"
    assert not strategy_eligible(revisions[-1],40500,registry=registry)


@pytest.mark.parametrize("middle", [(106,107,104,106),(106,107,105,106)])
def test_fvg_partial_or_quiet_candle_cannot_restore_availability(middle):
    result=gaps(fvg_prefix(900)+[unrecorded(bar(900,middle)),bar(1200,(105,106,102,105))])
    last=result["revisions"][-1]
    assert last["available_at"]==1500 and last["state"]=="PARTIALLY_MITIGATED"
    assert last["timing"]["eligibility_reason"]=="UNVERIFIED_AVAILABILITY"


def test_block_inherits_unrecorded_touch():
    from decimal import Decimal
    from research_backend.app.order_blocks import Block
    engine=make_block()
    engine.registry.publish(canonical_seed("pivot",600,kind="PIVOT"),"structure",ARCHIVE,"structure",predicate="FORMATION")
    engine.registry.publish(canonical_seed("bos",1020,kind="BOS",dependency=600,refs=["pivot"]),
                            "structure",ARCHIVE,"structure",predicate="CLOSE_ONLY")
    zone=Block("block",1,Decimal(95),Decimal(100),9,{"first_visibility_time":1020,
        "formation_fingerprint":"fixture","timing_recorded":True,"bos_id":"bos","algorithm_version":1})
    engine.book[zone.id]=zone
    engine.put(zone)
    engine.publish(zone,"ACTIVE","CONFIRMED_BOS",bar(600,available=1020)[0],1020)
    candle,stamp=unrecorded(bar(1200,(101,102,100,101)))
    engine.observe(candle,stamp,2,None,[],[],"","")
    candle,stamp=bar(1500,(101,102,97,99))
    engine.observe(candle,stamp,3,None,[],[],"","")
    touch,mitigation=engine.revisions.events
    assert touch["state"]=="TOUCHED" and mitigation["state"]=="PARTIALLY_MITIGATED"
    assert mitigation["timing"]["lineage_dependencies"]==[touch["id"]]
    assert mitigation["timing"]["eligibility_reason"]=="UNVERIFIED_AVAILABILITY"


@pytest.mark.parametrize("problem",["category","type","confirmation","state","missing","ineligible","cycle",
                                     "predicate","source","config","coverage","version"])
def test_positive_canonical_boundary_rejects_invalid_event(problem):
    registry=EvidenceRegistry()
    base=canonical_seed("reference",600,state="ACTIVE",recorded=problem!="ineligible")
    registry.publish(base,"fvg",ARCHIVE,"config",predicate="FORMATION",family="zone",active=True)
    event=canonical_seed("event",900,state="PARTIALLY_MITIGATED",dependency=600,
                         refs=[{"missing":"missing","cycle":"event"}.get(problem,"reference")])
    category="unknown" if problem=="category" else "fvg"
    if problem=="type":
        category="structure"
        event["type"]="UNKNOWN"
    if problem=="confirmation":
        del event["confirmation_time"]
    if problem=="state":
        event["state"]="UNKNOWN"
    if problem=="version":
        event["timing"]["timing_policy_version"]=2
    registry.publish(event,category,{**ARCHIVE,"symbol":"GBPUSD"} if problem=="source" else ARCHIVE,
                     "other" if problem=="config" else "config",
                     predicate="CLOSE_ONLY" if problem=="predicate" else "WHOLE_CANDLE",
                     coverage=problem!="coverage")
    assert not strategy_eligible(event,900,registry=registry)


def test_historical_reference_is_valid_before_expiry_and_rewind():
    registry=EvidenceRegistry()
    reference=canonical_seed("reference",600,state="ACTIVE")
    registry.publish(reference,"fvg",ARCHIVE,"config",predicate="FORMATION",family="zone",active=True)
    expired=canonical_seed("expiry",900,state="EXPIRED",dependency=600,refs=["reference"])
    registry.publish(expired,"fvg",ARCHIVE,"config",predicate="WHOLE_CANDLE",family="zone")
    assert strategy_eligible(reference,900,registry=registry)
    assert not strategy_eligible(reference,900,registry=registry,current=True)
    assert strategy_eligible(reference,899,registry=registry,current=True)
    assert not strategy_eligible(reference,599,registry=registry,current=True)
    assert not strategy_eligible({**reference,"lower":1},899,registry=registry)
    assert not strategy_eligible(reference,899,registry=EvidenceRegistry())


def test_crt_resolves_canonical_evidence_with_zero_delivery():
    rows=[bar(t,(103,105,100,103)) for t in range(36000,39600,300)]
    rows.append(bar(39600,(104,106,103,104)))
    selected=EvidenceRegistry()
    zero=EvidenceRegistry()
    rules=CRTRules(market_schedule=OPEN)
    result=evaluate_crt(rows,300,39900,"H1",rules,CRTDelivery(),ARCHIVE,registry=selected)
    hidden=evaluate_crt(rows,300,39900,"H1",rules,CRTDelivery(formation_limit=0,sweep_limit=0),
                        ARCHIVE,registry=zero)
    event,=result["crt_sweeps"]
    assert hidden["crt_sweeps"]==[]
    assert selected.records==zero.records
    assert strategy_eligible(event,39900,registry=zero)
    assert not strategy_eligible(event,39899,registry=zero)

def test_liquidity_does_not_restore_trust_after_unrecorded_boundary_revision():
    from decimal import Decimal
    from research_backend.app.liquidity import LiquidityDelivery, LiquidityRules, LiquidityStream, Pool, digest
    stream=LiquidityStream(LiquidityRules(equality_bps=100),LiquidityDelivery(),300,timing=(3000,ARCHIVE,OPEN))
    registry=stream.registry
    initial=canonical_seed("initial",900,state="ACTIVE")
    registry.publish(initial,"pool",ARCHIVE,digest(stream.rules.identity()),predicate="FORMATION")
    pool=Pool("pool","HIGH",Decimal(100),Decimal(100),Decimal(100),9,0,600,state="ACTIVE",activated_at=900)
    pool.latest_revision=("initial",600,900,"p","e")
    stream.active[pool.id]=pool
    stream.buckets[stream.bucket(pool.side,pool.anchor)]={pool.id}
    stream.index(pool)
    pivot={"id":"new-pivot","side":"HIGH","price":100.5,"pivot_time":600,
           "available_at":1200,"timing_recorded":False}
    candle,stamp=unrecorded(bar(900,(99,100,98,99)))
    stream.observe(candle,stamp,2,None,[(pivot,2)],[],"input","fixture")
    candle,stamp=bar(1200,(99,103,98,99))
    stream.observe(candle,stamp,3,None,[],[],"input","fixture")
    revision, terminal=stream.pools.events
    sweep,=stream.sweeps.events
    assert (revision["state"],terminal["state"],sweep["available_at"])==("ACTIVE","SWEPT",1500)
    assert sweep["timing"]["required_dependencies"]==[revision["id"]]
    assert sweep["timing"]["eligibility_reason"]=="UNVERIFIED_AVAILABILITY"
    assert not strategy_eligible(sweep,1500,registry=registry)
