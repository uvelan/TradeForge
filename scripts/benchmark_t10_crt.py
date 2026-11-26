"""Pair the unchanged saved-replay workload and new H1 CRT reads on verified provider data."""

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from tempfile import TemporaryDirectory

from scripts.benchmark_t10_structure_liquidity import checked, install_counters, replay, sample, summary
from scripts.benchmark_t10_zones import (
    BACKEND_COUNTERS, DIRECTION, INSPECTION, OVERLAYS, SESSION_ROUTE, CountedClient, at, overlay_update,
)
from scripts.verify_t10_structure_liquidity import END, START, load_provider, utc


ROOT = Path(__file__).resolve().parents[1]
LANES = ("primary", "comparison")
SELECTED_REFERENCE = START + 13 * 3600
HTTP_COUNTERS = ("http_requests", "http_request_bytes", "http_response_bytes",
                 "returned_range_rows", "returned_revision_rows", "returned_source_rows")


def worker(operation, session_id, source):
    from fastapi.testclient import TestClient
    from research_backend.app.main import app

    with TestClient(app) as raw:
        if operation == "prepare":
            return replay(raw, source)

        counts = install_counters()
        client = CountedClient(raw, counts)
        rss_peak = runpy.run_path(str(ROOT / "docs/implementation/evidence/TF-I08C/benchmark.py"))["rss_peak"]
        route = f"{SESSION_ROUTE}/{session_id}"

        def reopen():
            result = checked(client.get(route))
            assert result["checkpoint"]["position"] == 132
            return result

        def crt(view, cutoff, **params):
            return client.get(f"{route}/crt", params={"view": view, "cutoff": cutoff, **params})

        def read_crt(view, cutoff, **params):
            result = checked(crt(view, cutoff, **params))
            assert result["authority"] == "RESEARCH_ONLY"
            counts["returned_range_rows"] += len(result["ranges"])
            selected = result["selected"] or {}
            counts["returned_revision_rows"] += len(selected.get("revisions", []))
            counts["returned_source_rows"] += len(selected.get("evidence", [])) + len(selected.get("reference_children", []))
            return result

        if operation in {"cold-with-overlays", "cold-only"}:
            def cold():
                session = reopen()
                if operation == "cold-with-overlays":
                    overlay_update(client, session, session["market"]["candles"][132]["available_at"])
            measurement, _ = sample(cold, counts)
            return {"sample": measurement, "peak_kib": rss_peak()}

        session = reopen()
        for ordinal in (103, 104, 105):
            overlay_update(client, session, at(ordinal))
        activities = {
            label: [sample(lambda: overlay_update(client, session, at(ordinal)), counts)[0] for ordinal in ordinals]
            for label, ordinals in DIRECTION.items()
        }
        common_peak = rss_peak()
        status = crt("primary", at(INSPECTION[0])).status_code
        assert status in (200, 404), status
        selected_ids = {}
        if status == 200:
            def listed(view, ordinal):
                result = read_crt(view, at(ordinal))
                assert result["total"] > 0 and result["selected"] is None
                return next(item["range_id"] for item in result["ranges"]
                            if item["reference_start"] == SELECTED_REFERENCE)

            def selected(view, ordinal, family):
                result = read_crt(view, at(ordinal), range_id=family)["selected"]
                assert result["range_id"] == family and result["reference_start"] == SELECTED_REFERENCE
                assert len(result["reference_children"]) == 12 and result["evidence"]
                assert result["state"] == ("INVALIDATED" if view == "primary" else "OPPOSITE_BOUNDARY_REACHED")

            for view in LANES:
                label = "EURUSD" if view == "primary" else "GBPUSD"
                for ordinal in INSPECTION[:3]:
                    selected(view, ordinal, listed(view, ordinal))

                activities[f"{label}_new_list"] = []
                for ordinal in INSPECTION:
                    timing, family = sample(lambda: listed(view, ordinal), counts)
                    activities[f"{label}_new_list"].append(timing)
                    assert selected_ids.setdefault(label, family) == family
                activities[f"{label}_new_selection"] = [
                    sample(lambda: selected(view, ordinal, selected_ids[label]), counts)[0] for ordinal in INSPECTION
                ]
                activities[f"{label}_new_list_and_selection"] = [
                    sample(lambda: selected(view, ordinal, listed(view, ordinal)), counts)[0] for ordinal in INSPECTION
                ]
        return {"timings": activities, "endpoint_status": status, "common_peak_kib": common_peak,
                "all_peak_kib": rss_peak(), "selected_range_ids": selected_ids}


