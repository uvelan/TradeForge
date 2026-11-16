import pytest

from research_backend.app.indicator_engine import Evaluation, evaluate

UP = [10, 12, 10, 12, 10]


def candles(values=UP):
    return [dict(time=i*60,open=p,high=p,low=p,close=p) for i,p in enumerate(values)]


def body(rows, *, cutoff=None, liquidity=None, delivery=None, structure=None, **scope):
    spec={"id":"liquidity_sweeps","structure":{"left":1,"right":1,"price_source":"close",**(structure or {})},
          "liquidity":{"min_separation_bars":1,**(liquidity or {})}}
    if delivery:
        spec["liquidity_delivery"]=delivery
    return {"source":"DEMO","symbol":"EURUSD","timeframe":"M1","candles":rows,
            "cutoff":cutoff if cutoff is not None else rows[-1]["time"]+60,"indicators":[spec],**scope}


def result(rows,**options):
    return evaluate(Evaluation.model_validate(body(rows,**options)))["results"][0]


@pytest.mark.parametrize(("prices","side","classification","price","cross"),[
    (UP,"BSL","EQH",12,{"open":11,"high":13,"low":11,"close":11}),
    ([30-x for x in UP],"SSL","EQL",18,{"open":19,"high":19,"low":17,"close":19}),
])
def test_exact_activation_and_same_candle_sweep_replay(prices,side,classification,price,cross):
    data=candles(prices)+[{"time":300,**cross}]
    prior=result(data,cutoff=299)
    assert [(p["state"],p["available_at"]) for p in prior["pools"] if p["liquidity_side"]==side]==[("FORMING",180)]
    armed=result(data,cutoff=300)
    assert armed["sweeps"]==[]
    pool=next(p for p in armed["pools"] if p["liquidity_side"]==side and p["state"]=="ACTIVE")
    assert (pool["classification"],pool["lower"],pool["upper"],pool["activated_at"],pool["touches"])==(
        classification,price,price,300,2)
    assert [(p["time"],p["available_at"]) for p in pool["constituents"]]==[(60,180),(180,300)]
    current=result(data,cutoff=360)
    event=current["sweeps"][0]
    assert (event["pool_id"],event["state"],event["observation_time"],event["confirmation_time"],
            event["available_at"],event["ohlc"])==(pool["pool_id"],"SWEPT",300,360,360,cross)
    assert result(data,cutoff=359)["sweeps"]==[]
    assert result(data,cutoff=299)==prior
    assert result(data,cutoff=360)==current
    assert "signal" not in event and "volume" not in event


@pytest.mark.parametrize(("price","active"),[(12.012,True),(12.01201,False)])
def test_inclusive_bps_against_first_pivot(price,active):
    data=result(candles([10,12,10,price,10]),liquidity={"equality_bps":10})
    highs=[p for p in data["pools"] if p["liquidity_side"]=="BSL"]
    assert any(p["state"]=="ACTIVE" for p in highs)==active
    if active:
        assert highs[-1]["upper"]==price and highs[-1]["lower"]==12


def test_extreme_close_break_candidate_and_opening_gap():
    base=candles()
    for close,state in ((12,"SWEEP_CANDIDATE"),(12.2,"BROKEN"),(11.9,"SWEPT")):
        event=result(base+[dict(time=300,open=12.5,high=13,low=11,close=close)])["sweeps"][0]
        assert (event["state"],event["opening_crossed"],event["available_at"])==(state,True,360)
    pending=base+[dict(time=300,open=11,high=13,low=11,close=11,closed=False)]
    assert result(pending)["sweeps"]==[]
    delayed=base+[dict(time=300,open=11,high=13,low=11,close=11,available_at=400)]
    assert result(delayed,cutoff=399)["sweeps"]==[]
    assert result(delayed,cutoff=400)["sweeps"][0]["available_at"]==400


