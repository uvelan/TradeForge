from datetime import UTC, datetime

import pytest

from research_backend.app.indicator_engine import Evaluation, evaluate


START = int(datetime(2026, 11, 2, 10, tzinfo=UTC).timestamp())
REF = (102,105,100,103)
CENTER = (103,104,102,103)
HIGH = (104,106,103,104)
LOW = (101,102,99,101)


def payload(values, *, start=START, cutoff=None, ltf="M5", step=300, rules=None, delivery=None, delays=None,
            feed="demo-v1", basis="SIMULATED"):
    candles=[]
    for index,value in enumerate(values):
        if value is None:
            continue
        stamp=start+step*index
        open_,high,low,close=value
        candles.append(dict(time=stamp,open=open_,high=high,low=low,close=close,
                            available_at=stamp+step+(delays or {}).get(index,0)))
    return dict(source="DEMO",symbol="EURUSD",timeframe=ltf,cutoff=cutoff if cutoff is not None else start+len(values)*step,
                feed_id=feed,price_basis=basis,candles=candles,
                indicators=[{"id":"candle_range_theory","crt":rules or {},"crt_delivery":delivery or {}}])


def calc(*args,**kwargs):
    return evaluate(Evaluation.model_validate(payload(*args,**kwargs)))["results"][0]


@pytest.mark.parametrize("ltf,step,frame,size,start",[
    ("M5",300,"H1",12,START),("M15",900,"H4",16,START-7200),
    ("H1",3600,"D1",24,START-36000),("M15",900,"D1",96,START-36000),
])
def test_exact_first_open_high_low_last_close(ltf,step,frame,size,start):
    values=[(101,103,100,102)]+[CENTER]*(size-2)+[(102,105,101,104)]
    result=calc(values,ltf=ltf,step=step,start=start,rules={"reference_frame":frame})
    row,=result["crt_formations"]
    assert (row["reference_frame"],row["constituents"],row["reference_ohlc"])==(frame,size,[101,105,100,104])
    assert (row["high"],row["low"],row["midpoint"],row["available_at"])==(105,100,102.5,start+size*step)
    assert result["crt_sweeps"]==[]


def test_developing_missing_unknown_and_zero_width():
    assert "DEVELOPING_REFERENCE" in calc([REF]*11)["warnings"]
    assert "MISSING_OPEN_CANDLES" in calc([REF]*5+[None]+[REF]*6,
         rules={"market_schedule":{"kind":"continuous"}})["warnings"]
    assert calc([REF]*5+[None]+[REF]*6)["crt_formations"]==[]
    assert "ZERO_WIDTH_REFERENCE" in calc([(100,100,100,100)]*12)["warnings"]
    assert "UNVERIFIED_MARKET_SCHEDULE" in calc([REF]*12)["warnings"]


@pytest.mark.parametrize("candle,rules,state",[
    (HIGH,{},"HIGH_SWEPT_RECLAIMED"),(LOW,{},"LOW_SWEPT_RECLAIMED"),
    (HIGH,{"min_penetration":1,"min_reclaim":1},"HIGH_SWEPT_RECLAIMED"),
    (HIGH,{"min_penetration":1.01},None),
    ((104,105,103,104),{},None),
    ((104,106,103,106),{},"HIGH_SWEEP_CANDIDATE"),
    ((101,102,99,99),{},"LOW_SWEEP_CANDIDATE"),
    (HIGH,{"min_reclaim":1.01},"HIGH_SWEEP_CANDIDATE"),
    ((103,106,99,104),{},"AMBIGUOUS"),
    ((106,107,105.5,106),{},"HIGH_SWEEP_CANDIDATE"),
    (CENTER,{},None),
])
def test_sweep_and_reclaim_thresholds(candle,rules,state):
    output=calc([REF]*12+[candle],rules=rules)
    if state is None:
        assert output["crt_sweeps"]==[]
    else:
        event,=output["crt_sweeps"]
        assert (event["state"],event["confirmation_time"],event["available_at"])==(state,START+3900,START+3900)
        if state.endswith("RECLAIMED"):
            assert (event["penetration"],event["reclaim_close"])==(1,candle[-1])
            assert event["swept_boundary"]==(105 if candle==HIGH else 100)
        if state=="AMBIGUOUS":
            assert "sweep_side" not in event and output["current_crt"]["items"]==[]


def test_delayed_reference_straddle_and_replay_rewind():
    values=[REF]*12+[HIGH,HIGH,(104,104,102,103),(102,103,100,101)]
    delays={11:120}
    def at(seconds):
        return calc(values,cutoff=START+seconds,delays=delays)
    assert not at(3599)["crt_formations"]
    assert not at(3719)["crt_formations"]
    release=at(3720)
    assert release["crt_formations"][0]["reference_end"]==START+3600
    assert release["crt_formations"][0]["available_at"]==START+3720
    assert not at(3900)["crt_sweeps"]
    assert not at(4199)["crt_sweeps"]
    swept,=at(4200)["crt_sweeps"]
    assert (swept["state"],swept["observation_time"])==("HIGH_SWEPT_RECLAIMED",START+3900)
    assert not at(4200)["crt_revisions"]
    assert at(4500)["crt_revisions"][0]["state"]=="EXPANSION_OBSERVED"
    assert at(4800)["crt_revisions"][-1]["state"]=="OPPOSITE_BOUNDARY_REACHED"
    assert at(3720)==release and at(4200)["crt_sweeps"]==[swept]


