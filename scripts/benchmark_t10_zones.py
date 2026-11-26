"""Paired Windows timings for the saved November 13 FVG and order-block replay."""

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from tempfile import TemporaryDirectory

from scripts.benchmark_t10_structure_liquidity import (
    checked, evaluate, install_counters, replay, sample, summary,
)
from scripts.verify_t10_zones import END, PROFILE, START, STEP


ROOT = Path(__file__).resolve().parents[1]
SESSION_ROUTE = "/api/replay/sessions"
BACKEND_COUNTERS = ("decoded_rows", "sql_selects", "parquet_opens", "verified_snapshot_bytes")
HTTP_COUNTERS = ("http_requests", "http_request_bytes", "http_response_bytes", "returned_zone_rows",
                 "returned_evidence_rows")
DIRECTION = {"collapsed_step": list(range(106, 136)), "collapsed_rewind": list(range(135, 105, -1))}
INSPECTION = list(range(234, 264))
OVERLAYS = [
    {"id": "fair_value_gap", "fvg": {"market_schedule": PROFILE["market_schedule"]},
     "fvg_delivery": {"formation_limit": 12, "revision_limit": 12, "current_limit": 12}},
    {"id": "order_blocks", "structure": PROFILE,
     "ob_delivery": {"formation_limit": 12, "revision_limit": 12, "current_limit": 12}},
]


class CountedClient:
    def __init__(self, client, counters):
        self.client = client
        self.counters = counters

    def record(self, response):
        self.counters["http_requests"] += 1
        self.counters["http_request_bytes"] += len(response.request.content)
        self.counters["http_response_bytes"] += len(response.content)
        return response

    def get(self, *args, **kwargs):
        return self.record(self.client.get(*args, **kwargs))

    def post(self, *args, **kwargs):
        return self.record(self.client.post(*args, **kwargs))


def at(ordinal):
    return START + ordinal * STEP


def overlay_update(client, session, cutoff):
    for pane in (session["market"], session["views"]["comparison"]):
        results = evaluate(client, pane, cutoff, OVERLAYS)
        assert "current_fvgs" in results[0] and "current_obs" in results[1], results


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
            session = checked(client.get(route))
            assert session["checkpoint"]["position"] == 132
            return session

        def read_zones(cutoff, **params):
            return client.get(f"{route}/zones", params={"cutoff": cutoff, "view": "primary",
                                                        "mitigation": "wick", **params})

        def checked_zones(cutoff, **params):
            payload = checked(read_zones(cutoff, **params))
            counts["returned_zone_rows"] += len(payload["zones"])
            selected = payload["selected"] or {}
            counts["returned_evidence_rows"] += len(selected.get("evidence", []))
            counts["returned_evidence_rows"] += len((selected.get("structure") or {}).get("evidence", []))
            return payload

        if operation in {"cold", "cold-no-overlays"}:
            def cold():
                session = reopen()
                if operation == "cold":
                    overlay_update(client, session, session["market"]["candles"][132]["available_at"])
            measurement, _ = sample(cold, counts)
            return {"sample": measurement, "peak_kib": rss_peak()}

        session = reopen()
        for ordinal in (103, 104, 105):
            overlay_update(client, session, at(ordinal))
        result = {
            key: [sample(lambda: overlay_update(client, session, at(ordinal)), counts)[0]
                  for ordinal in ordinals]
            for key, ordinals in DIRECTION.items()
        }
        result["peak_common_kib"] = rss_peak()
        result["zones_status"] = read_zones(at(104)).status_code
        if result["zones_status"] == 200:
            def zone_list(ordinal):
                output = checked_zones(at(ordinal))
                assert output["zones"]
                return output["zones"]

            selected_ids = {}
            for ordinal in (103, 104, 105):
                zones = zone_list(ordinal)
                checked_zones(at(ordinal), zone_id=zones[-1]["zone_id"])
            result["new_list"] = []
            for ordinal in INSPECTION:
                elapsed, zones = sample(lambda: zone_list(ordinal), counts)
                selected_ids[ordinal] = next(zone["zone_id"] for zone in reversed(zones) if zone["type"] == "OB")
                result["new_list"].append(elapsed)

            def selection(ordinal):
                zone_id = selected_ids[ordinal]
                value = checked_zones(at(ordinal), zone_id=zone_id)["selected"]
                assert value["zone_id"] == zone_id and value["structure"] and value["evidence"]

            result["new_selection"] = [sample(lambda: selection(ordinal), counts)[0] for ordinal in INSPECTION]
            result["new_list_and_selection"] = []
            for ordinal in INSPECTION:
                def both():
                    zone_list(ordinal)
                    selection(ordinal)
                result["new_list_and_selection"].append(sample(both, counts)[0])
        result["peak_kib"] = rss_peak()
        return result


