"""Measure the pinned November 13 saved-chart workload in fresh and warm backend processes."""

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import runpy
from statistics import median, quantiles
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

from scripts.verify_t10_structure_liquidity import (
    END, START, STEP, SYMBOLS, PROFILE, load_provider,
)


ROOT = Path(__file__).resolve().parents[1]
SESSIONS = "/api/replay/sessions"
EVALUATE = "/api/indicators/evaluate"
SCHEDULE = {
    "warm_step": list(range(106, 136)),
    "warm_rewind": list(range(135, 105, -1)),
    "older_page": list(range(234, 264)),
}
SPECIFICATIONS = [
    {"id": "market_structure", "structure": PROFILE,
     "structure_delivery": {"pivot_limit": 8, "break_limit": 8}},
    {"id": "liquidity_sweeps", "structure": PROFILE,
     "liquidity_delivery": {"pool_limit": 8, "sweep_limit": 8, "current_limit": 8}},
]


def checked(response, expected=200):
    assert response.status_code == expected, (response.request.url, response.status_code, response.text)
    return response.json()


def replay(client, storage):
    for symbol in SYMBOLS:
        _, _, path, pin = load_provider(storage, symbol)
        receipt = checked(client.post("/api/market/import/reviewed", files={"file": (path.name, path.read_bytes())}))
        assert {"id": receipt["partitions"][0]["id"], "sha256": receipt["source_sha256"]} == pin
    session = checked(client.post(SESSIONS, json={
        "source": "DUKASCOPY_SAVED", "symbol": "EURUSD", "timeframe": "M5", "start": START, "end": END,
        "views": [{"view": "comparison", "symbol": "GBPUSD", "timeframe": "M5"}],
    }), 201)
    assert len(session["market"]["candles"]) == len(session["views"]["comparison"]["candles"]) == 264
    assert checked(client.post(f"{SESSIONS}/{session['id']}/checkpoint", json={"position": 132}))["position"] == 132
    return session["id"]


def evaluate(client, pane, cutoff, specifications=SPECIFICATIONS):
    scope = {key: pane[key] for key in
             ("source", "symbol", "timeframe", "feed_id", "price_basis", "partitions")}
    if pane.get("dataset_id") is not None:
        scope["dataset_id"] = pane["dataset_id"]
    results = checked(client.post(EVALUATE, json={
        **scope, "cutoff": cutoff, "frozen": True, "load_reference": True,
        "timing_policy": "LEGACY_RESEARCH", "indicators": specifications,
        "candles": [row for row in pane["candles"] if row["available_at"] <= cutoff],
    }))["results"]
    assert len(results) == len(specifications)
    return results


def panes(session):
    return session["market"], session["views"]["comparison"]


def legacy(client, session, cutoff):
    return [evaluate(client, pane, cutoff) for pane in panes(session)]


def reference_pages(client, session, cutoff):
    result = legacy(client, session, cutoff)
    for pane, (structure, liquidity) in zip(panes(session), result):
        pivot = structure["event_page"]["pivots"]["next_cursor"]
        pool = liquidity["liquidity_page"]["pools"]["next_cursor"]
        assert pivot and pool
        evaluate(client, pane, cutoff, [{
            "id": "market_structure", "structure": PROFILE,
            "structure_delivery": {"break_limit": 0, "pivot_limit": 8, "pivot_cursor": pivot},
        }])
        evaluate(client, pane, cutoff, [{
            "id": "liquidity_sweeps", "structure": PROFILE,
            "liquidity_delivery": {"current_limit": 0, "sweep_limit": 0, "pool_limit": 8, "pool_cursor": pool},
        }])