def measure(repo, source, cold_samples):
    started = datetime.now(UTC).isoformat(timespec="seconds")
    scratch = ROOT / "storage"
    scratch.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="t10-crt-benchmark-", dir=scratch) as workspace:
        env = {**os.environ, "TRADEFORGE_STORAGE": workspace,
               "TRADEFORGE_DATA_DIR": str(Path(workspace) / "research"),
               "PYTHONPATH": os.pathsep.join((str(repo), str(ROOT)))}

        def execute(operation, session_id=None):
            command = [sys.executable, str(Path(__file__).resolve()), "--worker", operation,
                       "--source-storage", str(source)]
            if session_id:
                command.extend(("--session-id", session_id))
            return json.loads(subprocess.check_output(command, cwd=repo, env=env, text=True))

        session = execute("prepare")
        cold = {"cold-only": [], "cold-with-overlays": []}
        for _ in range(cold_samples):
            for operation, measurements in cold.items():
                measurements.append(execute(operation, session))
        warm = execute("warm", session)

    timings = {label: summary([item["sample"] for item in measurements]) for label, measurements in cold.items()}
    timings.update({label: summary(items) for label, items in warm["timings"].items()})
    for timing in timings.values():
        for counter in (*HTTP_COUNTERS, *BACKEND_COUNTERS):
            timing["counts_per_sample_min_max"].setdefault(counter, [0, 0])
    return {
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "tracked_worktree_diff": bool(subprocess.check_output(["git", "diff", "HEAD", "--name-only"], cwd=repo)),
        "started_at": started, "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.splitlines()[0], "endpoint_status": warm["endpoint_status"],
        "selected_range_ids": warm["selected_range_ids"],
        "peak_working_set_kib": {
            **{label: [item["peak_kib"] for item in measurements] for label, measurements in cold.items()},
            "warm_common": warm["common_peak_kib"], "warm_with_inspection": warm["all_peak_kib"],
        },
        "timings": timings,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", required=True, type=Path)
    parser.add_argument("--baseline-repo", type=Path)
    parser.add_argument("--repo", default=ROOT, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cold-samples", type=int, default=8)
    parser.add_argument("--worker", choices=("prepare", "cold-only", "cold-with-overlays", "warm"))
    parser.add_argument("--session-id")
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker, args.session_id, args.source_storage)))
        return
    if args.baseline_repo is None or args.output is None or args.cold_samples < 2:
        parser.error("Paired measurement requires --baseline-repo, --output and at least two cold samples")
    source = args.source_storage.resolve()
    result = {
        "method": "Serial FastAPI TestClient on the same host. Both builds reopen the same pinned paired M5 replay "
                  "in a fresh backend, excluding startup/import. Step and rewind run unchanged FVG and OB overlays "
                  "with selection collapsed. The candidate also lists and selects the genuine 13:00 UTC H1 terminal "
                  "family on each pane. Timings exclude the preliminary route probe and warm-up. OS cache is uncontrolled.",
        "counter_scope": "HTTP requests and JSON body bytes; SQLite SELECTs and verified artifact snapshot bytes; "
                         "actual Parquet opens and decoded rows. Evidence rows count both reference_children and evidence "
                         "records in the response, including repetitions across these arrays.",
        "coverage_utc": [utc(START), utc(END)],
        "cutoff_ordinals": {**DIRECTION, "inspection": INSPECTION},
        "collapsed_overlays": OVERLAYS,
        "pins": {symbol: load_provider(source, symbol)[3] for symbol in ("EURUSD", "GBPUSD")},
        "baseline": measure(args.baseline_repo.resolve(), source, args.cold_samples),
        "candidate": measure(args.repo.resolve(), source, args.cold_samples),
    }
    assert (result["baseline"]["endpoint_status"], result["candidate"]["endpoint_status"]) == (404, 200)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