def test_midpoint_and_boundary_touched_in_reclaim_are_not_later_outcomes():
    values=[REF]*12+[(104,106,100,104),HIGH,(104,104,102,103)]
    result=calc(values)
    assert len(result["crt_sweeps"])==1
    assert [(e["state"],e["observation_time"]) for e in result["crt_revisions"]]==[
        ("EXPANSION_OBSERVED",START+4200)]


@pytest.mark.parametrize("more,rules,state,reason,index",[
    ([(104,106,103,106),CENTER,CENTER],{"candidate_age":2},"EXPIRED","CANDIDATE_AGE",14),
    ([CENTER,CENTER,CENTER],{"max_age":2},"EXPIRED","REFERENCE_AGE",13),
    ([HIGH,(106,108,106,107)],{},"INVALIDATED","CLOSE_BEYOND_INVALIDATION",13),
    ([HIGH,(105.5,106,105.25,105.75)],{"invalidate":"reference_boundary"},"INVALIDATED","CLOSE_BEYOND_INVALIDATION",13),
    ([HIGH,None,CENTER],{},"INVALIDATED","UNKNOWN_SCHEDULE",14),
    ([(104,106,103,106),LOW],{},"INVALIDATED","OPPOSITE_SWEEP_BEFORE_RECLAIM",13),
])
def test_exact_lifecycle_termination(more,rules,state,reason,index):
    event=calc([REF]*12+more,rules=rules)["crt_revisions"][-1]
    assert (event["state"],event["reason"],event["observation_time"])==(state,reason,START+index*300)


def test_windows_retain_distinct_live_ranges_and_replace_with_evidence():
    values=[REF]*36
    result=calc(values,rules={"max_ranges":2})
    assert result["current_crt"]["total"]==2
    assert len({event["range_id"] for event in result["crt_formations"]})==3
    replaced,=result["crt_revisions"]
    assert replaced["reason"]=="REFERENCE_CAP_REPLACEMENT"
    assert replaced["available_at"]==START+10800
    assert replaced["reference_start"]==START


def test_iana_spring_day_has_23_expected_hourly_observations():
    start=int(datetime(2026,3,8,5,tzinfo=UTC).timestamp())
    result=calc([REF]*23,start=start,step=3600,ltf="H1",rules={"daily_timezone":"America/New_York"})
    ref,=result["crt_formations"]
    assert (ref["reference_end"]-start,ref["constituents"])==(23*3600,23)