def install_counters():
    # Count calls at the actual application boundary. These are not disk-cache misses.
    import pyarrow.parquet as parquet
    from research_backend.app import db, replay_hour

    counts = Counter()
    original_connect = db.connect
    original_hash = hashlib.file_digest
    original_partition = replay_hour.iter_complete_partition
    verify_hook_installed = False
    original_file = parquet.ParquetFile

    def connect():
        connection = original_connect()
        counts["replay_database_connections"] += 1

        def trace(statement):
            counts["sql_statements"] += 1
            if statement.lstrip().upper().startswith("SELECT"):
                counts["sql_selects"] += 1

        connection.set_trace_callback(trace)
        return connection

    def hash_file(*args, **kwargs):
        counts["file_digest_calls"] += 1
        return original_hash(*args, **kwargs)

    def partition(*args, **kwargs):
        # Preserve the application's first-use import and memory behavior.
        nonlocal verify_hook_installed
        if not verify_hook_installed:
            from tradeforge import catalog

            original_verify = catalog.verify_artifact

            def verify_path(path, checksum, snapshot=None):
                counts["verified_artifacts"] += 1
                result = original_verify(path, checksum, snapshot)
                if snapshot is not None:
                    counts["verified_snapshot_bytes"] += snapshot.tell()
                return result

            catalog.verify_artifact = verify_path
            verify_hook_installed = True
        return original_partition(*args, **kwargs)

    class CountedParquet(original_file):
        def __init__(self, *args, **kwargs):
            counts["parquet_opens"] += 1
            super().__init__(*args, **kwargs)

        def read_row_groups(self, *args, **kwargs):
            table = super().read_row_groups(*args, **kwargs)
            counts["decoded_rows"] += table.num_rows
            return table

        def iter_batches(self, *args, **kwargs):
            for batch in super().iter_batches(*args, **kwargs):
                counts["decoded_rows"] += batch.num_rows
                yield batch

    db.connect = connect
    hashlib.file_digest = hash_file
    replay_hour.iter_complete_partition = partition
    parquet.ParquetFile = CountedParquet
    return counts


def sample(callback, counts):
    counts.clear()
    start = perf_counter()
    result = callback()
    return {"elapsed_ms": (perf_counter() - start) * 1000, "counters": dict(counts)}, result


def inspection(client, session_id, cutoff, *, view="primary", event_id=None):
    endpoint = f"{SESSIONS}/{session_id}/structure-liquidity"
    params = {"cutoff": cutoff, "view": view}
    if event_id:
        params["event_id"] = event_id
    return client.get(endpoint, params=params)


def worker(operation, session_id, storage):
    from fastapi.testclient import TestClient
    from research_backend.app import db
    from research_backend.app.main import app

    with TestClient(app) as client:
        if operation == "prepare":
            return replay(client, storage)
        counts = install_counters()
        rss_peak = runpy.run_path(str(ROOT / "docs/implementation/evidence/TF-I08C/benchmark.py"))["rss_peak"]
        route = f"{SESSIONS}/{session_id}"

        def reopen():
            session = checked(client.get(route))
            assert session["checkpoint"]["position"] == 132
            return session

        if operation in {"cold", "cold-panel"}:
            def cold_reopen():
                session = reopen()
                cutoff = session["market"]["candles"][132]["available_at"]
                legacy(client, session, cutoff)
                if operation == "cold-panel":
                    assert checked(inspection(client, session_id, cutoff))["events"]

            result, _ = sample(cold_reopen, counts)
            return {"cold_reopen": [result], "peak_process_kib": rss_peak()}

        session = reopen()
        reopen_only = [sample(reopen, counts)[0] for _ in range(30)]
        for index in (102, 103, 104):
            legacy(client, session, START + index * STEP)
        result = {"warm_reopen_only": reopen_only}
        warm = lambda cutoff: legacy(client, session, cutoff)
        older = lambda cutoff: reference_pages(client, session, cutoff)
        for name, workload in SCHEDULE.items():
            callback = older if name == "older_page" else warm
            result[name] = [sample(lambda: callback(START + index * STEP), counts)[0] for index in workload]
        result["peak_common_process_kib"] = rss_peak()
        probe_cutoff = START + 104 * STEP
        probe = [inspection(client, session_id, probe_cutoff, view=view) for view in ("primary", "comparison")]
        result["inspection_status"] = [response.status_code for response in probe]
        if all(response.status_code == 200 for response in probe):
            def evidence(cutoff):
                return checked(inspection(client, session_id, cutoff))

            def active_panel(cutoff):
                legacy(client, session, cutoff)
                evidence(cutoff)

            def selected_receipt(cutoff):
                result = evidence(cutoff)
                wanted = result["events"][-1]["id"]
                selected = checked(inspection(client, session_id, cutoff, event_id=wanted))["selected"]
                assert selected["id"] == wanted and selected["evidence"]

            for index in (102, 103, 104):
                evidence(START + index * STEP)
            for name in ("warm_step", "warm_rewind"):
                result[f"open_panel_{name}"] = [
                    sample(lambda: active_panel(START + index * STEP), counts)[0] for index in SCHEDULE[name]
                ]
            result["new_panel_open"] = [
                sample(lambda: evidence(START + index * STEP), counts)[0] for index in SCHEDULE["older_page"]
            ]
            result["new_evidence_inspection"] = [
                sample(lambda: selected_receipt(START + index * STEP), counts)[0] for index in SCHEDULE["older_page"]
            ]
        result["peak_process_kib"] = rss_peak()
        return result


