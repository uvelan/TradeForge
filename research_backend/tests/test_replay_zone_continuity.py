"""Public saved-zone receipts retain the producer's two observed gap boundaries."""
from datetime import UTC, datetime

import pytest

from research_backend.tests.test_replay_hour import publish, session
from research_backend.tests.test_replay_zones import BLOCK, GAP, STEP, receipt


def utc(day, hour, minute):
    return int(datetime(2026, 11, day, hour, minute, tzinfo=UTC).timestamp())


def gap_fixture(kind, previous, successor):
    quiet = (16, 17, 15, 16)
    prices = [*GAP[:3], quiet] if kind == "FVG" else [BLOCK[0], *BLOCK[:6]]
    start = previous - len(prices) * STEP
    values = [dict(time=start + i * STEP, open=o, high=h, low=l, close=c, available_at=None)
              for i, (o, h, l, c) in enumerate([*prices, quiet, quiet])]
    values[-1]["time"] = successor
    return values


@pytest.mark.parametrize("kind,symbol,previous,successor", [
    ("FVG", "EURUSD", utc(13, 13, 15), utc(13, 13, 25)),
    ("OB", "GBPUSD", utc(13, 13, 35), utc(13, 13, 45)),
])
@pytest.mark.parametrize("mitigation", ["wick", "close"])
def test_quiet_gap_boundary_rows_belong_to_exact_revision(client, kind, symbol, previous, successor, mitigation):
    values = gap_fixture(kind, previous, successor)
    available = successor + STEP + 7
    values[-1]["available_at"] = available
    first = publish(values[:-1], symbol)
    second = publish(values[-2:], symbol)
    key = session(client, start=values[0]["time"], end=available + STEP, symbol=symbol)
    earlier = receipt(client, key, available - 1, mitigation=mitigation)
    active = next(z for z in earlier["zones"] if z["type"] == kind and z["formation_time"] == values[2 if kind == "FVG" else 6]["time"])
    options = dict(zone_id=active["zone_id"], mitigation=mitigation)
    prior = receipt(client, key, available - 1, **options)["selected"]
    assert active["state"] == "ACTIVE"
    assert prior["revisions"] == [active]
    assert all(r["time"] < previous for r in prior["evidence"])

    terminal = receipt(client, key, available, **options)["selected"]
    assert [r["state"] for r in terminal["revisions"]] == ["ACTIVE", "INVALIDATED"]
    revision = terminal["revisions"][-1]
    assert (revision["reason"], revision["available_at"], revision["observed_price"]) == (
        "MISSING_OPEN_CANDLES", available, None)
    continuity = revision["continuity"]
    assert continuity == {
        "previous_observed_open": previous, "next_observed_open": successor, "step_seconds": STEP,
        "missing_open_intervals": [{"start": previous + STEP, "end": successor}],
        "schedule": {"kind": "forex_weekly", "timezone": "America/New_York", "sunday_open": "17:00",
                     "friday_close": "17:00"}}
    assert terminal["revisions"][0] == active
    rows = {r["time"]: r for r in terminal["evidence"]}
    assert previous + STEP not in rows
    for stamp, expected, role in [(previous, [first, second], "GAP_PREVIOUS_OBSERVED"),
                                  (successor, [second], "GAP_NEXT_OBSERVED")]:
        row = rows[stamp]
        pins = sorted(({k: p[k] for k in ("id", "sha256")} for p in expected), key=lambda p: p["id"])
        assert row["source_id"] == f"{pins[0]['id']}:{stamp}"
        assert row["partitions"] == pins
        assert {"role": role, "event_id": revision["id"]} in row["roles"]
    assert receipt(client, key, available - 1, **options)["selected"] == prior


@pytest.mark.parametrize("kind", ["FVG", "OB"])
@pytest.mark.parametrize("missed_open", [False, True])
def test_weekend_only_closure_and_mixed_open_intervals(client, kind, missed_open):
    close, reopen = utc(13, 22, 0), utc(15, 22, 0)
    previous = close - STEP * (2 if missed_open else 1)
    successor = reopen + (STEP if missed_open else 0)
    values = gap_fixture(kind, previous, successor)
    publish(values)
    cutoff = successor + STEP
    key = session(client, start=values[0]["time"], end=cutoff)
    page = receipt(client, key, cutoff)
    zone = next(z for z in page["zones"] if z["type"] == kind and z["formation_time"] == values[2 if kind == "FVG" else 6]["time"])
    selected = receipt(client, key, cutoff, zone_id=zone["zone_id"])["selected"]
    if missed_open:
        assert selected["reason"] == "MISSING_OPEN_CANDLES"
        assert selected["revisions"][-1]["continuity"]["missing_open_intervals"] == [
            {"start": previous + STEP, "end": close}, {"start": reopen, "end": successor}]
    else:
        assert selected["state"] == "ACTIVE" and selected["revision"] == 1
        assert "continuity" not in selected["revisions"][0]
        assert not any(role["role"].startswith("GAP_") for row in selected["evidence"] for role in row["roles"])
