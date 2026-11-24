from datetime import datetime
from hashlib import sha256
import json

import pytest

from research_backend.app.db import one
from research_backend.app.historical_import import FEED
from research_backend.app.saved_history import read_tick_partitions
from research_backend.app.storage import write_partition
from test_tick_replay import inputs, tick


def archive(kind, rows):
    return write_partition("DUKASCOPY", FEED, "EURUSD", kind, "M1" if kind == "candles" else None,
                           "BID" if kind == "candles" else "BID_ASK_UNITS", rows)


def endpoint(client, bars, **options):
    return client.get("/api/backtest", params={
        "source": "DUKASCOPY_SAVED", "timeframe": "M1", "start": bars[0]["time"],
        "end": bars[-1]["time"] + 60, **options,
    })


def test_split_boundary(client):
    bars, ticks = inputs()
    bars[3].update(high=106, low=98)
    archive("candles", bars)
    source = sha256(json.dumps(ticks, sort_keys=True).encode()).hexdigest()
    for q in ticks:
        q.update(time=q["time_ms"] // 1000, source_sha256=source)
    whole = archive("ticks", ticks)
    single = endpoint(client, bars, tick_dataset_id=whole["id"]).json()
    # Range selection must see only the split layout.
    from research_backend.app import db
    with db.transaction() as conn:
        conn.execute("DELETE FROM datasets WHERE id=?", (whole["id"],))
    parts = [archive("ticks", subset) for subset in [ticks[:3], ticks[2:]]]
    datasets = [one("SELECT * FROM datasets WHERE id=?", (p["id"],)) for p in parts]
    assert all(p["sha256"] == d["sha256"] for p, d in zip(parts, datasets))
    assert read_tick_partitions(datasets) == read_tick_partitions(datasets[::-1]) == ticks
    response = endpoint(client, bars, tick_archive_range=True)
    assert response.status_code == 200, response.text
    result = response.json()
    assert [result[k] for k in ("trades", "unresolved_outcomes", "expectancy_r", "eligible_signals")] == [
        single[k] for k in ("trades", "unresolved_outcomes", "expectancy_r", "eligible_signals")]
    trade, = result["trades"]
    assert (trade["side"], trade["entry"], trade["exit"], trade["exit_reason"],
            trade["entry_time_ms"], trade["exit_time_ms"]) == ("BUY", 101, 106, "TARGET", 180000, 181000)
    assert result["replay_coverage"] == single["replay_coverage"]
    conflict = archive("ticks", [{**ticks[2], "bid_units": 105}])
    datasets.append(one("SELECT * FROM datasets WHERE id=?", (conflict["id"],)))
    for selection in (datasets, datasets[::-1]):
        with pytest.raises(ValueError, match="Conflicting tick quotes"):
            read_tick_partitions(selection)
    assert endpoint(client, bars, tick_archive_range=True).status_code == 422


@pytest.mark.parametrize("close,reopen,expected", [
    ("2025-01-10T22:00Z", "2025-01-12T22:00Z", "KNOWN_CLOSED"),
    ("2025-03-07T22:00Z", "2025-03-09T21:00Z", "KNOWN_CLOSED"),
    ("2025-10-31T21:00Z", "2025-11-02T22:00Z", "KNOWN_CLOSED"),
    ("2025-01-10T12:00Z", "2025-01-10T12:01Z", "MISSING_OPEN"),
])
def test_calendar_frontier(client, close, reopen, expected):
    end, start = [int(datetime.fromisoformat(t).timestamp()) for t in (close, reopen)]
    bars, _ = inputs()
    for i, bar in enumerate(bars):
        bar["time"] = end + (i-6)*60 if i < 6 else start + (i-6)*60
    bars[3]["high"] = bars[9]["high"] = 106
    bars[8].update(high=104, close=103)
    quotes = []
    for entry in (end-180, start+180):
        for s, bid in enumerate([100, 106]):
            quotes.append(dict(tick((entry+s)*1000, len(quotes), bid, bid+1), time=entry+s))
    archive("candles", bars)
    archive("ticks", quotes)
    r = endpoint(client, bars, tick_archive_range=True).json()
    assert r["count"] == (2 if expected == "KNOWN_CLOSED" else 1)
    assert r["expectancy_r"] == (2.5 if expected == "KNOWN_CLOSED" else None)
    assert r["replay_coverage"]["gaps"] == [{"start": end, "end": start, "status": expected}]

@pytest.mark.parametrize("scope", [None, "different"])
def test_unverified_cross_file_tie(client, scope):
    bars, ticks = inputs()
    bars[3].update(high=106, low=98)
    archive("candles", bars)
    for i, quote in enumerate(ticks):
        quote["time"] = quote["time_ms"] // 1000
        if scope:
            quote["source_sha256"] = ("a" if i < 3 else "b") * 64
    archive("ticks", ticks[:3])
    archive("ticks", ticks[3:])
    result = endpoint(client, bars, tick_archive_range=True)
    assert result.status_code == 422 and "Ambiguous cross-partition" in result.text


def test_open_trade_stops_at_dst_weekend(client):
    bars, _ = inputs()
    friday = int(datetime.fromisoformat("2025-03-07T21:54Z").timestamp())
    sunday = int(datetime.fromisoformat("2025-03-09T21:00Z").timestamp())
    for i, bar in enumerate(bars):
        bar["time"] = friday + i*60 if i < 6 else sunday + (i-6)*60
    bars[6]["high"] = 106
    quotes = [dict(tick((friday+s)*1000, i), time=friday+s) for i,s in enumerate(range(180,360,30))]
    quotes.append(dict(tick(sunday*1000, 6, 106, 107), time=sunday))
    archive("candles", bars)
    archive("ticks", quotes)
    r = endpoint(client, bars, tick_archive_range=True).json()
    assert r["replay_coverage"]["gaps"][0]["status"] == "KNOWN_CLOSED"
    assert r["trades"] == [] and r["expectancy_r"] is None
    assert r["unresolved_outcomes"][0]["reason"] == "CANDLE_SESSION_BOUNDARY"


def test_unsupported_metal_break_is_unverified():
    from research_backend.app.tick_replay import replay_coverage
    market = {"candles": [{"time": 0}], "feed_id": FEED,
              "coverage": {"truncated": False, "start": 0, "end": 3600, "gaps": [{"start": 60, "end": 3600}]}}
    assert replay_coverage(market, [], "DUKASCOPY", "XAUUSD", 60, 60)["gaps"][0]["status"] == "UNKNOWN_SCHEDULE"


def test_input_budgets_before_file_access():
    with pytest.raises(ValueError, match="500,000"):
        read_tick_partitions([{"rows_count": 500001}])
    with pytest.raises(ValueError, match="50 files"):
        read_tick_partitions([{"rows_count": 1}] * 51)