def test_pivot_delay_touches_revisions_and_expiration():
    rows=candles([10,12,10,12,10,12,10,11])
    value=result(rows,liquidity={"max_age_bars":5})
    highs=[p for p in value["pools"] if p["liquidity_side"]=="BSL"]
    assert [(p["revision"],p["state"],p["touches"],p["available_at"]) for p in highs]==[
        (1,"FORMING",1,180),(2,"ACTIVE",2,300),(3,"ACTIVE",3,420),(4,"EXPIRED",3,480)]
    assert len({p["pool_id"] for p in highs})==1 and len({p["id"] for p in highs})==4
    delayed=candles()
    delayed[-1]["available_at"]=330
    assert all(p["state"]!="ACTIVE" for p in result(delayed,cutoff=329)["pools"])
    assert next(p for p in result(delayed,cutoff=330)["pools"] if p["state"]=="ACTIVE")["available_at"]==330
    assert all(p["state"]!="ACTIVE" for p in result(rows,liquidity={"min_touches":4})["pools"])


def test_weekend_continuity_and_unknown_gap_invalidate_pools():
    friday=1794002400  # 2026-11-06 22:00 UTC
    sunday=friday+48*3600
    rows=[{**row,"time":friday-300+row["time"]} for row in candles()]
    rows.append(dict(time=sunday,open=11,high=13,low=11,close=11))
    allowed=result(rows,structure={"algorithm_version":2,"market_schedule":{"kind":"forex_weekly"}})
    assert allowed["sweeps"][0]["state"]=="SWEPT"
    reset=result(rows)
    assert reset["sweeps"]==[] and any(p["state"]=="INVALIDATED" for p in reset["pools"])
    rows.pop(2)
    assert not result(rows,structure={"algorithm_version":2,"market_schedule":{"kind":"forex_weekly"}})["sweeps"]


def test_two_distinct_pools_can_sweep_in_one_candle_and_page(client):
    rows=candles([10,12,10,14,10,12,10,12,10])+[dict(time=540,open=11,high=15,low=11,close=11)]
    rows[7]["high"]=14
    options={"liquidity":{"equality_bps":0},"structure":{"price_source":"wick"}}
    complete=result(rows,**options)
    hits=[event for event in complete["sweeps"] if event["state"]=="SWEPT"]
    assert sorted((s["upper"],s["available_at"],s["state"]) for s in hits)==[(12,600,"SWEPT"),(14,600,"SWEPT")]
    page=result(rows,delivery={"sweep_limit":1,"pool_limit":0},**options)
    cursor=page["liquidity_page"]["sweeps"]["next_cursor"]
    old=result(rows,delivery={"sweep_limit":1,"sweep_cursor":cursor},**options)
    assert old["sweeps"]+page["sweeps"]==hits
    changed=[*rows[:-1],{**rows[-1],"high":16}]
    assert client.post("/api/indicators/evaluate",json=body(changed,
        delivery={"sweep_cursor":cursor},**options)).status_code==409


def test_duplicate_candle_rejected_and_simultaneous_pivots_do_not_count_twice(client):
    rows=[dict(time=0,open=10,high=10,low=10,close=10),
          dict(time=60,open=10,high=12,low=8,close=10),
          dict(time=120,open=10,high=10,low=10,close=10)]
    value=result(rows,structure={"price_source":"wick"})
    assert {p["classification"] for p in value["pools"]}=={"EQH","EQL"}
    assert all(p["state"]=="FORMING" and p["touches"]==1 for p in value["pools"])
    assert client.post("/api/indicators/evaluate",json=body([rows[0],rows[0]])).status_code==422


def test_bos_and_choch_are_not_relabelled_as_sweeps():
    prices=[10,12,10,13,11,14,12,15,13,10,11]
    rows=candles(prices)
    data=body(rows)
    data["indicators"].append({"id":"market_structure","structure":{"left":1,"right":1,"price_source":"close"}})
    liquidity,structure=evaluate(Evaluation.model_validate(data))["results"]
    assert liquidity["sweeps"]==[]
    assert [e["type"] for e in structure["breaks"]]==["BOS","BOS","BOS","CHOCH"]
    rows=candles()+[dict(time=300,open=11,high=13,low=11,close=11),dict(time=360,open=13,high=13,low=13,close=13)]
    data=body(rows)
    data["indicators"].append({"id":"market_structure","structure":{"left":1,"right":1,"price_source":"close"}})
    liquidity,structure=evaluate(Evaluation.model_validate(data))["results"]
    assert liquidity["sweeps"][0]["state"]=="SWEPT"
    assert liquidity["sweeps"][0]["related_structure_ids"]==[]
    assert structure["breaks"][-1]["type"]=="BOS" and structure["breaks"][-1]["available_at"]==420


