"""Exercise each public replay entry point against the same physical quote layouts."""
from datetime import datetime

import pytest

from research_backend.app import db, main
from test_t06_replay import archive
from test_tick_replay import inputs, tick


@pytest.mark.parametrize("case", [
    "trailing", "leading", "exclusive_end", "clean_weekend", "distinct_sources", "ordinal_collision",
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
