"""Independent trust fixtures for the normal UI, quote and AI surfaces."""
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from research_backend.app.db import one, transaction
from research_backend.app.main import app
from research_backend.app.storage import partition_path, write_partition
from research_backend.tests.test_tick_replay import inputs


def saved_tick_run(client, *, multi=False, incomplete=False):
    candles, ticks = inputs()
    candles[3].update(high=106, low=98)
    if incomplete:
        ticks = ticks[:2]
    for quote in ticks:
        quote["time"] = quote["time_ms"] // 1000
    write_partition("DUKASCOPY", "review", "EURUSD", "candles", "M1", "BID", candles)
    partitions = [ticks[:2], ticks[2:]] if multi else [ticks]
    ids = [write_partition("DUKASCOPY", "review", "EURUSD", "ticks", None, "BID_ASK_UNITS", quotes)["id"]
           for quotes in partitions]
    inputs_for_run = {"tick_archive_range": True} if multi else {"tick_dataset_id": ids[0]}
    result = client.post("/api/backtest/runs", json={"timeframe": "M1", "source": "DUKASCOPY_SAVED", **inputs_for_run})
    assert result.status_code == 201, result.text
    return result.json(), ids


def store_fixture(snapshot):
    payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    run_id = sha256(payload.encode()).hexdigest()
    result = snapshot["result"]
    summary = {key: result[key] for key in ("count", "expectancy_r", "symbol", "source", "timeframe", "synthetic")}
    summary["model"] = "TICK"
    with transaction() as db:
        db.execute("INSERT INTO backtest_runs VALUES(?,?,?,?)",
                   (run_id, json.dumps(summary), payload, "2024-01-01T00:00:00+00:00"))
    return run_id, payload


@pytest.mark.parametrize("historical_r", [99, -99])
def test_legacy_performance_is_restricted_after_restart_and_in_export(client, plan, historical_r):
    current, _ = saved_tick_run(client, multi=True)
    assert current["result"]["trust"]["status"] == "VERIFIED_PROVENANCE"
    assert current["result"]["expectancy_r"] == 2.5
    raw = client.get(f"/api/backtest/runs/{current['id']}/audit").json()["original"]
    raw["engine_version"] = 1
    result = raw["result"]
    del result["lineage"]["tick_ordering"]
    result.update(expectancy_r=historical_r, total_r=historical_r)
    result["trades"][0].update(result_r=historical_r, exit=999, exit_reason="TARGET")
    legacy_id, payload = store_fixture(raw)
    with TestClient(app) as restarted:
        base = f"/api/backtest/runs/{legacy_id}"
        reopened = restarted.get(base).json()["result"]
        assert reopened["trust"]["status"] == "LEGACY_UNVERIFIED_ORDER"
        assert reopened["trades"] == [] and reopened["count"] == 0
        assert all(reopened[key] is None for key in ("expectancy_r", "total_r", "win_rate", "expectancy_bounds_r"))
        assert restarted.get(f"{base}/trades/0/quotes").status_code == 409
        comparison = {entry["id"]: entry for entry in restarted.get("/api/backtest/runs").json()}
        assert comparison[legacy_id]["expectancy_r"] is None
        assert comparison[legacy_id]["trust"]["performance_eligible"] is False
        assert comparison[current["id"]]["expectancy_r"] == 2.5
        audit = restarted.get(f"{base}/audit").json()
        assert audit["audit_only"] and audit["original"]["result"]["trades"][0]["result_r"] == historical_r
        uid = restarted.post("/api/trades", json=plan).json()["id"]
        export = restarted.post(f"/api/ai/exports/{uid}", params={"backtest_run_id": legacy_id})
        assert export.status_code == 201, export.text
        package = restarted.get("/api/ai/packages/" + export.json()["package_id"] + "/download")
        with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
            evidence = json.loads(archive.read("manifest.json"))["backtest_run"]
            assert evidence["trust"]["status"] == "LEGACY_UNVERIFIED_ORDER"
            assert evidence["performance_included"] is False
            assert "expectancy_r" not in evidence and "trades" not in evidence
            assert b"LEGACY_UNVERIFIED_ORDER" in archive.read("AI_CONTEXT.md")
        rerun = restarted.post(f"{base}/rerun")
        assert rerun.status_code == 201 and rerun.json()["result"]["expectancy_r"] == 2.5
    assert one("SELECT payload FROM backtest_runs WHERE id=?", (legacy_id,))["payload"] == payload


def test_single_partition_historical_result_is_recomputed_and_marker_alone_is_insufficient(client):
    original, _ = saved_tick_run(client)
    raw = client.get(f"/api/backtest/runs/{original['id']}/audit").json()["original"]
    raw["engine_version"] = 1
    del raw["result"]["lineage"]["tick_ordering"]
    historical, _ = store_fixture(raw)
    result = client.get(f"/api/backtest/runs/{historical}").json()["result"]
    assert result["trust"]["status"] == "VERIFIED_PROVENANCE" and result["expectancy_r"] == 2.5
    raw["result"]["lineage"]["tick_ordering"] = "PROVENANCE_V1"
    raw["result"]["trades"][0]["result_r"] = 99
    altered, _ = store_fixture(raw)
    assert client.get(f"/api/backtest/runs/{altered}").json()["result"]["trust"]["status"] == "SOURCE_INTEGRITY_FAILURE"


def test_corrupt_input_restricts_details_summary_and_warm_quote_path(client):
    saved, sources = saved_tick_run(client)
    base = f"/api/backtest/runs/{saved['id']}"
    quote_url = base + "/trades/0/quotes"
    assert client.get(quote_url).status_code == 200
    path = partition_path(one("SELECT * FROM datasets WHERE id=?", (sources[0],)))
    with path.open("ab") as file:
        file.write(b"tampered")
    detail = client.get(base).json()["result"]
    assert detail["trust"]["status"] == "SOURCE_INTEGRITY_FAILURE" and detail["trades"] == []
    assert client.get("/api/backtest/runs").json()[0]["expectancy_r"] is None
    assert client.get(quote_url).status_code == 409


def test_incomplete_and_synthetic_replays_have_distinct_trust(client):
    incomplete, _ = saved_tick_run(client, incomplete=True)
    checked = incomplete["result"]
    assert checked["trust"]["status"] == "INCOMPLETE_EXECUTION_DATA"
    assert checked["trades"] == [] and checked["expectancy_r"] is None
    assert checked["unresolved_outcomes"][0]["reason"] == "EXECUTION_DATA_ENDED"
    demo = client.post("/api/backtest/runs", json={"source": "DEMO"}).json()["result"]
    assert demo["trust"]["status"] == "SYNTHETIC_RESEARCH"
    assert demo["trust"]["performance_eligible"] is False


def test_concurrent_pagination_keeps_exact_cross_partition_boundary(client):
    saved, _ = saved_tick_run(client, multi=True)
    url = f"/api/backtest/runs/{saved['id']}/trades/0/quotes"
    def page(offset):
        response = client.get(url, params={"offset": offset, "limit": 1})
        assert response.status_code == 200, response.text
        return response.json()
    with ThreadPoolExecutor(max_workers=4) as pool:
        first, second, repeat_first, repeat_second = pool.map(page, (0, 1, 0, 1))
    assert first == repeat_first and second == repeat_second
    assert [(page["items"][0]["time_ms"], page["items"][0]["bid_units"]) for page in (first, second)] == [
        (180_000, 100), (181_000, 106)
    ]
    assert first["next_offset"] == 1 and second["next_offset"] is None
