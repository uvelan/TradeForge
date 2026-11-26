"""Controlled published LIMIT intents against generated or original BI5 quotes."""
import lzma
import os
from pathlib import Path
import struct
from hashlib import sha256

import pytest

from research_backend.app.db import transaction
from research_backend.app.offline_market_entry import validate_market_entries
from test_bounded_crt_smt_successors import canonical_successors
from test_offline_market_entry import publish, source_records
from test_recorded_market_entry import BASE, OFFSET, Q, controlled, prepare, shard

QUAL = Q + 3600_000
PIN = "9d40aa3147d93e6daa2218933877adb9471dc02ff08b19fc0d272f1eb781688f"
# Canonical SQLite payload SHA-256, result ID and fill ID from merged PR #35.
PUBLISHED = {
    "MARKET": ("8b8b9ffeeeabddb41600e295d2239083c637d13d36b802602e70fdfa4a27ff56",
               "ME-9a1bf764f238eab75df7572401a0c59266d611cd6789a9f4413357db451cc79a",
               "MF-ac41f1364297f69021fb039ff8b7b313f32d2ca3feb81c92ec2ddbef5019d012"),
    "LIMIT": ("de88ae8a0b7eb22d3df4b8d6624981bcf8b4ee2b24d957d6073826ca82b403ca",
              "ME-8830a1fd74875c4798dc191f68c913a7d7cc99b9648022b00990b80158cf5335",
              "MF-457f74b6ec52487690b296ac8f54d7e5ad3e7446bf8bcdadbdcb6202b3cdfd45"),
}
pytestmark = pytest.mark.parametrize("controlled", [(BASE+3600, 90)], indirect=True)


def bi5(points):
    return lzma.compress(b"".join(struct.pack(">IIIff", OFFSET+delta, ask, bid, 1, 1)
                                  for delta, bid, ask in points))


def count():
    with transaction() as db:
        return db.execute("SELECT count(*) FROM offline_market_entry_results").fetchone()[0]


@pytest.mark.parametrize("direction,limit,side,units,offset,ordinal", [
    ("bullish", 1.16274, "ASK", 116274, 17210, 4932),
    ("bearish", 1.16272, "BID", 116273, 1924, 4920),
])
def test_first_touch(controlled, direction, limit, side, units, offset, ordinal):
    run, intent = controlled(direction=direction, kind="LIMIT", entry_price=limit)
    before = source_records(run)
    assert intent["qualified_at"] * 1000 == QUAL
    path = os.environ.get("TRADEFORGE_T07_LIMIT_BI5")
    raw = Path(path).read_bytes() if path else bi5([
        (-361, 116270, 116274), (86, 116271, 116275), (449, 116271, 116276),
        (1924, 116273, 116276), (17210, 116271, 116274), (18000, 116271, 116275)])
    if path:
        assert sha256(raw).hexdigest() == PIN
    spec, rows = prepare(raw, 14, QUAL)
    miss = next(r for r in rows if r["time_ms"] > QUAL)
    assert (miss["time_ms"], miss["bid_units"], miss["ask_units"]) == (QUAL+86, 116271, 116275)
    pos = next(i for i, row in enumerate(rows) if row["time_ms"] == QUAL+offset)
    pieces = [shard(rows[:pos+1]), shard(rows[pos-1:])]
    results = []
    for pins in ([spec["source"]], pieces, pieces[::-1]):
        request = spec | {"partitions": pins}
        result = validate_market_entries(run, recorded=request)
        assert result == validate_market_entries(run, recorded=request)
        assert result["simulated_orders"] == result["simulated_fills"] == 1
        r, = result["results"]
        assert r["source"] == intent and r["status"] == "SIMULATED_FILLED"
        assert r["order"]["limit_price_units"] == (116274 if side == "ASK" else 116272)
        f = r["fill"]
        assert (f["side"], f["price_units"], f["time_ms"], f["quote"]["source_order"]) == (
            side, units, QUAL+offset, ordinal if path else pos)
        assert f["quote"]["available_at_ms"] == f["quote"]["time_ms"] == f["time_ms"]
        results.append(r)
    assert results[1] == results[2]
    assert results[0]["order"] == results[1]["order"]
    assert {k:v for k,v in results[0]["fill"].items() if k!="id"} == {
        k:v for k,v in results[1]["fill"].items() if k!="id"}
    assert source_records(run) == before and count() == 2
    print(direction, spec["source"], results[0]["fill"])


