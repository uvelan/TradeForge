"""Exercise each public replay entry point against the same physical quote layouts."""
from datetime import datetime

import pytest

from research_backend.app import db, main
from test_t06_replay import archive
from test_tick_replay import inputs, tick


@pytest.mark.parametrize("case", [
    "trailing", "leading", "exclusive_end", "clean_weekend", "distinct_sources", "ordinal_collision", "null_ordinal", "null_ms",
])
def test_public_layout_equivalence(client, monkeypatch, case):
    close = int(datetime.fromisoformat("2025-01-10T22:00Z").timestamp())
    reopen = int(datetime.fromisoformat("2025-01-12T22:00Z").timestamp())
    first = reopen if case == "leading" else close - 1800
    bars, ticks = inputs()
    bars[3].update(high=106, low=98)
    for bar in bars:
        bar["time"] += first
    for quote in ticks:
        quote["time_ms"] += first * 1000
        quote.update(time=quote["time_ms"] // 1000, source_sha256="a" * 64)
    start, end = (reopen - 120, reopen + 1800) if case == "leading" else (first, close + 120)
    if case == "clean_weekend":
        bars.extend([{**bars[-1], "time": reopen + i * 60} for i in range(3)])
        end = reopen + 180
    if case in ("distinct_sources", "ordinal_collision"):
        ticks[3]["source_sha256"] = "b" * 64
        if case == "ordinal_collision":
            ticks[3]["source_order"] = ticks[2]["source_order"]
    if case in ("null_ordinal", "null_ms"):
        ticks[3]["source_order" if case == "null_ordinal" else "time_ms"] = None
    pieces = [ticks[:3], ticks[3:]]
    if case in ("leading", "trailing", "exclusive_end"):
        time = {"leading": reopen - 60, "trailing": close + 60, "exclusive_end": end}[case]
        extra = dict(tick(time * 1000, 5), time=time, source_sha256="a" * 64)
        pieces.append([extra])
        if case == "exclusive_end":
            # Opposite sources outside the requested interval cannot affect replay.
            pieces[-1].append(dict(extra, source_order=6, source_sha256="b" * 64))
    archive("candles", bars)
    whole = archive("ticks", [q for piece in pieces for q in piece])
    params = dict(source="DUKASCOPY_SAVED", timeframe="M1", start=start, end=end)
    observations = {}

    def probe(layout, **choice):
        request = params | choice
        get = client.get("/api/backtest", params=request)
        post = client.post("/api/backtest/runs", json=request)
        observations[layout] = (get.status_code, post.status_code, get.json())
        if post.status_code == 201:
            assert post.json()["result"] == get.json()
        else:
            assert post.json() == get.json() and set(post.json()) == {"detail"}

    probe("shorthand", tick_dataset_id=whole["id"])
    probe("one_file", tick_archive_range=True)
    with db.transaction() as conn:
        conn.execute("DELETE FROM datasets WHERE id=?", (whole["id"],))
    if case in ("exclusive_end", "clean_weekend"):
        # One identical pinned source observation overlaps the two shards.
        pieces[1] = [ticks[2], *pieces[1]]
    for piece in pieces:
        archive("ticks", piece)
    probe("split", tick_archive_range=True)
    select = main.tick_partitions
    monkeypatch.setattr(main, "tick_partitions", lambda *args: select(*args)[::-1])
    probe("reversed_split", tick_archive_range=True)
    statuses = {name: value[:2] for name, value in observations.items()}
    if case not in ("exclusive_end", "clean_weekend"):
        assert set(statuses.values()) == {(422, 422)}, statuses
        details = {o[2]["detail"] for o in observations.values()}
        assert len(details) == 1
        reason = "calendar" if case in ("leading", "trailing") else "source order"
        if case in ("null_ordinal", "null_ms"):
            assert details == {"Invalid tick timestamp or source order"}
        else:
            assert reason in details.pop()
        assert db.one("SELECT count(*) AS n FROM backtest_runs")["n"] == 0
    else:
        assert set(statuses.values()) == {(200, 201)}, statuses
        reference = observations["shorthand"][2]
        for _, _, result in observations.values():
            assert {k:v for k,v in result.items() if k != "lineage"} == {
                k:v for k,v in reference.items() if k != "lineage"}
            assert result["replay_coverage"]["required_period"] == [start, end]
            assert result["replay_coverage"]["observed_quotes"] == 5
            trade, = result["trades"]
            assert (trade["exit_reason"], trade["exit_time_ms"], trade["result_r"]) == (
                "TARGET", (first + 181)*1000, 2.5)

@pytest.mark.parametrize("friday,sunday,before_close", [
    ("2025-01-10T22:00Z", "2025-01-12T22:00Z", False),
    ("2025-03-07T22:00Z", "2025-03-09T21:00Z", False),
    ("2025-01-10T22:00Z", "2025-01-12T22:00Z", True),
])
def test_mixed_calendar_gap(client, friday, sunday, before_close):
    close, reopen = [int(datetime.fromisoformat(t).timestamp()) for t in (friday, sunday)]
    last_end = close - 60 if before_close else close
    first_open = reopen if before_close else reopen + 60
    bars, _ = inputs()
    for i, bar in enumerate(bars):
        bar["time"] = (last_end if i < 6 else first_open) + (i - 6) * 60
    bars[3]["high"] = bars[9]["high"] = 106
    bars[8].update(high=104, close=103)
    quotes = []
    for entry in (last_end - 180, first_open + 180):
        for second, bid in enumerate((100, 106)):
            t = entry + second
            quotes.append(dict(tick(t * 1000, len(quotes), bid, bid + 1), time=t))
    archive("candles", bars)
    archive("ticks", quotes)
    params = dict(source="DUKASCOPY_SAVED", timeframe="M1", start=bars[0]["time"],
                  end=bars[-1]["time"] + 60, tick_archive_range=True)
    get = client.get("/api/backtest", params=params)
    post = client.post("/api/backtest/runs", json=params)
    assert (get.status_code, post.status_code) == (200, 201)
    stored = client.get("/api/backtest/runs/" + post.json()["id"])
    result = get.json()
    assert result == post.json()["result"] == stored.json()["result"]
    known = dict(start=close, end=reopen, status="KNOWN_CLOSED")
    missing = (dict(start=last_end, end=close, status="MISSING_OPEN") if before_close else
               dict(start=reopen, end=first_open, status="MISSING_OPEN"))
    audit = result["replay_coverage"]
    assert audit["gaps"] == ([missing, known] if before_close else [known, missing])
    assert audit["first_unverified_candle_s"] == missing["start"]
    trade, = result["trades"]
    assert trade["exit_time_ms"] == (last_end - 179) * 1000 and trade["result_r"] == 2.5
    assert all(result[k] is None for k in ("expectancy_r", "expectancy_bounds_r", "total_r", "win_rate"))


@pytest.mark.parametrize("boundary", ["before_start", "start", "before_end", "end"])
def test_ordinal_boundary(client, monkeypatch, boundary):
    start = int(datetime.fromisoformat("2025-01-10T12:00Z").timestamp())
    end = start + 1800
    bars, quotes = inputs()
    bars[3].update(high=106, low=98)
    for bar in bars:
        bar["time"] += start
    for row in quotes:
        row["time_ms"] += start * 1000
        row.update(time=row["time_ms"] // 1000, source_sha256="a" * 64)
    archive("candles", bars)
    params = dict(source="DUKASCOPY_SAVED", timeframe="M1", start=start, end=end)
    clean = archive("ticks", quotes)
    control = client.get("/api/backtest", params=params | {"tick_dataset_id": clean["id"]})
    assert control.status_code == 200
    expected = control.json()
    assert expected["trades"][0]["exit_time_ms"] == (start + 181) * 1000

    def uncatalog(part):
        with db.transaction() as conn:
            conn.execute("DELETE FROM datasets WHERE id=?", (part["id"],))

    uncatalog(clean)
    ms = {"before_start": start * 1000 - 1, "start": start * 1000,
          "before_end": end * 1000 - 1, "end": end * 1000}[boundary]
    bad = dict(quotes[0], time_ms=ms, time=ms // 1000, source_order=None)
    packed = archive("ticks", [*quotes, bad])

    def probe(**choice):
        request = params | choice
        return (client.get("/api/backtest", params=request),
                client.post("/api/backtest/runs", json=request))

    responses = [probe(tick_dataset_id=packed["id"]), probe(tick_archive_range=True)]
    uncatalog(packed)
    archive("ticks", quotes[:2])
    archive("ticks", [*quotes[2:], bad])
    responses.append(probe(tick_archive_range=True))
    select = main.tick_partitions
    monkeypatch.setattr(main, "tick_partitions", lambda *args: select(*args)[::-1])
    responses.append(probe(tick_archive_range=True))
    outside = boundary in ("before_start", "end")
    statuses = [(get.status_code, post.status_code) for get, post in responses]
    assert statuses == ([(200, 201)] if outside else [(422, 422)]) * 4
    for get, post in responses:
        if outside:
            result = get.json()
            assert post.json()["result"] == result
            assert {k: v for k, v in result.items() if k != "lineage"} == {
                k: v for k, v in expected.items() if k != "lineage"}
        else:
            assert get.json() == post.json() == {"detail": "Invalid tick timestamp or source order"}
    if not outside:
        assert db.one("SELECT count(*) AS n FROM backtest_runs")["n"] == 0
