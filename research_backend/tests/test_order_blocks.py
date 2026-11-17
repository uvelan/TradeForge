import pytest

from research_backend.app.indicator_engine import Evaluation, evaluate


BARS = [(9,10,8,9), (11,13,10,12), (11,12,9,10), (12,15,11,14)]
START = 1793577600
FLAT = (9,10,8,9)
ABOVE = (14,15,13,14)


def payload(values=BARS, *, times=None, cutoff=None, delays=None, rules=None, delivery=None,
            structure=None, source="DEMO", feed="fixture", basis="SIMULATED"):
    times = times if times is not None else [START+60*i for i in range(len(values))]
    delays = delays or {}
    return dict(source=source,symbol="EURUSD",timeframe="M1",
                cutoff=cutoff if cutoff is not None else times[-1]+60,
                feed_id=feed,price_basis=basis,
                candles=[dict(time=t,open=o,high=h,low=l,close=c,available_at=t+60+delays.get(i,0))
                         for i,(t,(o,h,l,c)) in enumerate(zip(times,values))],
                indicators=[dict(id="order_blocks",structure={"algorithm_version":2,"left":1,"right":1,**(structure or {})},
                                 ob=rules or {},ob_delivery=delivery or {})])


def calculate(*args, **kwargs):
    return evaluate(Evaluation.model_validate(payload(*args,**kwargs)))["results"][0]


def primary(result, kind="ob_formations"):
    return [item for item in result[kind] if item["break_time"]==START+180]


@pytest.mark.parametrize("bearish", [False,True])
@pytest.mark.parametrize("geometry", ["wick","body"])
def test_exact_mirrored_bos_links_and_candidate_geometry(bearish,geometry):
    values = [(25-o,25-l,25-h,25-c) for o,h,l,c in BARS] if bearish else BARS
    data = payload(values,rules={"geometry":geometry})
    data["indicators"].append({"id":"market_structure","structure":data["indicators"][0]["structure"]})
    block,structure = evaluate(Evaluation.model_validate(data))["results"]
    zone, = primary(block)
    bounds = (9,12) if geometry == "wick" else (10,11)
    if bearish:
        bounds = (25-bounds[1],25-bounds[0])
    assert (zone["lower"],zone["upper"],zone["candidate_time"]) == (*bounds,START+120)
    assert zone["candidate_ohlc"] == list(values[2])
    assert zone["direction"] == ("BEARISH" if bearish else "BULLISH")
    assert zone["state"] == "ACTIVE" and zone["mitigation_pct"] == 0
    assert zone["bos_id"] == structure["breaks"][0]["id"]
    assert zone["broken_pivot_id"] == structure["breaks"][0]["broken_pivot_id"]
    assert zone["first_visibility_time"] == START+240
    assert zone["break_type"] == "BOS"


@pytest.mark.parametrize("rules,key", [
    ({"min_body":1.001},"NO_ELIGIBLE_OPPOSING_CANDLE"),
    ({"min_break":1.001},"BREAK_DISTANCE"),
    ({"min_displacement":2.001},"DISPLACEMENT"),
])
def test_threshold_rejections(rules,key):
    result = calculate(rules=rules)
    assert primary(result)==[]
    assert result["rejections"][key]==1


def test_threshold_inclusivity_and_recent_qualifying_lookback():
    form, = primary(calculate(rules={"min_body":1,"min_break":1,"min_displacement":2,"lookback":1}))
    assert form["candidate_time"] == START+120
    values = BARS[:3]+[(10,12,9,11),BARS[-1]]
    result=calculate(values,rules={"lookback":2})
    assert result["ob_formations"][0]["candidate_time"]==START+120
    assert calculate(values,rules={"lookback":1})["ob_formations"]==[]
    assert calculate(values,rules={"candidate_age":1})["ob_formations"]==[]
    most_recent = BARS[:3]+[(11,12,9,10),BARS[-1]]
    assert calculate(most_recent)["ob_formations"][0]["candidate_time"]==START+180


@pytest.mark.parametrize("values", [
    BARS[:2]+[(10,12,9,11),BARS[3]],
    BARS[:2]+[(12,12.5,10,12)],
    BARS[:3]+[(12,15,11,12)],
    BARS[:2]+[BARS[-1]],
])
def test_missing_opposite_candle_or_confirmed_break(values):
    assert calculate(values,structure={"wick_candidates":True})["ob_formations"]==[]


