from datetime import UTC, datetime

import pytest

from research_backend.app.indicator_engine import Evaluation, evaluate
from research_backend.app.storage import write_partition


UP = [10, 12, 10, 13, 11, 14, 12, 15, 13, 10, 11]


def candle(time, price, **overrides):
    return {"time": time, "open": price, "high": price, "low": price, "close": price, **overrides}


def payload(prices, cutoff=None, step=60, rules=None, bars=None, **scope):
    return {"source": "DEMO", "symbol": "EURUSD", "timeframe": "M1" if step == 60 else "H1",
            "cutoff": cutoff if cutoff is not None else len(prices)*step,
            "candles": bars if bars is not None else [candle(i*step, price) for i, price in enumerate(prices)],
            "indicators": [{"id": "market_structure", "structure": {
                "left": 1, "right": 1, "price_source": "close", **(rules or {})}}], **scope}


def structure(prices, **options):
    return evaluate(Evaluation.model_validate(payload(prices, **options)))["results"][0]


def test_equal_high_low_classification_and_flat_state():
    assert structure([10]*8)["pivots"] == []
    assert structure([10]*8, rules={"ties":"earliest"})["structure_state"] == "UNCONFIRMED"
    result = structure([10, 12, 10, 12, 10, 12, 10])
    highs = [item for item in result["pivots"] if item["side"] == "HIGH"]
    assert [(item["pivot_time"], item["classification"]) for item in highs] == [
        (60,"UNCLASSIFIED"), (180,"EH"), (300,"EH")]
    assert result["pivots"][-2]["classification"] == "EL"
    assert result["structure_state"] == "NEUTRAL"


def test_plateau_policies_and_instrument_normalized_tolerance():
    prices = [10, 12, 12.005, 10, 11]
    assert [item["pivot_time"] for item in structure(prices)["pivots"] if item["side"]=="HIGH"] == [120]
    assert [item for item in structure(prices, rules={"tolerance_bps":5})["pivots"]
            if item["side"]=="HIGH"] == []
    allowed = structure(prices, rules={"tolerance_bps":5,"ties":"earliest"})["pivots"][0]
    assert (allowed["pivot_time"],allowed["price"],allowed["available_at"]) == (60,12,180)


@pytest.mark.parametrize("step", [60, 3600])
def test_pivot_replay_at_and_before_actual_confirmation(step):
    for cutoff in (step,2*step,3*step-1):
        assert structure([10,12,10], step=step, cutoff=cutoff)["pivots"] == []
    first = structure([10,12,10], step=step,cutoff=3*step)["pivots"][0]
    assert (first["pivot_time"],first["confirmation_bar_time"],first["confirmation_time"],
            first["available_at"],first["price"],first["status"]) == (step,2*step,3*step,3*step,12,"confirmed")
    assert first["algorithm_version"]==1 and len(first["input_fingerprint"])==64


def test_delayed_right_evidence_and_incomplete_candle():
    bars=[candle(0,10),candle(60,12),candle(120,10,available_at=260)]
    assert not structure([],bars=bars,cutoff=259)["pivots"]
    confirmed=structure([],bars=bars,cutoff=260)["pivots"][0]
    assert confirmed["confirmation_time"]==180 and confirmed["available_at"]==260
    late=structure([],bars=bars+[candle(180,11)],cutoff=300)
    assert late["pivots"]==[]
    assert "LATE_STRUCTURE_CANDLE_IGNORED" in late["warnings"]
    assert not structure([],bars=[*bars[:2],{**bars[-1],"closed":False}],cutoff=400)["pivots"]


def test_gap_clears_reference_and_ambiguous_double_pivots():
    gap=structure([],bars=[candle(0,10),candle(60,12),candle(180,10)],cutoff=240)
    assert gap["pivots"]==[] and "STRUCTURE_RESET_AT_GAP" in gap["warnings"]
    double=structure([],bars=[candle(0,10),candle(60,10,high=12,low=8),candle(120,10)],
                     cutoff=180,rules={"price_source":"wick"})
    assert {(item["side"],item["price"],item["available_at"]) for item in double["pivots"]} == {
        ("HIGH",12,180),("LOW",8,180)}
    assert double["structure_state"]=="UNCONFIRMED"
    assert not double["breaks"]


