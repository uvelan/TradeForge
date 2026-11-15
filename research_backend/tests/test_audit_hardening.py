import asyncio
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import json
import lzma
import struct
from datetime import UTC, datetime

import pytest

from research_backend.app.db import one
from research_backend.app.saved_history import read_tick_partitions
from research_backend.app.storage import decode_bi5, write_partition
from research_backend.tests.test_tick_replay import inputs, replay


def archive(events, *, hour=None, legacy=False):
    day = datetime(1970, 1, 1, tzinfo=UTC)
    origin = (hour or 0) * 3_600_000
    payload = lzma.compress(b"".join(
        struct.pack(">IIIff", time-origin, bid+1, bid, 1, 1) for time,bid in events
    ))
    quotes = decode_bi5(payload, "EURUSD", day, hour)
    if legacy:
        for row in quotes:
            for key in ("source_sha256", "source_window_start_ms", "source_window_end_ms"):
                del row[key]
    result = write_partition("DUKASCOPY", "test", "EURUSD", "ticks", None, "BID_ASK_UNITS", quotes)
    return one("SELECT * FROM datasets WHERE id=?", (result["id"],))


@pytest.mark.parametrize("import_hourly_first", [False, True])
def test_daily_hourly_overlap_preserves_repeated_quotes_and_execution(client, import_hourly_first):
    anchor = 3_600_000
    hour = [(anchor, 100), (anchor, 100), (anchor+1000, 98), (anchor+1000, 106)]
    day = [(anchor-1, 100), *hour]
    if import_hourly_first:
        hourly, daily = archive(hour, hour=1), archive(day)
    else:
        daily, hourly = archive(day), archive(hour, hour=1)
    quotes = read_tick_partitions([daily, hourly])
    assert quotes == read_tick_partitions([hourly, daily])
    assert [(q["time_ms"], q["bid_units"]) for q in quotes] == day
    assert [q["source_order"] for q in quotes] == [0, 0, 1, 0, 1]
    assert quotes[1]["original_source_order"] < quotes[2]["original_source_order"]
    assert len(quotes[1]["source_sha256"]) == 64
    candles, _ = inputs()
    for row in candles:
        row["time"] += 3420
        for field in ("open", "high", "low", "close"):
            row[field] /= 100_000
    trade, = replay(candles, quotes)["trades"]
    assert trade["exit_reason"] == "STOP"
    assert trade["exit_source_order"] == 0


@pytest.mark.parametrize("alternative", [[(3_600_000, 106), (3_600_000, 98)], [(3_600_000, 98)]])
def test_different_order_or_missing_overlap_is_rejected(client, alternative):
    daily = archive([(3_599_999, 100), (3_600_000, 98), (3_600_000, 106)])
    hourly = archive(alternative, hour=1)
    with pytest.raises(ValueError, match="Conflicting tick quotes"):
        read_tick_partitions([daily, hourly])


def test_legacy_overlap_rejected_but_disjoint_boundary_is_supported(client):
    left = archive([(3_599_999, 100)], hour=0, legacy=True)
    right = archive([(3_600_000, 100)], hour=1)
    assert [q["time_ms"] for q in read_tick_partitions([right, left])] == [3_599_999, 3_600_000]
    with pytest.raises(ValueError, match="lack source provenance"):
        read_tick_partitions([left, archive([(3_599_999, 100)])])


def review_payload(client, plan):
    trade = client.post("/api/trades", json=plan).json()
    package = client.post(f"/api/ai/exports/{trade['id']}").json()
    return {"package_id": package["package_id"], "trade_decision_id": trade["id"],
            "plan_version": 1, "assessment": "SUPPORTED", "limitations": ["Résumé 📈"]}


def submit(client, body):
    return client.post("/api/ai/reviews", content=body, headers={"Content-Type": "application/json"})


