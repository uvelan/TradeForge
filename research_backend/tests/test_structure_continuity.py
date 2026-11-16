from datetime import UTC, datetime

import pytest

from research_backend.app import storage
from research_backend.app.indicator_engine import Evaluation, evaluate
from research_backend.app.indicator_coverage import MarketSchedule
from research_backend.app.structure_continuity import continuity_reason

UP = [10, 12, 10, 13, 11, 14, 12, 15, 13, 10, 11]


def stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


FRIDAY = stamp("2026-11-06T22:00")
SUNDAY = stamp("2026-11-08T22:00")
NY = {"kind":"forex_weekly"}


def bar(time, price, **extra):
    return dict(time=time,open=price,high=price,low=price,close=price) | extra


def weekend(prices, split):
    return [bar(FRIDAY+(index-split)*60 if index<split else SUNDAY+(index-split)*60, price)
            for index, price in enumerate(prices)]


def request(candles, version=2, schedule=NY, cutoff=None, delivery=None, **scope):
    rules={"left":1,"right":1,"price_source":"close","algorithm_version":version,**scope.pop("rules",{})}
    if version == 2 and schedule is not None:
        rules["market_schedule"] = schedule
    spec={"id":"market_structure","structure":rules}
    if delivery is not None:
        spec["structure_delivery"]=delivery
    return dict(source="DEMO",symbol="EURUSD",timeframe="M1",candles=candles,
                cutoff=cutoff if cutoff is not None else candles[-1]["time"]+60,
                indicators=[spec]) | scope


def result(candles, **options):
    return evaluate(Evaluation.model_validate(request(candles, **options)))["results"][0]


@pytest.mark.parametrize(("close","reopen","zone"),[
    ("2026-03-06T22:00","2026-03-08T21:00","America/New_York"),
    ("2026-10-30T21:00","2026-11-01T22:00","America/New_York"),
    ("2026-03-27T17:00","2026-03-29T16:00","Europe/London"),
])
def test_calendar_dst_and_unexpected_open_minute(close,reopen,zone):
    close, reopen = stamp(close), stamp(reopen)
    schedule=MarketSchedule(kind="forex_weekly",timezone=zone)
    assert continuity_reason(close-60,reopen,60,schedule)=="SCHEDULED_CLOSURE"
    assert continuity_reason(close-120,reopen,60,schedule)=="MISSING_OPEN_CANDLES"
    assert continuity_reason(close-60,reopen+60,60,schedule)=="MISSING_OPEN_CANDLES"


@pytest.mark.parametrize(("prices","split","kind","direction","level","close"),[
    (UP,7,"BOS","BULLISH",14,15),
    ([30-x for x in UP],7,"BOS","BEARISH",16,15),
    (UP,9,"CHOCH","BEARISH",12,10),
    ([30-x for x in UP],9,"CHOCH","BULLISH",18,20),
])
def test_reopening_uses_previously_confirmed_reference(prices,split,kind,direction,level,close):
    candles=weekend(prices,split)
    before=result(candles,cutoff=SUNDAY+59)
    assert not before["continuity"]["recent"]
    now=result(candles,cutoff=SUNDAY+60)
    event=now["breaks"][-1]
    assert before["breaks"]==now["breaks"][:-1]
    assert (event["type"],event["direction"],event["level_price"],event["break_price"],
            event["break_time"],event["confirmation_time"],event["available_at"])==(
                kind,direction,level,close,SUNDAY,SUNDAY+60,SUNDAY+60)
    assert event["opening_status"]=="CLOSE_CONFIRMED"
    assert event["level_available_at"] < event["available_at"]
    assert now["continuity"]["schedule_authority"]=="CONFIGURED_ASSUMPTION"
    gap=now["continuity"]["recent"][0]
    assert (gap["previous_time"],gap["time"],gap["available_at"],gap["state_preserved"])==(
        FRIDAY-60,SUNDAY,SUNDAY+60,True)
    assert result(candles,cutoff=SUNDAY+59)==before
    legacy=result(candles,version=1,cutoff=SUNDAY+60)
    assert legacy["structure_state"]=="UNCONFIRMED"
    assert all(item["break_time"]!=SUNDAY for item in legacy["breaks"])


