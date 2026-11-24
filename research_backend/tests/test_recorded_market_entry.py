"""Public-service tests. An optional pinned BI5 hour replaces the generated positive tape."""
from datetime import UTC, datetime
from hashlib import sha256
import json
import lzma
import os
from pathlib import Path
import struct

import pytest

from research_backend.app.db import transaction
from research_backend.app.historical_import import FEED, import_bi5
from research_backend.app.offline_market_entry import validate_market_entries
from research_backend.app.storage import decode_bi5, write_partition
import test_bounded_crt_smt_successors as crt
import test_offline_market_entry as market
import test_strategy_evaluator as evaluator
import test_strategy_evaluator_integrity as integrity
from test_bounded_crt_smt_successors import canonical_successors
from test_offline_market_entry import publish, source_records

DAY = datetime(2025, 11, 13, tzinfo=UTC)
BASE = int(DAY.replace(hour=10).timestamp())
Q = (BASE + 14120) * 1000
OFFSET = 3320000
REAL_PIN = "2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014"


@pytest.fixture
def controlled(monkeypatch, publish):
    for module in (crt, market, evaluator, integrity):
        monkeypatch.setattr(module, "BASE", BASE)
    monkeypatch.setattr(crt, "START", BASE + 3600)
    monkeypatch.setattr(crt, "END", BASE + 15300)
    original = crt.bounded_definition

    def definition(*args, **kwargs):
        value = original(*args, **kwargs)
        value["trade_intent"]["expiry"] = {"unit": "SECONDS", "count": 5}
        return value

    monkeypatch.setattr(crt, "bounded_definition", definition)
    return publish


def generated(first=1403):
    return lzma.compress(b"".join(struct.pack(">IIIff", OFFSET+ms, ask, bid, 1, 1)
                                  for ms, ask, bid in [(-1000, 116169, 116165),
                                                       (first, 116174, 116170),
                                                       (first+200, 116177, 116172)]))


def pin(part):
    return {key: part[key] for key in ("id", "sha256")}