def test_review_size_is_byte_exact_and_checked_before_validation(client, plan):
    body = json.dumps(review_payload(client, plan), ensure_ascii=False).encode()
    exact = body + b" " * (1_000_000 - len(body))
    response = submit(client, exact)
    assert response.status_code == 201, response.text
    review_id = response.json()["id"]
    assert client.get(f"/api/ai/reviews/{review_id}/original").content == exact
    assert submit(client, exact + b" ").status_code == 413
    assert submit(client, iter([b"x" * 500_000, b"x" * 500_001])).status_code == 413
    assert submit(client, b'{"bad":').status_code == 422
    assert submit(client, b'{"field":1,"field":2}').status_code == 422
    assert submit(client, b'{"field":NaN}').status_code == 422
    assert submit(client, b"[" * 65 + b"0" + b"]" * 65).status_code == 422
    assert len(client.get(f"/api/ai/reviews/{review_id}/submissions").json()["items"]) == 1


def test_streaming_limit_stops_receiving_before_a_third_chunk():
    from fastapi import HTTPException, Request
    from research_backend.app.main import import_review

    chunks = iter([b"x" * 500_000, b"x" * 500_001])
    received = 0
    async def receive():
        nonlocal received
        received += 1
        return {"type": "http.request", "body": next(chunks), "more_body": True}

    with pytest.raises(HTTPException) as error:
        asyncio.run(import_review(Request({"type": "http", "headers": []}, receive)))
    assert error.value.status_code == 413 and received == 2


def test_each_accepted_review_submission_is_immutable_and_traceable(client, plan):
    payload = review_payload(client, plan)
    raw = json.dumps(payload, ensure_ascii=False).encode()
    equivalent = json.dumps(payload, indent=4).encode()
    submissions = [submit(client, body).json() for body in (raw, raw, equivalent)]
    assert [item["classification"] for item in submissions] == ["NEW_REVIEW", "EXACT_DUPLICATE", "EQUIVALENT_JSON"]
    assert len({item["id"] for item in submissions}) == 1
    assert len({item["submission_id"] for item in submissions}) == 3
    base = f"/api/ai/reviews/{submissions[0]['id']}/submissions"
    page = client.get(base, params={"limit": 2}).json()
    items = page["items"] + client.get(base, params={"after": page["next_cursor"]}).json()["items"]
    for item, body in zip(items, (raw, raw, equivalent), strict=True):
        assert item["validation"] == "ACCEPTED"
        assert item["sha256"] == hashlib.sha256(body).hexdigest()
        assert client.get(f"{base}/{item['id']}/original").content == body
    changed = submit(client, json.dumps({**payload, "assessment": "REJECTED"}).encode()).json()
    assert changed["id"] != submissions[0]["id"]
    assert changed["classification"] == "NEW_REVIEW"

    from research_backend.app.db import transaction
    with pytest.raises(sqlite3.IntegrityError, match="immutable record"):
        with transaction() as db:
            db.execute("DELETE FROM ai_review_submissions WHERE id=?", (items[0]["id"],))


def test_concurrent_review_duplicates_share_one_canonical_record(client, plan):
    raw = json.dumps(review_payload(client, plan)).encode()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: submit(client, raw), range(4)))
    assert all(result.status_code == 201 for result in results)
    submissions = [result.json() for result in results]
    assert len({record["id"] for record in submissions}) == 1
    assert len({record["submission_id"] for record in submissions}) == 4
    assert sorted(record["classification"] for record in submissions) == [
        "EXACT_DUPLICATE", "EXACT_DUPLICATE", "EXACT_DUPLICATE", "NEW_REVIEW"
    ]



def test_bid_check_uses_actual_half_open_candle_intervals():
    from research_backend.app.tick_replay import validate_bid_extremes

    candles = [{"time": 15, "low": 99, "high": 101}, {"time": 75, "low": 106, "high": 109}]
    validate_bid_extremes(candles, [{"time_ms": 75_000, "bid_units": 107, "scale": 1}], 60)
    with pytest.raises(ValueError, match="UTC epoch 15"):
        validate_bid_extremes(candles, [{"time_ms": 74_999, "bid_units": 107, "scale": 1}], 60)
