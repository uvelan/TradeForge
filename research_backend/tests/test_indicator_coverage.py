from datetime import UTC, datetime
from pathlib import Path

import pytest

from research_backend.app import storage
from research_backend.app.indicator_coverage import DAY, WEEK, MarketSchedule, open_intervals
from research_backend.app.indicator_engine import Evaluation, evaluate
from research_backend.app.indicator_history import load_reference


def utc(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


MONDAY = utc("2026-11-02")
HOUR = 3600
FRIDAY_CLOSE = utc("2026-11-06T22:00")
SUNDAY_REOPEN = utc("2026-11-08T22:00")
NEXT_MONDAY = MONDAY + WEEK


def bar(stamp, high=12, low=9, **extra):
    return dict(time=stamp, open=10, close=10, high=high, low=low, **extra)


def forex(step=HOUR):
    stamps = [*range(MONDAY, FRIDAY_CLOSE, step), *range(SUNDAY_REOPEN, NEXT_MONDAY, step)]
    return [bar(stamp, 14 if stamp == SUNDAY_REOPEN else 12,
                8 if stamp == FRIDAY_CLOSE-step else 9) for stamp in stamps]


def request(candles, *, cutoff=NEXT_MONDAY+HOUR, timeframe="H1", schedule="forex_weekly",
            source="MT5_SAVED", kind="previous_week", **extra):
    spec = {"id": kind}
    if schedule is not None:
        spec["schedule"] = {"kind": schedule}
    return dict(candles=candles, cutoff=cutoff, timeframe=timeframe, source=source, symbol="EURUSD",
                indicators=[spec], **extra)


def result(candles, **kwargs):
    value = evaluate(Evaluation.model_validate(request(candles, **kwargs)))
    return value["results"][0]


def test_complete_forex_week_allows_friday_close_and_sunday_reopen():
    data = forex()
    report = result(data+[bar(NEXT_MONDAY, 40, 1)])
    assert report["coverage"] == {
        "status": "COMPLETE", "confirmation_status": "confirmed", "reference_start": MONDAY, "reference_end": NEXT_MONDAY,
        "expected_candles": 120, "observed_candles": 120, "available_at": NEXT_MONDAY,
        "scheduled_closed_seconds": 48*HOUR, "candidate_high": 14, "candidate_low": 8,
    }
    assert report["warnings"] == []
    assert report["series"]["High"][-1] == dict(time=NEXT_MONDAY, value=14,
        available_at=NEXT_MONDAY+HOUR, status="confirmed", reference_start=MONDAY,
        reference_end=NEXT_MONDAY)
    assert report["series"]["Low"][-1]["value"] == 8


def test_missing_open_candle_and_completely_absent_week():
    data = forex()
    missing = result(data[:10]+data[11:]+[bar(NEXT_MONDAY)])
    assert missing["coverage"]["status"] == "MISSING_OPEN_CANDLES"
    assert missing["coverage"]["observed_candles"] == 119
    assert missing["coverage"]["candidate_high"] == 14
    assert missing["coverage"]["confirmation_status"] == "provisional"
    assert missing["coverage"]["available_at"] is None
    assert not missing["series"]["High"]
    older = [{**item, "time": item["time"]-WEEK} for item in data]
    absent = result(older+[bar(NEXT_MONDAY)])
    assert absent["coverage"]["status"] == "REFERENCE_ABSENT"
    assert absent["coverage"]["confirmation_status"] == "unavailable"
    assert not absent["series"]["High"]


def test_closed_saturday_and_unexpected_candle():
    sunday = utc("2026-11-08")
    closed = result([bar(SUNDAY_REOPEN)], cutoff=SUNDAY_REOPEN+HOUR, kind="previous_day")
    assert closed["coverage"]["status"] == "MARKET_CLOSED"
    assert closed["coverage"]["reference_end"] == sunday
    assert closed["coverage"]["expected_candles"] == 0
    assert not closed["series"]["High"]
    wrong = result(sorted(forex()+[bar(utc("2026-11-07T12:00"))],key=lambda row:row["time"]))
    assert wrong["coverage"]["status"] == "SCHEDULE_MISMATCH"


@pytest.mark.parametrize(("timezone", "start", "window"), [
    ("America/New_York", "2026-03-08", ("2026-03-08T21:00", "2026-03-13T21:00")),
    ("America/New_York", "2026-11-01", ("2026-11-01T22:00", "2026-11-06T22:00")),
    ("Europe/London", "2026-03-29", ("2026-03-29T16:00", "2026-04-03T16:00")),
    ("Europe/London", "2026-10-25", ("2026-10-25T17:00", "2026-10-30T17:00")),
])
def test_sunday_open_and_friday_close_follow_iana_dst(timezone, start, window):
    beginning = utc(start)
    assert open_intervals(beginning, beginning+WEEK, MarketSchedule(kind="forex_weekly", timezone=timezone)) == [
        tuple(map(utc, window))]


def test_unverified_schedule_cannot_confirm_even_with_caller_hashes(client):
    response = client.post("/api/indicators/evaluate", json=request(forex()+[bar(NEXT_MONDAY)],
        schedule=None, price_basis="BID", feed_id="claimed",
        partitions=[{"id":"claimed", "sha256":"a"*64}])).json()
    assert response["authority"] == "CLIENT_SUPPLIED_UNVERIFIED"
    state = response["results"][0]
    assert state["coverage"]["status"] == "UNKNOWN_SCHEDULE"
    assert state["coverage"]["candidate_high"] == 14
    assert state["series"]["High"] == []


def test_delayed_final_reference_bar_and_exact_week_boundary():
    delayed = NEXT_MONDAY+2*HOUR
    data = forex()
    data[-1]["available_at"] = delayed
    data += [bar(NEXT_MONDAY), bar(NEXT_MONDAY+HOUR)]
    before = result(data, cutoff=NEXT_MONDAY-1)
    assert before["coverage"]["reference_end"] == MONDAY
    at_boundary = result(data, cutoff=NEXT_MONDAY)
    assert at_boundary["coverage"]["status"] == "MISSING_OPEN_CANDLES"
    assert result(data, cutoff=delayed-1)["series"]["High"] == []
    after = result(data, cutoff=delayed)
    assert after["coverage"]["available_at"] == delayed
    assert [(point["time"],point["available_at"]) for point in after["series"]["High"]] == [
        (NEXT_MONDAY+HOUR, delayed)]


@pytest.mark.parametrize(("frame","step"), [("M1", 60), ("M5", 300)])
def test_server_lookback_beyond_chart_budget_for_continuous_week(client, frame, step):
    last = utc("2026-11-13T20:00")
    payload = request([bar(last-step)], cutoff=last, source="DEMO", timeframe=frame, schedule=None,
                      feed_id="demo-v1", price_basis="SIMULATED", load_reference=True, frozen=True)
    data = client.post("/api/indicators/evaluate", json=payload).json()
    state = data["results"][0]
    assert data["input"]["candles"] == 1
    assert state["coverage"]["status"] == "COMPLETE"
    assert state["coverage"]["reference_end"] == utc("2026-11-09")
    assert state["coverage"]["expected_candles"] == WEEK//step
    assert state["coverage"]["retrieval"]["decoded_candles"] == WEEK//step
    assert state["series"]["High"][0]["status"] == "confirmed"


def test_exact_endpoint_limit_and_empty_history(client):
    data = [bar(MONDAY+index*60) for index in range(2001)]
    args = dict(cutoff=MONDAY+WEEK+1, timeframe="M1", source="DEMO", schedule=None)
    assert client.post("/api/indicators/evaluate", json=request(data[:2000], **args)).status_code == 200
    assert client.post("/api/indicators/evaluate", json=request(data, **args)).status_code == 422
    result = client.post("/api/indicators/evaluate", json=request([], **args)).json()["results"][0]
    assert result["coverage"]["status"] == "REFERENCE_ABSENT"


def save_feed(candles, feed="test_feed"):
    return [storage.write_partition("MT5", feed, "EURUSD", "candles", "H1", "BID", candles[i:i+60])
            for i in range(0, len(candles), 60)]


def test_archive_provenance_and_corruption(client):
    manifest = save_feed(forex())
    save_feed([bar(MONDAY, 80, 1)], "other_feed")
    claimed = [{"id": entry["id"], "sha256": entry["sha256"]} for entry in manifest]
    payload = request([bar(NEXT_MONDAY)], load_reference=True, frozen=True, feed_id="test_feed",
                      price_basis="BID", partitions=claimed)
    response = client.post("/api/indicators/evaluate", json=payload).json()
    state = response["results"][0]
    assert response["authority"] == "CLIENT_SUPPLIED_UNVERIFIED"
    assert state["coverage"]["context_authority"] == "CHECKSUM_VERIFIED_ARCHIVE"
    assert state["coverage"]["retrieval"] == {
        "requested_candles": 168, "decoded_candles": 120, "partitions_examined": 2}
    assert state["series"]["High"][0]["value"] == 14
    wrong = {**payload, "partitions": [{**claimed[0], "sha256": "0"*64}, claimed[1]]}
    assert client.post("/api/indicators/evaluate",json=wrong).json()["results"][0]["coverage"]["status"] == "SOURCE_INTEGRITY_FAILURE"
    path = Path(storage.MARKET_DIR) / manifest[0]["relative_path"]
    path.write_bytes(path.read_bytes()+b"corruption")
    corrupted = client.post("/api/indicators/evaluate",json=payload).json()["results"][0]
    assert corrupted["coverage"]["status"] == "SOURCE_INTEGRITY_FAILURE"
    assert corrupted["series"]["High"] == []


@pytest.mark.parametrize("second_offset", [0, HOUR//2])
def test_reject_duplicate_and_overlap_in_saved_evidence(client, second_offset):
    save_feed([bar(MONDAY)])
    save_feed([bar(MONDAY+second_offset, 15)])
    evaluation = Evaluation.model_validate(request([bar(NEXT_MONDAY)], load_reference=True,
        feed_id="test_feed", price_basis="BID"))
    assert evaluate(evaluation, load_reference)["results"][0]["coverage"]["status"] == "SOURCE_INTEGRITY_FAILURE"
