"""Independent importer invariants on deterministic provider-format fixtures."""
from datetime import UTC, datetime
from hashlib import sha256
from math import nan
import lzma
from struct import pack

import pytest

from research_backend.app import historical_import, storage
from research_backend.app.db import one, rows


DAY = datetime(2025, 1, 2, tzinfo=UTC)
START = int(DAY.timestamp())


def packet(entries):
    return lzma.compress(b"".join(pack(">IIIff", ms, ask, bid, volume, volume)
                                  for ms, bid, ask, volume in entries), format=lzma.FORMAT_ALONE)


def tick_rows(entries):
    return storage.decode_bi5(packet(entries), "EURUSD", DAY)


def test_millisecond_and_file_ordinal_control_both_prices():
    ticks = tick_rows([(400,110000,110004,1),(400,110020,110024,1),(100,110010,110011,1)])
    assert storage.aggregate_ticks(ticks[::-1],1) == [{
        "time":START,"open":1.10010,"high":1.10020,"low":1.10000,"close":1.10020,
        "ask_open":1.10011,"ask_high":1.10024,"ask_low":1.10004,"ask_close":1.10024,"volume":3,
    }]
    assert storage.aggregate_ticks([ticks[0], *ticks],1)[0]["volume"] == 3
    conflict = {**ticks[0],"bid_units":109999}
    with pytest.raises(ValueError,match="same source identity"):
        storage.aggregate_ticks([conflict, *ticks],1)
    with pytest.raises(ValueError,match="point scale"):
        storage.aggregate_ticks([ticks[0],{**ticks[1],"scale":1000}],1)


@pytest.mark.parametrize("entries,reason",[
    ([(86_400_000,100,101,1)],"outside"),
    ([(0,101,100,1)],"bid/ask"),
    ([(0,100,101,nan)],"volume"),
])
def test_reject_bad_provider_records(entries,reason):
    with pytest.raises(ValueError,match=reason):
        tick_rows(entries)


@pytest.mark.parametrize("payload,reason",[(b"broken","compression|Truncated"),
    (lzma.compress(b"not-a-record"),"record size")])
def test_reject_broken_bi5(payload,reason):
    with pytest.raises(ValueError,match=reason):
        storage.decode_bi5(payload,"EURUSD",DAY)


def test_complete_batches_preserve_first_tick_beyond_chart_limit(client):
    entries = [(index,110000 if index == 0 else 110020,110030,1) for index in range(8205)]
    payload = packet(entries)
    result = historical_import.import_bi5(payload,"EURUSD",DAY)
    assert len(result["candle_datasets"]) == 4
    assert result["source"]["sha256"] == sha256(payload).hexdigest()
    data = one("SELECT * FROM datasets WHERE id=?", (result["tick_dataset"]["id"],))
    assert len(storage.read_partition(data)) == 2000
    assert storage.read_partition(data)[0]["bid_units"] == 110020
    from research_backend.app.saved_history import read_tick_partitions
    complete = read_tick_partitions([data])
    assert len(complete) == 8205 and complete[0]["time_ms"] == START*1000
    assert storage.aggregate_ticks(complete,5)[0]["open"] == 1.1
    assert historical_import.import_bi5(payload,"EURUSD",DAY) == result
    assert len(rows("SELECT id FROM datasets")) == 5
    with pytest.raises(ValueError,match="row count"):
        list(storage.iter_complete_partition({**data,"rows_count":8204},max_rows=9000))


def test_atomic_publication_on_interrupted_derivation(client,monkeypatch):
    payload = packet([(0,110000,110002,1)])
    original = historical_import.write_partition

    def interrupted(*args,**kwargs):
        if args[4] == "M5":
            raise OSError("publication interrupted")
        return original(*args,**kwargs)

    monkeypatch.setattr(historical_import,"write_partition",interrupted)
    with pytest.raises(OSError,match="interrupted"):
        historical_import.import_bi5(payload,"EURUSD",DAY)
    assert not rows("SELECT id FROM datasets")
    assert not rows("SELECT manifest FROM historical_imports")
    monkeypatch.setattr(historical_import,"write_partition",original)
    assert historical_import.import_bi5(payload,"EURUSD",DAY)["candle_rows"] == 1


def test_overlap_refuses_second_source_even_with_disjoint_observed_ticks(client):
    first = packet([(60000,110000,110002,1)])
    later = packet([(3_600_000,110003,110004,1)])
    historical_import.import_bi5(first,"EURUSD",DAY,0)
    with pytest.raises(ValueError,match="Overlapping historical"):
        historical_import.import_bi5(later,"EURUSD",DAY)
    with pytest.raises(ValueError,match="Overlapping historical"):
        historical_import.import_bi5(packet([(61000,110003,110004,1)]),"EURUSD",DAY,0)
    assert len(rows("SELECT manifest FROM historical_imports")) == 1


def test_corruption_fails_complete_read_and_idempotent_reopen(client):
    payload = packet([(0,110000,110002,1)])
    result = historical_import.import_bi5(payload,"EURUSD",DAY)
    item = one("SELECT * FROM datasets WHERE id=?", (result["tick_dataset"]["id"],))
    file = storage.MARKET_DIR/item["relative_path"]
    file.write_bytes(file.read_bytes()+b"damaged")
    with pytest.raises(RuntimeError,match="checksum"):
        list(storage.iter_complete_partition(item,max_rows=5))
    with pytest.raises(RuntimeError,match="checksum"):
        historical_import.import_bi5(payload,"EURUSD",DAY)


def test_complete_m1_matches_direct_m5_and_missing_minute_is_not_filled():
    ticks = tick_rows([(60000*i+j,110000+i+j,110005+i+j,1) for i in range(5) for j in (0,1)])
    minutes = storage.aggregate_ticks(ticks,1)
    expected = storage.aggregate_ticks(ticks,5)[0]
    for prefix in ("","ask_"):
        assert (minutes[0][prefix+"open"],max(row[prefix+"high"] for row in minutes),
                min(row[prefix+"low"] for row in minutes),minutes[-1][prefix+"close"]) == tuple(
                    expected[prefix+name] for name in ("open","high","low","close"))
    assert sum(row["volume"] for row in minutes) == expected["volume"] == 10
    assert [bar["time"] for bar in storage.aggregate_ticks(ticks[:2]+ticks[4:],1)] == [
        START,START+120,START+180,START+240]


def test_future_source_is_rejected_without_catalog_write(client):
    response = client.post("/api/market/import/bi5?symbol=EURUSD&day=2099-01-02",
                           files={"file":("fixture.bi5",packet([(0,110000,110002,1)]))})
    assert response.status_code == 422 and "completed UTC" in response.text
    assert not rows("SELECT id FROM datasets")