def test_forex_weekend_preserves_range_when_schedule_known():
    start=int(datetime(2026,11,6,21,tzinfo=UTC).timestamp())
    sunday=int(datetime(2026,11,8,22,tzinfo=UTC).timestamp())
    values=[REF]*12+[None]*((sunday-start)//300-12)+[CENTER]
    result=calc(values,start=start,rules={"market_schedule":{"kind":"forex_weekly"}})
    assert len(result["crt_formations"])==1
    assert result["crt_revisions"]==[]
    assert result["quality"]["SCHEDULED_CLOSURE"]==1


def test_rolling_reference_and_lifecycle_ids_hold_across_100_windows():
    values=[REF]*2100
    values[1992]=HIGH
    signature=None
    fixed=START+1980*300
    for offset in range(101):
        result=calc(values[offset:offset+2000],start=START+offset*300,
                    rules={"reference_start":fixed,"max_age":500})
        formation,=result["crt_formations"]
        sweep,=result["crt_sweeps"]
        assert formation["reference_start"]==fixed
        assert sweep["observation_time"]==START+1992*300
        ids=(formation["range_id"],formation["id"],sweep["id"])
        if signature is None:
            signature=ids
        assert ids==signature


def test_identity_corrections_delivery_and_source_scope():
    vals=[REF]*24
    before=calc(vals)
    zero=calc(vals,delivery={"formation_limit":0,"sweep_limit":0,"revision_limit":0})
    assert before["current_crt"]==zero["current_crt"]
    vals[0]=(101,105,100,103)
    after=calc(vals)
    assert before["crt_formations"][0]["id"]!=after["crt_formations"][0]["id"]
    assert before["crt_formations"][-1]["id"]==after["crt_formations"][-1]["id"]
    assert calc(vals,feed="different")["crt_formations"][-1]["id"]!=after["crt_formations"][-1]["id"]
    assert calc(vals,basis="BID")["crt_formations"][-1]["id"]!=after["crt_formations"][-1]["id"]
    cursor=calc([REF]*24,delivery={"formation_limit":1})["crt_page"]["formations"]["next_cursor"]
    from research_backend.app.structure_pages import StaleStructureCursor
    with pytest.raises(StaleStructureCursor):
        calc(vals,delivery={"formation_cursor":cursor})


@pytest.mark.parametrize("bad",["frame","duplicate","out_of_order"])
def test_api_rejects_incompatible_and_bad_constituent_order(client,bad):
    data=payload([REF]*12)
    if bad=="frame":
        data["indicators"][0]["crt"]["reference_frame"]="D1"
    if bad=="duplicate":
        data["candles"][1]["time"]=data["candles"][0]["time"]
    if bad=="out_of_order":
        data["candles"].reverse()
    assert client.post("/api/indicators/evaluate",json=data).status_code==422


def test_native_same_feed_mismatch_and_corrupt_archive(client):
    from research_backend.app import storage
    data=payload([REF]*24,rules={"history_bars":10000,"history_start":START,"reference_mode":"native"},
                 delivery={"formation_limit":1},feed="crt-native",basis="BID")
    candles=data["candles"]
    small=storage.write_partition("MT5","crt-native","EURUSD","candles","M5","BID",candles)
    htf=[dict(time=START+i*3600,open=102,high=105,low=100,close=103) for i in range(2)]
    large=storage.write_partition("MT5","crt-native","EURUSD","candles","H1","BID",htf)
    wrong=storage.write_partition("MT5","crt-native","EURUSD","candles","H1","ASK",htf)
    def manifest(*parts):
        return [{k:p[k] for k in ("id","sha256")} for p in parts]
    data.update(source="MT5_SAVED",frozen=True,candles=[],partitions=manifest(small,wrong))
    output=client.post("/api/indicators/evaluate",json=data).json()["results"][0]
    assert not output["crt_formations"]
    data.update(partitions=manifest(small,large))
    output=client.post("/api/indicators/evaluate",json=data).json()["results"][0]
    cursor=output["crt_page"]["formations"]["next_cursor"]
    assert cursor and output["provenance"]["native_reference"]["context_authority"]=="CHECKSUM_VERIFIED_ARCHIVE"
    assert output["crt_formations"][0]["reference_mode"]=="native"
    path=storage.MARKET_DIR/large["relative_path"]
    path.write_bytes(path.read_bytes()+b"corruption")
    result=client.post("/api/indicators/evaluate",json=data).json()["results"][0]
    assert result["crt_formations"]==[] and "SOURCE_INTEGRITY_FAILURE" in result["warnings"]
    data["indicators"][0]["crt_delivery"]["formation_cursor"]=cursor
    assert client.post("/api/indicators/evaluate",json=data).status_code==409


def test_native_mismatch_is_refused_and_not_silently_replaced():
    from research_backend.app.indicator_engine import EvidenceBar
    start=START
    data=payload([REF]*24,rules={"history_bars":10000,"history_start":start,"reference_mode":"native"})
    request=Evaluation.model_validate(data)
    def load(current,_start,_end,step,**_limits):
        if current.timeframe=="M5":
            rows=[(bar,bar.available_at) for bar in request.candles]
        else:
            rows=[(EvidenceBar(time=start,open=102,high=106,low=100,close=103), start+3600)]
        return {"rows":rows,"evidence":{"context_authority":"SERVER_SYNTHETIC"}}
    result=evaluate(request,load)["results"][0]
    assert result["crt_formations"]==[]
    assert result["quality"]["NATIVE_AGGREGATE_MISMATCH"]==1


def test_future_context_does_not_rewrite_earlier_crt_event():
    values=[REF]*12+[HIGH,CENTER,(106,108,106,107),(108,110,108,109)]
    earlier=calc(values,cutoff=START+3900,rules={"include_context":True})["crt_sweeps"][0]
    later=calc(values,rules={"include_context":True})["crt_sweeps"][0]
    assert earlier==later
    assert all(link["available_at"]<=earlier["available_at"] for link in earlier["context"].values())


def test_combined_indicator_request_reuses_verified_ltf_read():
    data=payload([REF]*24,rules={"history_bars":10000,"history_start":START})
    data["indicators"].extend([
        {"id":"market_structure","structure":{"history_bars":10000,"history_start":START}},
        {"id":"liquidity_sweeps","structure":{"history_bars":10000,"history_start":START}},
        {"id":"fair_value_gap","fvg":{"history_bars":10000,"history_start":START}},
        {"id":"order_blocks","structure":{"history_bars":10000,"history_start":START}},
    ])
    request=Evaluation.model_validate(data)
    calls=[]
    def history(current,start,end,step):
        calls.append((current.timeframe,start,end,step))
        return {"rows":[(bar,bar.available_at) for bar in request.candles],
                "evidence":{"context_authority":"SERVER_SYNTHETIC"}}
    results=evaluate(request,history)["results"]
    assert len(results)==5 and len(calls)==1
    assert results[0]["crt_formations"][0]["constituents"]==12


def test_delayed_reclaim_does_not_treat_straddling_dual_bar_as_later_ambiguity():
    bars=[REF]*12+[HIGH,(104,106,99,104),(104,104,102,103)]
    result=calc(bars,delays={12:120})
    first,=result["crt_sweeps"]
    assert first["available_at"]==START+4020
    assert [(event["state"],event["observation_time"]) for event in result["crt_revisions"]]==[
        ("EXPANSION_OBSERVED",START+4200)]