def test_delayed_break_and_candidate_replay_cutoff():
    rules={"geometry":"body"}
    delays={2:20,3:30}
    values=BARS+[(12,14,10.75,12)]
    for cutoff in (START+100,START+200,START+269):
        assert calculate(values,cutoff=cutoff,delays=delays,rules=rules)["ob_formations"]==[]
    at=calculate(values,cutoff=START+270,delays=delays,rules=rules)
    event,=primary(at)
    assert (event["candidate_available_at"],event["bos_confirmation_time"],event["bos_available_at"])==(
        START+200,START+240,START+270)
    result=calculate(values,delays=delays,rules=rules)
    assert primary(result)==[event]
    latest,=primary(result,"ob_revisions")
    assert (latest["state"],latest["mitigation_pct"],latest["available_at"])==("PARTIALLY_MITIGATED",25,START+300)
    assert calculate(values,cutoff=START+270,delays=delays,rules=rules)==at


@pytest.mark.parametrize("later,rules,expected", [
    ([(13,14,12,13),(13,14,12,13),(13,14,11.25,13),(13,14,10.5,13),(13,14,9,11)],{},
     [("TOUCHED",0,"OBSERVED_TOUCH"),("PARTIALLY_MITIGATED",25,"OBSERVED_PENETRATION"),
      ("PARTIALLY_MITIGATED",50,"OBSERVED_PENETRATION"),("FULLY_MITIGATED",100,"OBSERVED_FULL_DEPTH")]),
    ([(13,14,11.25,13)],{"mitigation":"close"},[]),
    ([(13,14,11.25,13),(13,14,11,13),(13,14,10.5,13)],{"min_penetration_pct":50},
     [("TOUCHED",0,"OBSERVED_TOUCH"),("PARTIALLY_MITIGATED",50,"OBSERVED_PENETRATION")]),
    ([(8,8.5,7,8)],{},[("INVALIDATED",0,"GAP_THROUGH_UNOBSERVED")]),
    ([(13,14,8,8)],{},[("INVALIDATED",100,"CLOSE_BEYOND_FAR_EDGE")]),
    ([ABOVE,ABOVE],{"max_age_bars":2},[("EXPIRED",0,"MAX_AGE")]),
])
def test_lifecycle(later,rules,expected):
    events=primary(calculate(BARS+later,rules=rules),"ob_revisions")
    assert [(row["state"],row["mitigation_pct"],row["reason"]) for row in events]==expected
    assert len({row["id"] for row in events})==len(expected)
    if rules.get("max_age_bars"):
        assert events[0]["observation_time"]==START+300


def test_unverified_gap_invalidates_book_and_prevents_cross_gap_candidate():
    rows=BARS+[ABOVE]
    times=[START+60*i for i in (0,1,2,3,5)]
    assert primary(calculate(rows,times=times),"ob_revisions")[0]["reason"]=="UNKNOWN_SCHEDULE"
    assert calculate(BARS,times=[START+60*i for i in (0,1,2,4)])["ob_formations"]==[]


def test_scheduled_closure_keeps_zone_and_does_not_borrow_candidate():
    from datetime import UTC,datetime
    friday=int(datetime(2026,11,6,21,56,tzinfo=UTC).timestamp())
    sunday=int(datetime(2026,11,8,22,0,tzinfo=UTC).timestamp())
    schedule={"market_schedule":{"kind":"forex_weekly","timezone":"America/New_York",
                                "friday_close":"17:00","sunday_open":"17:00"}}
    result=calculate(BARS+[ABOVE],times=[friday+60*i for i in range(4)]+[sunday],structure=schedule)
    assert result["current_obs"]["items"][0]["state"]=="ACTIVE"
    result=calculate(BARS,times=[friday+60*i for i in (1,2,3)]+[sunday],structure=schedule)
    assert result["ob_formations"]==[] and result["rejections"]["NO_ELIGIBLE_OPPOSING_CANDLE"]==1