def measure(repo, source, cold_samples):
    started = datetime.now(UTC).isoformat(timespec="seconds")
    scratch = ROOT / "storage"
    scratch.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix="t10-zones-benchmark-", dir=scratch) as workspace:
        env = {**os.environ, "TRADEFORGE_STORAGE": workspace,
               "TRADEFORGE_DATA_DIR": str(Path(workspace) / "research"),
               "PYTHONPATH": os.pathsep.join((str(repo), str(ROOT)))}

        def execute(operation, session_id=None):
            cmd = [sys.executable, str(Path(__file__).resolve()), "--source-storage", str(source),
                   "--worker", operation]
            if session_id:
                cmd.extend(("--session-id", session_id))
            return json.loads(subprocess.check_output(cmd, cwd=repo, env=env, text=True))

        session = execute("prepare")
        cold = {"cold_reopen_and_overlays": [], "cold_reopen_only": []}
        for _ in range(cold_samples):
            cold["cold_reopen_only"].append(execute("cold-no-overlays", session))
            cold["cold_reopen_and_overlays"].append(execute("cold", session))
        warm = execute("warm", session)
    timings = {key: summary([value["sample"] for value in values]) for key, values in cold.items()}
    timings.update({key: summary(items) for key, items in warm.items() if isinstance(items, list)})
    for timing in timings.values():
        for key in (*HTTP_COUNTERS, *BACKEND_COUNTERS):
            timing["counts_per_sample_min_max"].setdefault(key, [0, 0])
    return {
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "started_at": started, "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "python": sys.version.splitlines()[0], "coverage_utc": [START, END],
        "method": "Serial FastAPI TestClient requests on the same host with checksum-pinned EURUSD and GBPUSD M5. "
                  "Cold reopens run in fresh backend processes, excluding startup, archive import and host OS-cache "
                  "eviction. Both common warm paths run unchanged FVG and OB overlays for both panes with inspection "
                  "collapsed. New work measures one-pane list, explicit OB selection and a combined pass. "
                  "The selection requests carry an ID acquired in the preceding list pass.",
        "counter_scope": "Application SQLite SELECTs, verification bytes copied into snapshots, Parquet opens "
                         "and decoded rows. HTTP byte counts use JSON request and response bodies. Returned "
                         "evidence counts sum the direct evidence and structure producer rows, where present.",
        "overlays": OVERLAYS, "cutoff_ordinals": {**DIRECTION, "new": INSPECTION},
        "zones_http_status": warm["zones_status"],
        "peak_working_set_kib": {
            **{key: [item["peak_kib"] for item in values] for key, values in cold.items()},
            "warm_common": warm["peak_common_kib"], "warm_all": warm["peak_kib"],
        },
        "timings": timings,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", required=True, type=Path)
    parser.add_argument("--repo", default=ROOT, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cold-samples", type=int, default=8)
    parser.add_argument("--worker", choices=("prepare", "cold", "cold-no-overlays", "warm"))
    parser.add_argument("--session-id")
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker, args.session_id, args.source_storage)))
        return
    if args.output is None or args.cold_samples < 2:
        parser.error("--output and at least two cold samples are required")
    result = measure(args.repo.resolve(), args.source_storage.resolve(), args.cold_samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