@pytest.mark.parametrize("case,status", [
    ("omission", None), ("insertion", None), ("alteration", None),
    ("invalid_then_conflict", None), ("altered_metadata", None),
    ("touch_miss", "AMBIGUOUS_QUOTE_ORDER"), ("miss_touch", "AMBIGUOUS_QUOTE_ORDER"),
    ("later_conflict", "SIMULATED_FILLED"), ("deadline_touch", "SIMULATED_FILLED"),
    ("expiry", "VERIFIED_EXPIRED"), ("gap", "INSUFFICIENT_QUOTE_COVERAGE"),
])
def test_limit_evidence_boundaries(controlled, case, status):
    run, intent = controlled(direction="bullish", kind="LIMIT", entry_price=1.16274)
    before = source_records(run)
    touch_ms = {"gap": 70000, "expiry": 90001, "deadline_touch": 90000}.get(case, 40000)
    points = [(-1000, 116270, 116275), (200, 116270, 116275)]
    if case in ("expiry", "deadline_touch"):
        points.append((45000, 116270, 116275))
    points.extend([(touch_ms, 116270, 116273), (touch_ms+1, 116271, 116274)])
    spec, rows = prepare(bi5(points), 14, QUAL)
    anchor, miss = rows[:2]
    touch, later = rows[-2:]
    earlier = rows[:-2]
    if case == "omission":
        earlier = [anchor]
    elif case == "insertion":
        earlier = earlier + [touch | {"time_ms": touch["time_ms"]-1, "time": (touch["time_ms"]-1)//1000}]
    elif case in ("alteration", "invalid_then_conflict"):
        earlier = [anchor, miss | {"ask_units": 116273}]
    extra = {
        "touch_miss": touch | {"ask_units": 116275},
        "invalid_then_conflict": touch | {"ask_units": 116275},
        "miss_touch": miss | {"ask_units": 116273},
        "altered_metadata": touch | {"ask_volume": 99.0},
        "later_conflict": later | {"ask_units": 116275},
    }.get(case, later)
    shards = [shard([*earlier, touch, later]), shard([extra])]
    responses = []
    for pieces in (shards, shards[::-1]):
        for _ in range(2):
            request = spec | {"partitions": pieces}
            if status is None:
                with pytest.raises(ValueError, match="omits or adds|disagrees|source metadata"):
                    validate_market_entries(run, recorded=request)
                assert count() == 0
                continue
            response = validate_market_entries(run, recorded=request)
            result, = response["results"]
            assert result["status"] == status and result["source"] == intent
            assert response["simulated_fills"] == int(status == "SIMULATED_FILLED")
            if status == "SIMULATED_FILLED":
                assert result["fill"]["price_units"] == 116273
                assert result["fill"]["time_ms"] == QUAL + touch_ms
            else:
                assert result["fill"] is None
            responses.append(response)
            assert count() == 1
    assert all(r == responses[0] for r in responses)
    assert source_records(run) == before


@pytest.mark.parametrize("kind", ["MARKET", "LIMIT"])
def test_published_compatibility(controlled, monkeypatch, kind):
    from itertools import count as sequence
    from uuid import UUID
    from test_offline_market_entry import quote, tape, validate

    ids = sequence(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4", lambda: UUID(int=next(ids)))
    if kind == "MARKET":
        run, _ = controlled()
        spec, _ = prepare(bi5([(-200, 116270, 116275), (400, 116271, 116275)]), 14, QUAL)
        result, = validate_market_entries(run, recorded=spec)["results"]
    else:
        run, _ = controlled(kind="LIMIT", direction="bullish", entry_price=1.16274)
        data = tape([quote(QUAL+400, QUAL+400, 2, 116271, 116273)], intervals=[(QUAL+1, QUAL+90000)])
        result, = validate(run, data)["results"]
    with transaction() as db:
        payload, = db.execute("SELECT CAST(payload AS BLOB) FROM offline_market_entry_results").fetchone()
    observed = (sha256(payload).hexdigest(), result["id"], result["fill"]["id"])
    print(kind, *observed)
    assert observed == PUBLISHED[kind]