def test_identity_rolling_100_advances_and_delivery_invariance():
    values=[FLAT]*1900+BARS+[(13,14,11.25,13)]+[ABOVE]*195
    original=None
    for offset in range(101):
        window=values[offset:offset+2000]
        result=calculate(window,times=[START+60*i for i in range(offset,offset+len(window))],
                         delivery={"formation_limit":0,"revision_limit":0,"current_limit":1})
        zone,=result["current_obs"]["items"]
        assert (zone["break_time"],zone["mitigation_pct"])==(START+1903*60,25)
        current=(zone["ob_id"],zone["id"])
        if original is None:
            original=current
        assert current==original
    changed=calculate(BARS[:2]+[(11,12,8.5,10),BARS[-1]])
    assert primary(changed)[0]["ob_id"]!=primary(calculate())[0]["ob_id"]
    changed=calculate(BARS[:3]+[(12,15,11,14.5)])
    assert primary(changed)[0]["ob_id"]!=primary(calculate())[0]["ob_id"]


def test_context_from_future_does_not_rewrite_prior_formation():
    before,=primary(calculate(rules={"include_context":True}))
    after,=primary(calculate(BARS+[(16,17,16,17),(18,19,18,18),(20,21,20,21)],rules={"include_context":True}))
    assert before==after
    assert before["context"]["status"]=="NO_AVAILABLE_CONTEXT"
    assert len({primary(calculate())[0]["ob_id"],primary(calculate(feed="second"))[0]["ob_id"],
                primary(calculate(basis="BID"))[0]["ob_id"]})==3