@pytest.mark.parametrize(("prices","trend","continuation_level","opposite","protected"),[
    (UP,"BULLISH",14,"BEARISH",12),
    ([30-p for p in UP],"BEARISH",16,"BULLISH",18),
])
def test_independent_bos_choch_and_rewind(prices,trend,continuation_level,opposite,protected):
    before=structure(prices,cutoff=479)
    at_bos=structure(prices,cutoff=480)
    assert before["breaks"]==at_bos["breaks"][:-1]
    bos=at_bos["breaks"][-1]
    assert (bos["type"],bos["direction"],bos["break_time"],bos["level_price"],
            bos["break_price"],bos["available_at"],bos["state_before"],bos["state_after"]) == (
                "BOS",trend,420,continuation_level,prices[7],480,trend,trend)
    prior=next(p for p in at_bos["pivots"] if p["id"]==bos["broken_pivot_id"])
    assert (prior["pivot_time"],prior["confirmation_time"],prior["available_at"])==(300,420,420)
    assert bos["level_available_at"] < bos["available_at"]
    before_choch=structure(prices,cutoff=599)
    at_choch=structure(prices,cutoff=600)
    assert before_choch["breaks"]==at_choch["breaks"][:-1]
    event=at_choch["breaks"][-1]
    assert (event["type"],event["direction"],event["break_time"],event["level_price"],
            event["confirmation_time"],event["available_at"],event["state_after"])==(
                "CHOCH",opposite,540,protected,600,600,"NEUTRAL")
    assert at_choch["breaks"][0]["state_before"]=="UNCONFIRMED"
    assert at_choch["pivots"]==structure(prices)["pivots"]
    assert structure(prices,cutoff=479)==before


def test_wick_false_break_then_close_once_respects_tolerance():
    bars=[candle(0,10),candle(60,12),candle(120,11),candle(180,11.9,high=12.5),
          candle(240,11.95),candle(300,12.04),candle(360,12.2),candle(420,12.3)]
    rules={"wick_candidates":True,"break_bps":50}
    result=structure([],bars=bars,cutoff=480,rules=rules)
    wick,reclaim,bos=result["breaks"]
    assert (wick["type"],wick["status"],wick["break_price"],wick["break_time"])==(
        "WICK_CANDIDATE","provisional",12.5,180)
    assert (reclaim["type"],reclaim["status"],reclaim["candidate_id"])==(
        "WICK_INVALIDATED","invalidated",wick["id"])
    assert (bos["type"],bos["break_time"],bos["break_price"],bos["level_price"])==("BOS",360,12.2,12)
    assert not structure([],bars=bars,cutoff=359,rules=rules)["breaks"][-1]["status"]=="confirmed"


def test_wick_countertrend_candidate_requires_protected_close():
    bars=[candle(i*60,p) for i,p in enumerate(UP[:9])]
    bars.append(candle(540,13.5,low=11))
    result=structure([],bars=bars,cutoff=600,rules={"wick_candidates":True})
    assert (result["breaks"][-1]["type"],result["breaks"][-1]["state_after"])==(
        "COUNTERTREND_CANDIDATE","BULLISH")
    assert all(row["type"]!="CHOCH" for row in result["breaks"])


def test_reversed_duplicate_and_same_availability_are_conservative(client):
    base=payload([10,12,10,13])
    reversed_rows=list(reversed(base["candles"]))
    for bars in (reversed_rows,[base["candles"][0]]*2):
        assert client.post("/api/indicators/evaluate",json={**base,"candles":bars}).status_code==422
    base["candles"][2]["available_at"]=240
    result=client.post("/api/indicators/evaluate",json=base).json()["results"][0]
    assert result["pivots"][0]["available_at"]==240
    assert result["breaks"]==[]