def prepare(raw):
    part = import_bi5(raw, "EURUSD", DAY, 13)["tick_dataset"]
    spec = dict(source=pin(part), source_sha256=sha256(raw).hexdigest(), partitions=[pin(part)],
                start=Q//1000-60, end=Q//1000+90)
    rows = [row | {"source_sha256": spec["source_sha256"]}
            for row in decode_bi5(raw, "EURUSD", DAY, 13)
            if spec["start"] <= row["time"] < spec["end"]]
    return spec, rows


def shard(rows):
    return pin(write_partition("DUKASCOPY", FEED, "EURUSD", "ticks", None, "BID_ASK_UNITS", rows))


@pytest.mark.parametrize("direction,side,units", [("bearish", "BID", 116170), ("bullish", "ASK", 116174)])
def test_recorded_layouts(controlled, direction, side, units):
    run, intent = controlled(direction=direction)
    before = source_records(run)
    assert intent["qualified_at"] * 1000 == Q
    path = os.environ.get("TRADEFORGE_T07_REAL_BI5")
    raw = Path(path).read_bytes() if path else generated()
    if path:
        assert sha256(raw).hexdigest() == REAL_PIN
    spec, rows = prepare(raw)
    pos = next(i for i, row in enumerate(rows) if row["time_ms"] > Q)
    shards = [shard(rows[:pos+1]), shard(rows[pos:])]
    results = []
    for partitions in ([spec["source"]], shards, shards[::-1], [spec["source"], shards[1]]):
        request = spec | {"partitions": partitions}
        first = validate_market_entries(run, recorded=request)
        assert first == validate_market_entries(run, recorded=request)
        assert first["simulated_fills"] == 1
        result, = first["results"]
        assert result["source"] == intent
        fill = result["fill"]
        assert (fill["time_ms"], fill["quote"]["source_order"], fill["side"], fill["price_units"]) == (
            Q+1403, 2971 if path else 1, side, units)
        assert fill["quote"]["time_ms"] == fill["quote"]["available_at_ms"] == fill["time_ms"]
        results.append(result)
    assert results[1] == results[2]
    assert results[0]["id"] != results[1]["id"]
    assert source_records(run) == before
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM offline_market_entry_results").fetchone()[0] == 3
    print(json.dumps({"controlled_intent": intent["id"], "qualified_at_ms": Q,
                      "source": spec, "fill": results[0]["fill"]}, sort_keys=True))


@pytest.mark.parametrize("case,expected", [("ambiguous", "AMBIGUOUS_QUOTE_ORDER"),
                                          ("gap", "INSUFFICIENT_QUOTE_COVERAGE"),
                                          ("expiry", "VERIFIED_EXPIRED")])
def test_recorded_failures(controlled, case, expected):
    run, _ = controlled()
    raw = generated(6000 if case == "expiry" else 1403)
    if case == "gap":
        data = bytearray(lzma.decompress(raw))
        struct.pack_into(">I", data, 0, OFFSET-59000)
        raw = lzma.compress(data)
    spec, rows = prepare(raw)
    if case == "ambiguous":
        spec["partitions"] = [shard(rows), shard([rows[1] | {"bid_units": 116169}])]
    response = validate_market_entries(run, recorded=spec)
    assert response["results"][0]["status"] == expected
    assert response["simulated_orders"] == 1 and response["simulated_fills"] == 0


def test_recorded_omission_and_pin(controlled):
    run, _ = controlled()
    spec, rows = prepare(generated())
    with pytest.raises(ValueError, match="omits"):
        validate_market_entries(run, recorded=spec | {"partitions": [shard(rows[1:])]})
    with pytest.raises(ValueError, match="pin mismatch"):
        validate_market_entries(run, recorded=spec | {"source": spec["source"] | {"sha256": "0"*64}})
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM offline_market_entry_results").fetchone()[0] == 0


def test_recorded_suffix_and_retry_integrity(controlled):
    from research_backend.app import storage

    run, _ = controlled()
    spec, rows = prepare(generated())
    later = rows[2] | {"bid_units": 116166}
    spec["partitions"] = [shard(rows), shard([later])]
    result, = validate_market_entries(run, recorded=spec)["results"]
    assert result["fill"]["quote"]["source_order"] == 1
    with transaction() as db:
        relative = db.execute("SELECT relative_path FROM datasets WHERE id=?",
                              (spec["partitions"][1]["id"],)).fetchone()[0]
    path = storage.MARKET_DIR / relative
    with path.open("ab") as changed:
        changed.write(b"changed suffix")
    with pytest.raises(RuntimeError, match="(?i)checksum"):
        validate_market_entries(run, recorded=spec)
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM offline_market_entry_results").fetchone()[0] == 1


@pytest.mark.parametrize("prior", ["exact", "omitted", "added", "changed"])
def test_winning_conflict_requires_verified_prefix(controlled, prior):
    run, _ = controlled()
    before = source_records(run)
    spec, (anchor, winner, suffix) = prepare(generated())
    # The inserted observation shares the winner's millisecond but precedes its ordinal.
    earlier = {
        "exact": [anchor],
        "omitted": [],
        "added": [anchor, winner | {"source_order": 0}],
        "changed": [anchor | {"ask_volume": 2.0}],
    }[prior]
    shards = [shard([*earlier, winner, suffix]), shard([winner | {"bid_units": 116169}])]
    outcomes = []
    for parts in (shards, shards[::-1]):
        for _ in range(2):
            try:
                response = validate_market_entries(run, recorded=spec | {"partitions": parts})
            except ValueError as exc:
                outcome = (str(exc), None, None)
            else:
                result, = response["results"]
                outcome = (result["status"], response["simulated_fills"], result["id"])
            with transaction() as db:
                count = db.execute("SELECT count(*) FROM offline_market_entry_results").fetchone()[0]
            outcomes.append((*outcome, count))
    assert len(set(outcomes)) == 1
    assert source_records(run) == before
    status, fills, identifier, count = outcomes[0]
    print(prior, status, fills, count)
    if prior == "exact":
        assert (status, fills, count) == ("AMBIGUOUS_QUOTE_ORDER", 0, 1)
        assert identifier.startswith("ME-")
    else:
        reason = ("disagrees with verified BI5 source" if prior == "changed"
                  else "omits or adds source observations")
        assert reason in status and (fills, identifier, count) == (None, None, 0)
