"""Benchmark cold and repeated CRT–SMT replay reads on the pinned November 13 pair."""

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys
from tempfile import TemporaryDirectory

from scripts.benchmark_t10_structure_liquidity import checked, install_counters, sample, summary
from scripts.benchmark_t10_zones import CountedClient
from scripts.verify_t10_confluence import (
    DAY, END, PROFILE, ROOT, START, STEP, genuine, local_environment, read_confluence, replay, write_json,
)
from scripts.verify_t10_crt import SCHEDULE
from scripts.verify_t10_structure_liquidity import SYMBOLS, load_provider, utc

ALIGNMENT = DAY + 13 * 3600 + 25 * 60
POSITION = (ALIGNMENT - START) // STEP - 1
INSPECTION_CLOCKS = (ALIGNMENT - STEP, ALIGNMENT, ALIGNMENT + STEP, ALIGNMENT + 7*STEP,
                     ALIGNMENT + 12*STEP, ALIGNMENT + 27*STEP, END)
METRICS = ("http_requests", "http_request_bytes", "http_response_bytes", "sql_statements", "sql_selects",
           "replay_database_connections", "verified_artifacts", "verified_snapshot_bytes",
           "verified_direct_bytes", "verified_archive_bytes",
           "parquet_opens", "decoded_rows", "returned_components", "returned_revisions", "returned_evidence_rows")


def record_components(components, counters):
    for component in components:
        counters["returned_components"] += 1
        counters["returned_revisions"] += len(component.get("revisions", []))
        counters["returned_evidence_rows"] += len(component.get("evidence", []))


def count_direct_verification(counters):
    original = hashlib.file_digest

    def file_digest(file, *args, **kwargs):
        start = file.tell()
        result = original(file, *args, **kwargs)
        counters["verified_direct_bytes"] += file.tell() - start
        return result

    hashlib.file_digest = file_digest


def measure_sample(perform, counters):
    result, _ = sample(perform, counters)
    metric = result["counters"]
    metric["verified_archive_bytes"] = metric.get("verified_direct_bytes", 0) + metric.get(
        "verified_snapshot_bytes", 0
    )
    return result


def prepare(raw, storage):
    manifest = genuine(raw, storage)
    session = replay(raw, manifest)
    route = f"/api/replay/sessions/{session}"
    checkpoint = checked(raw.post(f"{route}/checkpoint", json={"position": POSITION}))
    assert checkpoint["position"] == POSITION
    pins = {}
    for cutoff in INSPECTION_CLOCKS:
        current = checked(read_confluence(raw, session, cutoff))
        receipt = current["receipt_id"]
        saved = checked(raw.post(f"{route}/confluence", params={"cutoff": cutoff, "profile": PROFILE,
                                                                 "receipt_id": receipt}))
        assert saved == current
        pins[cutoff] = {"receipt_id": receipt, "status": current["status"],
                        "crt": current["components"]["crt"]["family_id"],
                        "smt": current["components"]["smt"]["family_id"]}
    assert pins[ALIGNMENT]["status"] == "ALIGNED"
    return {"session": session, "pins": pins}


def worker(operation, spec, source, repetitions):
    from fastapi.testclient import TestClient
    from research_backend.app.main import app

    with TestClient(app) as raw:
        if operation == "prepare":
            return prepare(raw, source)
        session = spec["session"]
        pins = {int(clock): receipt for clock, receipt in spec["pins"].items()}
        counters = install_counters()
        count_direct_verification(counters)
        client = CountedClient(raw, counters)
        rss_peak = runpy.run_path(str(ROOT / "docs/implementation/evidence/TF-I08C/benchmark.py"))["rss_peak"]
        route = f"/api/replay/sessions/{session}"

        def reopen():
            captured = checked(client.get(route))
            assert captured["checkpoint"]["position"] == POSITION
            return captured

        def confluence(cutoff, pinned=False):
            marker = pins[cutoff]
            receipt = marker["receipt_id"]
            result = checked(read_confluence(client, session, cutoff, receipt if pinned else None))
            assert result["receipt_id"] == receipt and result["status"] == marker["status"]
            record_components(result["components"].values(), counters)
            return result

        if operation.startswith("cold-"):
            def cold():
                reopen()
                if operation in {"cold-current", "cold-pinned"}:
                    confluence(ALIGNMENT, pinned=operation == "cold-pinned")
            return {"sample": measure_sample(cold, counters), "peak_kib": rss_peak()}

        captured = reopen()

        def crt(cutoff, family=None):
            params = {"view": "primary", "cutoff": cutoff}
            if family is not None:
                params["range_id"] = family
            result = checked(client.get(f"{route}/crt", params=params))
            if family:
                assert result["selected"]["range_id"] == family
                record_components([result["selected"]], counters)
            else:
                counters["returned_components"] += len(result["ranges"])
            return result

        def feed(pane):
            return {**{key: pane[key] for key in ("source", "symbol", "feed_id", "price_basis", "timeframe",
                                                  "partitions")},
                    "start": START, "end": END, "calendar": SCHEDULE}

        def smt(cutoff):
            result = checked(client.post("/api/indicators/smt/strict", json={
                "primary": feed(captured["market"]), "comparison": feed(captured["views"]["2"]),
                "cutoff": cutoff, "period": 2, "alignment_bars": 0, "tolerance_bps": 0,
                "confirmation_bars": 1, "expiry_bars": 64, "availability_mode": "HISTORICAL_SIMULATION",
            }))
            record_components(result["events"], counters)
            assert result["events"]
            winner = min(result["events"], key=lambda event: (-event["candidate_at"], event["id"]))
            return winner

        def components(cutoff):
            branch = crt(cutoff)
            complete_crt = crt(cutoff, pins[cutoff]["crt"])["selected"]
            assert max(branch["ranges"], key=lambda item: item["reference_start"])["id"] == complete_crt["id"]
            return complete_crt, smt(cutoff)

        def save(cutoff):
            current = confluence(cutoff)
            pinned = checked(client.post(f"{route}/confluence", params={
                "cutoff": cutoff, "profile": PROFILE, "receipt_id": current["receipt_id"],
            }))
            assert pinned == current
            record_components(pinned["components"].values(), counters)

        exact = confluence(ALIGNMENT)
        separate_crt, separate_smt = components(ALIGNMENT)
        assert exact["components"]["crt"]["revision_id"] == separate_crt["id"]
        assert exact["components"]["smt"]["revision_id"] == separate_smt["revisions"][-1]["id"]
        assert confluence(ALIGNMENT, pinned=True) == exact
        save(ALIGNMENT)

        def series(run, clocks):
            return [measure_sample(lambda: run(cutoff), counters) for cutoff in clocks]

        repeat = [ALIGNMENT] * repetitions
        step = list(INSPECTION_CLOCKS) * repetitions
        rewind = sorted(set(INSPECTION_CLOCKS), reverse=True) * repetitions
        actions = {
            "warm_current_list_step": (confluence, step),
            "warm_current_rewind": (confluence, rewind),
            "warm_current_same_clock": (confluence, repeat),
            "warm_pinned_same_clock": (lambda cutoff: confluence(cutoff, pinned=True), repeat),
            "warm_pinned_rewind": (lambda cutoff: confluence(cutoff, pinned=True), rewind),
            "warm_save_current_selection": (save, repeat),
            "warm_crt_component_list": (crt, repeat),
            "warm_crt_component_drill": (lambda cutoff: crt(cutoff, pins[cutoff]["crt"]), repeat),
            "warm_smt_component": (smt, repeat),
            "warm_compose_component_reads": (components, repeat),
        }
        return {"samples": {label: series(run, clocks) for label, (run, clocks) in actions.items()},
                "peak_kib": rss_peak()}