def test_distinct_sources_and_client_authority(client):
    body=payload(UP,source="MT5_SAVED",feed_id="broker",price_basis="BID")
    a=client.post("/api/indicators/evaluate",json=body).json()
    b=client.post("/api/indicators/evaluate",json={**body,"source":"DUKASCOPY_SAVED","feed_id":"duk"}).json()
    assert a["authority"]==b["authority"]=="CLIENT_SUPPLIED_UNVERIFIED"
    p,q=(result["results"][0] for result in (a,b))
    assert p["pivots"][0]["price"]==q["pivots"][0]["price"]==12
    assert p["pivots"][0]["id"]!=q["pivots"][0]["id"]
    assert p["provenance"]["source"]=="MT5_SAVED" and q["provenance"]["source"]=="DUKASCOPY_SAVED"
    assert p["pivots"][0]["provenance_id"]==p["provenance_id"]


def test_fixed_extended_history_with_verified_partitions(client):
    start=int(datetime(2026,11,2,tzinfo=UTC).timestamp())
    bars=[candle(start+i*60,UP[i % len(UP)]) for i in range(10_000)]
    manifest=[]
    for offset in range(0,len(bars),2000):
        row=write_partition("MT5","fixture","EURUSD","candles","M1","BID",bars[offset:offset+2000])
        manifest.append({"id":row["id"],"sha256":row["sha256"]})
    body=payload([],cutoff=start+10_000*60,source="MT5_SAVED",price_basis="BID",feed_id="fixture",
                 partitions=manifest,frozen=True,rules={"history_bars":10_000,"history_start":start})
    response=client.post("/api/indicators/evaluate",json=body).json()
    result=response["results"][0]
    assert result["provenance"]["context_authority"]=="CHECKSUM_VERIFIED_ARCHIVE"
    assert result["provenance"]["retrieval"]["decoded_candles"]==10_000
    assert result["provenance"]["schedule_completeness"]=="NOT_ESTABLISHED"
    # Walk all bounded pages to verify the original complete-history assertion.
    earlier=client.post("/api/indicators/evaluate",json={**body,"cutoff":start+600}).json()["results"][0]
    page=result
    full_pivots=[]
    oldest_break=None
    while True:
        full_pivots[0:0]=page["pivots"]
        if page["breaks"]:
            oldest_break=page["breaks"][0]
        metadata=page["event_page"]
        pivot_cursor=metadata["pivots"]["next_cursor"]
        break_cursor=metadata["breaks"]["next_cursor"]
        if not (pivot_cursor or break_cursor):
            break
        spec={**body["indicators"][0],"structure_delivery":{
            "pivot_limit":500 if pivot_cursor else 0,"break_limit":500 if break_cursor else 0,
            "pivot_cursor":pivot_cursor,"break_cursor":break_cursor}}
        status=client.post("/api/indicators/evaluate",json={**body,"indicators":[spec]})
        assert status.status_code==200
        page=status.json()["results"][0]
    assert oldest_break["break_time"]==start+180
    assert earlier["pivots"]==[p for p in full_pivots if p["available_at"] <= start+600]
    assert client.post("/api/indicators/evaluate",json={**body,"cutoff":start+10_001*60}).status_code==422

def test_unprotected_opposing_close_is_countertrend_not_choch():
    bars=[candle(i*60,price) for i,price in enumerate(UP[:8])]
    bars += [candle(480,13,low=11),candle(540,14),candle(600,10.5)]
    result=structure([],bars=bars,cutoff=660,rules={"price_source":"wick"})
    broken=result["breaks"][-1]
    assert (broken["type"],broken["level_price"],broken["break_time"],broken["state_before"],
            broken["state_after"]) == ("COUNTERTREND_BREAK",11,600,"BULLISH","NEUTRAL")
    level=next(item for item in result["pivots"] if item["id"]==broken["broken_pivot_id"])
    assert (level["classification"],level["pivot_time"],level["available_at"]) == ("LL",480,600)
