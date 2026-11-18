import pytest

from research_backend.app import storage
from research_backend.app.fvg import FVGDelivery, FVGRules, evaluate_fvg
from research_backend.app.strategy_evidence import EvidenceRegistry
from test_strict_causal_timing import ARCHIVE, OPEN, bar, canonical_seed


def rows():
    return [bar(36000,(103,105,100,103)),bar(36300,(103,105,100,103)),
            bar(36600,(105,107,102,106)),bar(36900,(108,110,106,109)),
            *[bar(t,(108,110,106,109)) for t in range(37200,39900,300)]]


def unrecorded_rows():
    candles=rows()
    first,stamp=candles[0]
    candles[0]=(first.model_copy(update={"available_at":None}),stamp)
    return candles


@pytest.mark.parametrize("reverse",[False,True])
def test_same_formation_in_overlapping_calculations(reverse):
    history=unrecorded_rows()
    def evaluate(offset,registry):
        rules=FVGRules(market_schedule=OPEN,history_bars=10000,history_start=36000+offset*300)
        return evaluate_fvg(history[offset:],300,39900,rules,FVGDelivery(),ARCHIVE,True,registry=registry)
    separate=[evaluate(i,EvidenceRegistry()) for i in range(2)]
    events=[next(e for e in result["formations"] if e["c_time"]==36900) for result in separate]
    assert events[0]["id"]==events[1]["id"]=="0600eb7339e7b87d7027f56645d123ea6f53149e426469b976bc6716bff15425"
    assert [e["timing"]["strategy_eligibility"] for e in events]==["UNVERIFIED_AVAILABILITY","ELIGIBLE"]
    assert separate[0]["fvg_page"]["snapshot"]!=separate[1]["fvg_page"]["snapshot"]
    print([(e["id"],e["timing"]["strategy_eligibility"],r["fvg_page"]["snapshot"]) for e,r in zip(events,separate)])
    registry=EvidenceRegistry()
    combined=[evaluate(i,registry) for i in ([1,0] if reverse else [0,1])]
    assert combined==(separate[::-1] if reverse else separate)
    a,b=(event["evidence_reference"] for event in events)
    assert a["calculation_id"] != b["calculation_id"]
    assert a["calculation_snapshot"] != b["calculation_snapshot"]
    assert a["available_at"]==b["available_at"]==37200
    assert a["source"]==b["source"]==["MT5_SAVED","EURUSD","M5","fixture","BID",None]
    assert not registry.eligible(events[0],39900)
    assert registry.eligible(events[1],39900)


@pytest.mark.parametrize("parent",["candle_range_theory","order_blocks"])
@pytest.mark.parametrize("reverse",[False,True])
def test_overlapping_archived_window_api(client,parent,reverse):
    archive=storage.write_partition("MT5","overlap-calculation","EURUSD","candles","M5","BID",
        [c.model_dump() for c,_ in unrecorded_rows()])
    schedule={"kind":"continuous"}
    short={"history_bars":10000,"history_start":36300,"market_schedule":schedule}
    long={**short,"history_start":36000}
    settings=({"crt":{"include_context":True,**long}} if parent=="candle_range_theory" else
              {"ob":{"include_context":True},"structure":{"algorithm_version":2,**long}})
    indicators=[{"id":"fair_value_gap","fvg":short},
                {"id":parent,**settings,"context":{"policy":"independent","fvg":long}}]
    request=dict(source="MT5_SAVED",symbol="EURUSD",feed_id="overlap-calculation",timeframe="M5",
                 price_basis="BID",cutoff=39900,frozen=True,candles=[],timing_policy="STRICT_CAUSAL",
                 partitions=[{k:archive[k] for k in ("id","sha256")}],
                 indicators=indicators[::-1] if reverse else indicators)
    response=client.post("/api/indicators/evaluate",json=request)
    assert response.status_code==200,response.text
    result=next(item for item in response.json()["results"] if item["id"]=="fair_value_gap")
    event=next(event for event in result["formations"] if event["c_time"]==36900)
    assert event["timing"]["strategy_eligibility"]=="ELIGIBLE"
    first=response.json()
    request["indicators"].reverse()
    other=client.post("/api/indicators/evaluate",json=request)
    assert other.status_code==200,other.text
    assert {item["id"]:item for item in first["results"]} == {
        item["id"]:item for item in other.json()["results"]}
    assert first["diagnostics"]["executions"]["fvg"]==2
    print(parent,reverse,event["id"],event["evidence_reference"],event["timing"]["strategy_eligibility"])
    request["indicators"]=[item if item["id"]=="fair_value_gap" else {**item,"context":{"policy":"shared"}}
                           for item in request["indicators"]]
    shared=client.post("/api/indicators/evaluate",json=request)
    assert shared.status_code==200,shared.text
    assert shared.json()["diagnostics"]["executions"]["fvg"]==1