def dense(count):
    values=[]
    for i in range(count//4):
        n=9+5*i
        values += [(n,n+1,n-1,n),(n+2,n+4,n+1,n+3),(n+2,n+3,n,n+1),(n+3,n+6,n+2,n+5)]
    return values


def test_dense_current_book_survives_history_and_chart_limits():
    values=dense(2000)
    first=calculate(values,rules={"max_age_bars":500},delivery={"formation_limit":2})
    cursor=first["ob_page"]["formations"]["next_cursor"]
    second=calculate(values,rules={"max_age_bars":500},delivery={
        "formation_limit":2,"formation_cursor":cursor,"revision_limit":0})
    assert first["ob_page"]["formations"]["total"]>100
    assert first["current_obs"]["total"]>12
    assert first["current_obs"]==second["current_obs"]
    assert not ({r["id"] for r in first["ob_formations"]}&{r["id"] for r in second["ob_formations"]})


def test_verified_archive_shows_block_and_rejects_cursor_after_corruption(client):
    from research_backend.app import storage
    data=payload(dense(10000),source="MT5_SAVED",feed="ob_fixture",basis="BID",
                 structure={"history_bars":10000,"history_start":START},delivery={"formation_limit":1})
    candles=data["candles"]
    chunks=[candles[:2],*(candles[i:i+2000] for i in range(2,len(candles),2000))]
    parts=[storage.write_partition("MT5","ob_fixture","EURUSD","candles","M1","BID",rows) for rows in chunks]
    data.update(frozen=True,candles=[],partitions=[{key:part[key] for key in ("id","sha256")} for part in parts])
    response=client.post("/api/indicators/evaluate",json=data)
    assert response.status_code==200,response.text
    output=response.json()["results"][0]
    assert output["provenance"]["context_authority"]=="CHECKSUM_VERIFIED_ARCHIVE"
    assert output["current_obs"]["total"]>0
    assert output["provenance"]["retrieval"]["decoded_candles"]==10000
    cursor=output["ob_page"]["formations"]["next_cursor"]
    assert cursor
    path=storage.MARKET_DIR/parts[0]["relative_path"]
    path.write_bytes(path.read_bytes()+b"invalid")
    result=client.post("/api/indicators/evaluate",json=data).json()["results"][0]
    assert result["current_obs"]["items"]==[] and "SOURCE_INTEGRITY_FAILURE" in result["warnings"]
    data["indicators"][0]["ob_delivery"]["formation_cursor"]=cursor
    assert client.post("/api/indicators/evaluate",json=data).status_code==409


def test_choch_is_explicit_and_countertrend_wick_is_never_accepted():
    closes=[10,12,10,13,11,14,12,15,13,10,11]
    values=[(close-.25,close+.25,close-.25,close) for close in closes]
    options={"price_source":"close"}
    default=calculate(values,structure=options)
    enabled=calculate(values,structure=options,rules={"allow_choch":True})
    assert default["rejections"]["CHOCH_DISABLED"]==1
    assert not any(zone["break_type"]=="CHOCH" for zone in default["ob_formations"])
    zone,=[zone for zone in enabled["ob_formations"] if zone["break_type"]=="CHOCH"]
    assert (zone["direction"],zone["break_time"],zone["candidate_time"],zone["level_price"])==(
        "BEARISH",START+540,START+480,12)


def test_equal_boundaries_are_distinct_when_bos_evidence_is_distinct():
    values=BARS+[(9,9,8,9),FLAT,FLAT]+BARS
    times=[START+60*i for i in (*range(7),*range(8,12))]
    blocks=calculate(values,times=times)["ob_formations"]
    same_bounds=[b for b in blocks if (b["lower"],b["upper"],b["direction"])==(9,12,"BULLISH")]
    assert [b["break_time"] for b in same_bounds]==[START+180,START+660]
    assert len({b["ob_id"] for b in same_bounds})==2


def test_formation_uses_only_context_available_before_or_at_bos():
    earlier=[(5,6,4,5),(6,8,6,7),(8,9,8,8)]
    values=earlier+BARS
    with_context=calculate(values,rules={"include_context":True})["ob_formations"][-1]
    without=calculate(values)["ob_formations"][-1]
    assert with_context["id"]==without["id"]
    assert with_context["break_time"]==START+360
    assert with_context["context"]["fvg"]["available_at"]<=with_context["first_visibility_time"]


def partial_dense(count):
    values=[]
    for i in range(count//5):
        n=9+5*i
        values += [(n,n+1,n-1,n),(n+2,n+4,n+1,n+3),(n+2,n+3,n,n+1),
                   (n+3,n+6,n+2,n+5),(n+5,n+5,n+2,n+5)]
    return values


def test_current_state_older_than_sixty_lifecycle_events_survives_zero_history():
    values=BARS+[tuple(price+20 for price in row) for row in partial_dense(400)]
    normal=calculate(values,rules={"max_age_bars":500},delivery={"current_limit":100})
    zero=calculate(values,rules={"max_age_bars":500},delivery={
        "current_limit":100,"formation_limit":0,"revision_limit":0})
    assert normal["ob_page"]["revisions"]["total"]>60
    assert normal["current_obs"]==zero["current_obs"]
    other=calculate(values,rules={"max_age_bars":500},delivery={
        "current_limit":100,"formation_limit":0,"revision_limit":0,
        "current_cursor":zero["current_obs"]["next_cursor"]})
    zone=next(z for z in other["current_obs"]["items"] if z["break_time"]==START+180)
    assert (zone["state"],zone["mitigation_pct"],zone["lower"],zone["upper"])==("ACTIVE",0,9,12)


def test_overlapping_bullish_and_bearish_books_are_independent():
    values=[(8,13,6,10),(18,22,18,19),(17,18,17,18),(9,11,8,10),
            (20,23,16,18),(13,13,12,12),(15,15,13,14),(16,20,14,17),
            (17,22,15,19),(19,19,15,17),(13,13,10,11),(13,15,9,11),
            (12,13,10,13),(11,14,10,13),(15,17,13,14),(17,19,17,18)]
    result=calculate(values,rules={"allow_choch":True})
    by_time={z["break_time"]:z for z in result["current_obs"]["items"]}
    long=by_time[START+15*60]
    short=by_time[START+10*60]
    assert (long["direction"],long["lower"],long["upper"],long["state"])==("BULLISH",13,17,"ACTIVE")
    assert (short["direction"],short["lower"],short["upper"],short["state"])==("BEARISH",15,22,"PARTIALLY_MITIGATED")
    assert short["remaining_lower"]==19
    assert short["mitigation_pct"]==pytest.approx(400/7)
    assert long["ob_id"]!=short["ob_id"]


def test_two_sided_wick_crossing_cannot_supply_two_confirmed_bos():
    values=[(9,11,8,9),(10,13,7,10),(10,11,9,10),(10,14,6,10)]
    data=payload(values,structure={"wick_candidates":True})
    data["indicators"].append({"id":"market_structure","structure":data["indicators"][0]["structure"]})
    blocks,structure=evaluate(Evaluation.model_validate(data))["results"]
    assert {(event["type"],event["side"]) for event in structure["breaks"]}=={
        ("WICK_CANDIDATE","HIGH"),("WICK_CANDIDATE","LOW")}
    assert blocks["ob_formations"]==[] and blocks["current_obs"]["total"]==0