def summary(samples):
    latency = [sample["elapsed_ms"] for sample in samples]
    keys = {"replay_database_connections", "sql_statements", "sql_selects", "file_digest_calls",
            "parquet_opens", "decoded_rows", "verified_artifacts", "verified_snapshot_bytes"} | {
                key for item in samples for key in item["counters"]
            }
    return {
        "samples": len(samples),
        "p50_ms": round(median(latency), 3),
        "p95_ms": round(quantiles(latency, n=100, method="inclusive")[94], 3),
        "min_ms": round(min(latency), 3),
        "max_ms": round(max(latency), 3),
        "counts_per_sample_min_max": {
            key: [min(item["counters"].get(key, 0) for item in samples),
                  max(item["counters"].get(key, 0) for item in samples)]
            for key in sorted(keys)
        },
        "latency_ms": [round(value, 3) for value in latency],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", choices=("prepare", "cold", "cold-panel", "warm"))
    parser.add_argument("--session-id")
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker, args.session_id, args.source_storage)))
        return

    assert args.output is not None
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    source = args.source_storage.resolve()
    scratch = ROOT / "storage"
    scratch.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="t10-structure-benchmark-", dir=scratch) as workspace:
        env = {**os.environ, "TRADEFORGE_STORAGE": workspace,
               "TRADEFORGE_DATA_DIR": str(Path(workspace) / "research")}

        def execute(operation, session_id=None):
            cmd = [sys.executable, "-m", "scripts.benchmark_t10_structure_liquidity",
                   "--source-storage", str(source), "--worker", operation]
            if session_id is not None:
                cmd.extend(("--session-id", session_id))
            return json.loads(subprocess.check_output(cmd, env=env, text=True))

        session = execute("prepare")
        cold = [execute("cold", session) for _ in range(8)]
        warm = execute("warm", session)
        cold_panel = ([execute("cold-panel", session) for _ in range(8)]
                      if warm["inspection_status"] == [200, 200] else [])
    timings = {"cold_reopen": summary([item["cold_reopen"][0] for item in cold])}
    if cold_panel:
        timings["cold_reopen_then_open_panel"] = summary([item["cold_reopen"][0] for item in cold_panel])
    for key, value in warm.items():
        if key not in {"inspection_status", "peak_process_kib", "peak_common_process_kib"}:
            timings[key] = summary(value)
    receipt = {
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "python": sys.version.splitlines()[0],
        "session_id": session,
        "coverage_utc": [START, END],
        "method": "Serial TestClient HTTP/JSON through public handlers. Eight independent backend processes; "
                  "process spawn/startup, archive import, and OS disk-cache eviction excluded. "
                  "Cold reopen loads checkpoint and evaluates both panes through the existing indicator API. "
                  "Warm step/rewind measures both panes; older page includes the current response and one "
                  "older pivot and pool page in each pane. The new lazy panel opens on one selected pane. "
                  "Open-panel step/rewind includes both legacy indicator calls plus one new list request. "
                  "New inspection includes list and explicit selection with the full receipt. "
                  "These serial backend/API timings omit browser rendering, UI scheduling and transport.",
        "counter_scope": "Instrumented research db.connect and SQL trace, hashlib.file_digest calls, full "
                         "verification calls plus actual bytes copied into verified snapshots, and "
                         "Parquet constructor, read_row_groups and iter_batches. Physical file I/O and "
                         "SQLite engine I/O are unobserved; zero means no instrumented calls.",
        "legacy_specs": SPECIFICATIONS,
        "lane_cutoff_ordinals": SCHEDULE,
        "new_inspection_http_status": warm["inspection_status"],
        "peak_process_kib": {"cold_samples": [item["peak_process_kib"] for item in cold],
                             "cold_open_panel": [item["peak_process_kib"] for item in cold_panel],
                             "warm_common": warm["peak_common_process_kib"], "warm": warm["peak_process_kib"]},
        "timings": timings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