def test_flat_and_dense_results_are_bounded():
    assert result(candles([10]*120))["pools"]==[]
    full=result(candles(([10,12,10,12,10,13,10,11]*250)),delivery={"pool_limit":3,"sweep_limit":2})
    assert full["liquidity_page"]["pools"]["total"]>3
    assert len(full["pools"])==3 and len(full["sweeps"])==2

def test_break_reference_and_explicit_tolerances():
    rows=candles()+[dict(time=300,open=12.1,high=13,low=12,close=12.1)]
    payload=body(rows)
    payload["indicators"].append({"id":"market_structure","structure":{"left":1,"right":1,"price_source":"close"}})
    liquidity,structure=evaluate(Evaluation.model_validate(payload))["results"]
    assert liquidity["sweeps"][0]["state"]=="BROKEN"
    assert liquidity["sweeps"][0]["related_structure_ids"]==[structure["breaks"][-1]["id"]]
    def at(high,close):
        return result(candles()+[dict(time=300,open=11.9,high=high,low=close,close=close)],
                      liquidity={"penetration_bps":100,"reclaim_bps":100})["sweeps"]
    assert at(12.12,11.8)==[]
    assert at(12.13,11.88)[0]["state"]=="SWEEP_CANDIDATE"
    assert at(12.13,11.87)[0]["state"]=="SWEPT"


def test_liquidity_archive_integrity_and_source_correction(client):
    from research_backend.app import storage

    start=1793577600
    rows=candles()+[dict(time=300,open=11,high=13,low=11,close=11)]
    rows=[{**row,"time":row["time"]+start} for row in rows]
    part=storage.write_partition("MT5","liq_fixture","EURUSD","candles","M1","BID",rows)
    data=body([],cutoff=start+360,source="MT5_SAVED",feed_id="liq_fixture",price_basis="BID",frozen=True,
              partitions=[{"id":part["id"],"sha256":part["sha256"]}],
              structure={"history_bars":10000,"history_start":start},delivery={"pool_limit":1})
    response=client.post("/api/indicators/evaluate",json=data).json()
    current=response["results"][0]
    assert current["provenance"]["context_authority"]=="CHECKSUM_VERIFIED_ARCHIVE"
    assert response["authority"]=="CLIENT_SUPPLIED_UNVERIFIED"
    assert current["sweeps"][0]["state"]=="SWEPT"
    path=storage.MARKET_DIR/part["relative_path"]
    path.write_bytes(path.read_bytes()+b"corrupt")
    failed=client.post("/api/indicators/evaluate",json=data).json()["results"][0]
    assert "SOURCE_INTEGRITY_FAILURE" in failed["warnings"]
    assert not failed["sweeps"] and not failed["pools"]
    data["indicators"][0]["liquidity_delivery"]["pool_cursor"]=current["liquidity_page"]["pools"]["next_cursor"]
    assert client.post("/api/indicators/evaluate",json=data).status_code==409

def test_overlapping_bands_assign_one_confirmed_pivot_to_nearest_anchor():
    rows=candles([10]*7)
    for i,high in ((1,12),(3,12.018),(5,12.010)):
        rows[i]["high"]=high
    data=result(rows,structure={"price_source":"wick"},liquidity={"equality_bps":10})
    high=[p for p in data["pools"] if p["liquidity_side"]=="BSL"]
    armed=[p for p in high if p["state"]=="ACTIVE"]
    assert len(armed)==1
    assert [p["price"] for p in armed[0]["constituents"]]==[12.018,12.010]
    assert (armed[0]["lower"],armed[0]["upper"],armed[0]["available_at"])==(12.01,12.018,420)
    assert len({p["pool_id"] for p in high})==2


def test_later_reclaim_requires_its_own_penetration():
    rows=candles()+[dict(time=300,open=12,high=13,low=12,close=12),
                    dict(time=360,open=11,high=11,low=11,close=11),
                    dict(time=420,open=11,high=13,low=11,close=11)]
    events=result(rows)["sweeps"]
    assert [(event["state"],event["available_at"]) for event in events]==[
        ("SWEEP_CANDIDATE",360),("SWEPT",480)]
    assert events[-1]["candidate_id"]==events[0]["id"]
    assert result(rows,cutoff=419)["sweeps"]==events[:1]