def publication_scope(registry,name):
    return registry.calculation("test","settings",ARCHIVE,name,3000)


def test_current_state_gaps_metadata_and_authority_are_calculation_local():
    registry=EvidenceRegistry()
    long=publication_scope(registry,"long")
    short=publication_scope(registry,"short")
    client=publication_scope(registry,"client")
    events=[]
    for index,view in enumerate((long,short,client)):
        event=canonical_seed("event",600,state="ACTIVE")
        event["warnings"]=[str(index)]
        event["context"]={"source":index}
        if view is client:
            event["timing"].update(source_authority="CLIENT_SUPPLIED_UNVERIFIED",
                                   strategy_eligibility="UNVERIFIED_SOURCE")
        view.publish(event,"fvg",ARCHIVE,"config",predicate="FORMATION",family="fvg-family",active=True)
        events.append(event)
    long.gap("fvg","config",ARCHIVE,900)
    assert registry.current_at(events[0]["evidence_reference"],899)
    assert not registry.current_at(events[0]["evidence_reference"],900)
    assert registry.current_at(events[1]["evidence_reference"],900)
    assert not registry.eligible(events[2],900)
    expired=canonical_seed("expiry",1200,state="EXPIRED",dependency=600,refs=["event"])
    short.publish(expired,"fvg",ARCHIVE,"config",predicate="WHOLE_CANDLE",family="fvg-family")
    assert registry.current_at(events[1]["evidence_reference"],1199)
    assert not registry.current_at(events[1]["evidence_reference"],1200)
    assert registry.eligible(events[1],1200)
    assert registry.current_at(events[0]["evidence_reference"],899)
    assert not registry.eligible({**events[1],"warnings":["forged"]},1200)


@pytest.mark.parametrize("case",["bare","ambiguous","incompatible","forged","eligible"])
def test_cross_engine_dependencies_require_exact_pinned_producer(case):
    registry=EvidenceRegistry()
    left=publication_scope(registry,"left")
    right=publication_scope(registry,"right")
    block=publication_scope(registry,"block")
    block.pin_producer("structure",left)
    references=[]
    for view in (left,right):
        pivot=canonical_seed("pivot",300,kind="PIVOT")
        view.publish(pivot,"structure",ARCHIVE,"structure",predicate="FORMATION")
        bos=canonical_seed("bos",600,kind="BOS",dependency=300,refs=["pivot"])
        view.publish(bos,"structure",ARCHIVE,"structure",predicate="CLOSE_ONLY")
        references.append(bos["evidence_reference"])
    pinned={"bare":[],"ambiguous":references,"incompatible":references[1:],
            "forged":[{**references[0],"available_at":601}],"eligible":references[:1]}[case]
    event=canonical_seed("block",900,state="ACTIVE",refs=["bos"])
    block.publish(event,"order_block",ARCHIVE,"block",predicate="FORMATION",producer_references=pinned)
    assert registry.eligible(event,900)==(case=="eligible")
    if case=="eligible":
        assert event["timing"]["dependency_references"]["bos"]==references[0]
    else:
        assert event["timing"]["eligibility_reason"]=="DEPENDENCY_UNAVAILABLE"


def test_v3_fvg_cursor_is_stale_for_reference_v1():
    from base64 import urlsafe_b64encode
    import json
    from research_backend.app.structure_pages import StaleStructureCursor

    reviewed_snapshot="458120ef2597c4be5e52143acc43abbcb7ef42e62438bfece0a4b285b0b51a23"
    cursor=urlsafe_b64encode(json.dumps([1,"fvg_formations",reviewed_snapshot,1]).encode()).decode().rstrip("=")
    rules=FVGRules(market_schedule=OPEN,history_bars=10000,history_start=36000)
    with pytest.raises(StaleStructureCursor):
        evaluate_fvg(unrecorded_rows(),300,39900,rules,FVGDelivery(formation_cursor=cursor),ARCHIVE,True)