def measure(args):
    scratch = ROOT / "storage"
    scratch.mkdir(exist_ok=True)
    cold_modes = ("cold-reopen", "cold-current", "cold-pinned")
    started = datetime.now(UTC).isoformat(timespec="seconds")
    with TemporaryDirectory(prefix="t10-confluence-benchmark-", dir=scratch) as temporary:
        workspace = Path(temporary)
        env = local_environment(workspace)
        plan = workspace / "plan.json"

        def run(operation):
            return json.loads(subprocess.check_output([
                sys.executable, "-m", "scripts.benchmark_t10_confluence", "--worker", operation, "--plan", str(plan),
                "--source-storage", str(args.source_storage), "--repetitions", str(args.repetitions),
            ], cwd=ROOT, env=env, text=True))

        write_json(plan, run("prepare"))
        cold = {mode: [] for mode in cold_modes}
        # Interleave fresh backends to reduce drift from hot files and host load.
        for _ in range(args.cold_samples):
            for mode in cold_modes:
                cold[mode].append(run(mode))
        warm = run("warm")
    measurements = {label: summary([item["sample"] for item in data]) for label, data in cold.items()}
    measurements.update({label: summary(items) for label, items in warm["samples"].items()})
    for measure in measurements.values():
        for key in METRICS:
            measure["counts_per_sample_min_max"].setdefault(key, [0, 0])
    return {
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "started_at": started, "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "method": "Serial in-process HTTP against a Windows TestClient. Fresh-process samples reopen the "
                  "saved checkpoint and optionally perform one current or pinned read; startup and import "
                  "are excluded. Warm samples compare step/rewind/current/explicit save/pinned reads "
                  "and separate legacy CRT detail plus SMT requests on the same pinned market. "
                  "Save samples repeat explicit save of the same ID. OS page cache is uncontrolled.",
        "counters": "HTTP bytes measure serialized request and response bodies. SQL counts are trace callbacks. "
                    "Verified snapshot and direct hash bytes count total archive bytes verified, while decoded rows "
                    "count Parquet rows decoded by the backend. All counts include OS cache "
                    "hits. The strict SMT component returns event/pivot metadata; the joined receipt includes "
                    "more source-row proof than the separate indicator responses.",
        "coverage_utc": [utc(START), utc(END)], "clocks_utc": [utc(clock) for clock in INSPECTION_CLOCKS],
        "pins": {symbol: load_provider(args.source_storage, symbol)[3] for symbol in SYMBOLS},
        "peak_working_set_kib": {
            **{label: [item["peak_kib"] for item in data] for label, data in cold.items()}, "warm": warm["peak_kib"],
        },
        "timings": measurements,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cold-samples", type=int, default=4)
    parser.add_argument("--repetitions", type=int, default=4)
    parser.add_argument("--worker", choices=("prepare", "cold-reopen", "cold-current", "cold-pinned", "warm"),
                        help=argparse.SUPPRESS)
    parser.add_argument("--plan", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.source_storage = args.source_storage.resolve()
    if args.worker:
        spec = None if args.worker == "prepare" else json.loads(args.plan.read_text(encoding="utf-8"))
        print(json.dumps(worker(args.worker, spec, args.source_storage, args.repetitions)))
    elif args.output is None or args.cold_samples < 2 or args.repetitions < 2:
        parser.error("Measurement requires --output, at least two --cold-samples and two --repetitions")
    else:
        write_json(args.output, measure(args))


if __name__ == "__main__":
    main()