def test_equal_elapsed_weekday_gap_and_unknown_schedule_reset():
    candles=weekend(UP,7)
    # Translate by three days. The same elapsed interval now falls within open-market hours.
    shifted=[dict(row,time=row["time"]+3*86_400) for row in candles]
    data=result(shifted,cutoff=SUNDAY+3*86_400+60)
    assert data["continuity"]["counts"]=={"MISSING_OPEN_CANDLES":1}
    assert data["structure_state"]=="UNCONFIRMED"
    continuous=result(candles,schedule={"kind":"continuous"},cutoff=SUNDAY+60)
    assert continuous["continuity"]["counts"]=={"MISSING_OPEN_CANDLES":1}
    unknown=result(candles,schedule=None,cutoff=SUNDAY+60)
    assert unknown["continuity"]["counts"]=={"UNKNOWN_SCHEDULE":1}
    missing=candles[:6]+candles[7:]
    missed=result(missing,cutoff=SUNDAY+60)
    assert missed["continuity"]["counts"]=={"MISSING_OPEN_CANDLES":1}
    assert all(item["break_time"]!=SUNDAY for item in missed["breaks"])


def test_open_gap_and_wick_reclaim_do_not_confirm():
    candles=weekend(UP[:8],7)
    candles[-1]=bar(SUNDAY,13.5,open=15,high=15,low=13)
    value=result(candles,rules={"wick_candidates":True})
    event=value["breaks"][-1]
    assert (event["status"],event["type"],event["break_price"],event["opening_status"])==(
        "provisional","WICK_CANDIDATE",15,"OPEN_RECLAIMED")
    crossing=next(c for c in value["continuity"]["recent"][0]["opening"]["crossings"] if c["side"]=="HIGH")
    assert (crossing["open_crossed"],crossing["wick_crossed"],crossing["close_crossed"])==(True,True,False)
    candles[-1]=bar(SUNDAY,13.5,open=14,high=15,low=13)
    assert result(candles,rules={"wick_candidates":True})["breaks"][-1]["opening_status"]=="WICK_ONLY"


def test_pivot_spans_only_real_closed_observations_with_delayed_confirmation():
    candles=weekend([10,12,10],2)
    candles[-1]["available_at"]=SUNDAY+120
    before=result(candles,cutoff=SUNDAY+119)
    assert before["pivots"]==[] and before["continuity"]["recent"]==[]
    pivot=result(candles,cutoff=SUNDAY+120)["pivots"][0]
    assert (pivot["pivot_time"],pivot["price"],pivot["confirmation_bar_time"],pivot["confirmation_time"],
            pivot["available_at"])==(FRIDAY-60,12,SUNDAY,SUNDAY+60,SUNDAY+120)


def test_v1_prior_commit_golden_ids_stay_identical():
    candles=[bar(i*60, price) for i,price in enumerate(UP)]
    previous=result(candles,version=1)
    assert previous["pivots"][0]["id"]=="017e8b0fddcdfce6cd8bd45d16bc5b4e2326069286e738dae8ca61b0d358c78d"
    assert previous["breaks"][-1]["id"]=="1937fddb43dfecb32862c901bafe7d47f2ead779f0efb0a1227ce60c3f09c13d"
    assert "continuity" not in previous


def test_independent_paging_same_timestamp_retry_and_stale_cursor(client):
    candles=[bar(0,10),bar(60,10,high=12,low=8),bar(120,10)]
    args={"rules":{"price_source":"wick"}}
    latest=result(candles,delivery={"pivot_limit":1,"break_limit":0},**args)
    cursor=latest["event_page"]["pivots"]["next_cursor"]
    older=result(candles,delivery={"pivot_limit":1,"pivot_cursor":cursor,"break_limit":0},**args)
    assert latest["pivots"][0]["side"]=="LOW" and older["pivots"][0]["side"]=="HIGH"
    assert older["pivots"][0]["available_at"]==latest["pivots"][0]["available_at"]==180
    assert older["event_page"]["pivots"]["next_cursor"] is None
    assert older==result(candles,delivery={"pivot_limit":1,"pivot_cursor":cursor,"break_limit":0},**args)
    assert [*older["pivots"],*latest["pivots"]]==result(candles,**args)["pivots"]
    for change in ({"candles":[candles[0],bar(60,10,high=13,low=8),candles[-1]]},{"cutoff":181},{"symbol":"GBPUSD"}):
        payload=request(candles,delivery={"pivot_cursor":cursor},**args)|change
        assert client.post("/api/indicators/evaluate",json=payload).status_code==409
    assert client.post("/api/indicators/evaluate",json=request(candles,delivery={"break_cursor":cursor},**args)).status_code==422


def test_verified_weekend_archive_remains_bounded_and_detects_corruption(client):
    candles=weekend(UP,7)
    start=candles[0]["time"]
    parts=[storage.write_partition("MT5","fixture","EURUSD","candles","M1","BID",bars)
           for bars in (candles[:7],candles[7:])]
    manifest=[{"id":part["id"],"sha256":part["sha256"]} for part in parts]
    payload=request([],cutoff=SUNDAY+60,source="MT5_SAVED",feed_id="fixture",price_basis="BID",
                    frozen=True,partitions=manifest,rules={"history_bars":10_000,"history_start":start},
                    delivery={"pivot_limit":1})
    response=client.post("/api/indicators/evaluate",json=payload).json()["results"][0]
    assert response["provenance"]["context_authority"]=="CHECKSUM_VERIFIED_ARCHIVE"
    assert response["continuity"]["counts"]=={"SCHEDULED_CLOSURE":1}
    assert response["breaks"][-1]["break_time"]==SUNDAY
    cursor=response["event_page"]["pivots"]["next_cursor"]
    path=storage.MARKET_DIR / parts[0]["relative_path"]
    path.write_bytes(path.read_bytes()+b"corrupted")
    failure=client.post("/api/indicators/evaluate",json=payload).json()["results"][0]
    assert "SOURCE_INTEGRITY_FAILURE" in failure["warnings"] and failure["breaks"]==[]
    payload["indicators"][0]["structure_delivery"]["pivot_cursor"]=cursor
    assert client.post("/api/indicators/evaluate",json=payload).status_code==409


def test_dense_archive_more_than_ten_thousand_events(client):
    start=stamp("2026-11-02T00:00")
    candles=[bar(start+i*60,UP[i%len(UP)]) for i in range(10_000)]
    for i in range(0,len(candles),2000):
        storage.write_partition("MT5","dense","EURUSD","candles","M1","BID",candles[i:i+2000])
    body=request([],cutoff=start+600_000,source="MT5_SAVED",feed_id="dense",price_basis="BID",
        rules={"history_bars":10_000,"history_start":start},delivery={"pivot_limit":2,"break_limit":3})
    latest=client.post("/api/indicators/evaluate",json=body).json()["results"][0]
    page=latest["event_page"]
    assert (page["pivots"]["total"],page["breaks"]["total"])==(8180,4544)
    assert len(latest["pivots"])==2 and len(latest["breaks"])==3
    body["indicators"][0]["structure_delivery"]={"pivot_limit":3,"break_limit":4,
        "pivot_cursor":page["pivots"]["next_cursor"],"break_cursor":page["breaks"]["next_cursor"]}
    body=Evaluation.model_validate(body).model_dump()
    older=client.post("/api/indicators/evaluate",json=body).json()["results"][0]
    assert older["structure_state"]==latest["structure_state"]
    assert older["event_page"]["snapshot"]==page["snapshot"]
    assert not {row["id"] for row in older["pivots"]}&{row["id"] for row in latest["pivots"]}
    assert not {row["id"] for row in older["breaks"]}&{row["id"] for row in latest["breaks"]}
