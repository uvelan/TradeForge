"""Benchmark the pinned November audit on Windows, without changing either implementation."""
import argparse
from collections import defaultdict
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
from hashlib import sha256
import json
from math import ceil
import os
from pathlib import Path
import platform
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts"
sys.path[:0] = [str(SCRIPTS), str(ROOT)]
BASE = "7d26bbcbd475c9b0550e0f5ac3a8ab8acf505c59"
PIN = "5431d15fa8a24b63b5f5b1f62241caa52110049ab8c87c8d1f7f5783d84d606c"
EVIDENCE = ROOT / "docs/implementation/nov25-acceptance/independent-windows-replay.json"
ROWS = 1_121_792
WARMUPS, TRIALS = 3, 7


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ProcessMemory(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in (
            "peak_working_set", "working_set", "peak_paged", "paged", "peak_nonpaged",
            "nonpaged", "pagefile", "peak_pagefile")]


class ProcessIo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


def counters():
    process = wintypes.HANDLE(-1)  # GetCurrentProcess pseudo-handle
    memory, io = ProcessMemory(), ProcessIo()
    memory.cb = ctypes.sizeof(memory)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(memory), memory.cb):
        raise ctypes.WinError()
    if not ctypes.windll.kernel32.GetProcessIoCounters(process, ctypes.byref(io)):
        raise ctypes.WinError()
    return memory.peak_working_set, {name: getattr(io, name) for name, _ in io._fields_}


def stage_probes(stack, mode, audit_module):
    """Wrap coarse boundaries in a separate fully warmed diagnostic process."""
    import numpy
    import nov25_source_oracle

    elapsed = defaultdict(int)

    def measure(module, name, label):
        original = getattr(module, name)

        def timed(*args, **kwargs):
            start = perf_counter_ns()
            try:
                return original(*args, **kwargs)
            finally:
                elapsed[label] += perf_counter_ns() - start
        stack.enter_context(patch.object(module, name, timed))

    measure(numpy, "load", "npy")
    if mode == "product":
        measure(audit_module, "day_ticks", "days")
        measure(audit_module, "verified_bytes", "io_hash")
        measure(audit_module, "sha256", "hash")
    else:
        measure(audit_module, "read_mt5_claim", "days")
        measure(Path, "read_bytes", "read")
        measure(nov25_source_oracle, "sha256", "hash")
        measure(audit_module, "sha256", "hash")
        original = audit_module.groupby

        def groupby(values, key):
            if key is audit_module.open_hour:
                elapsed["calendar_start"] = perf_counter_ns()
            return original(values, key)
        stack.enter_context(patch.object(audit_module, "groupby", groupby))
    return elapsed


def stages(mode, probes, start, end):
    # Receipt I/O/hash occur outside daily checks. For exact exclusive stage accounting,
    # treat them as part of the overall input phase (including its small metadata cost).
    if mode == "verifier":
        read = probes["read"]
        calendar = end - probes["calendar_start"]
        aggregation = probes["calendar_start"] - start - probes["days"]
    else:
        read = probes["io_hash"] - probes["hash"]
        calendar = end - start - probes["days"]
        aggregation = 0
    # Transfer receipt read/hash time out of the outside-day residual.
    receipt_ns = probes["receipt"]
    if mode == "verifier":
        aggregation -= receipt_ns
    else:
        calendar -= receipt_ns
    data_validation = probes["days"] + receipt_ns - read - probes["hash"] - probes["npy"]
    result = dict(read_ms=read / 1e6, hash_ms=probes["hash"] / 1e6,
                  npy_load_ms=probes["npy"] / 1e6, decode_validate_ms=data_validation / 1e6,
                  calendar_gaps_report_ms=calendar / 1e6)
    if mode == "verifier":
        result["hour_grouping_and_receipt_metadata_ms"] = aggregation / 1e6
    return result


def verify_output(mode, result, baseline):
    require(result["capture_sha256"] == PIN, "capture pin mismatch")
    for key in ("counts", "closed_rows", "open_gaps_ms"):
        require(result[key] == baseline[key], f"{mode} differs from baseline: {key}")
    require(result["reasons"] == baseline["product_preflight"]["reasons"], "reason mismatch")
    if mode == "product":
        require(result["status"] == "REJECTED" and result["rows"] == ROWS, "product outcome differs")
        require(len(result["hours"]) == 720 and digest(result["hours"]) ==
                baseline["product_preflight"]["hourly_receipts_sha256"], "hourly receipt mismatch")
    else:
        for key in ("captured_days", "source_rows", "open_rows", "closed_hours_with_observations",
                    "missing_open_seconds", "accepted_for_replay", "used_raw_timestamps"):
            require(result[key] == baseline[key], f"verifier differs from baseline: {key}")
        require(not result["failed_days"], "incomplete capture")


def worker(mode, state, capture, count, baseline):
    samples = []
    for _ in range(count):
        _, io_before = counters()
        start = perf_counter_ns()
        if mode == "product":
            import month_mt5_audit as module
            audit = module.audit
        else:
            import verify_nov25_windows as module
            audit = module.mt5_calendar_report
        with ExitStack() as stack:
            if state == "stages":
                probes = stage_probes(stack, mode, module)
                # Isolate the very first manifest input from the 30 nested daily reads.
                target, name = (module, "verified_bytes") if mode == "product" else (Path, "read_bytes")
                original = getattr(target, name)

                def with_receipt(path, *args):
                    if path == capture:
                        at = perf_counter_ns()
                        value = original(path, *args)
                        probes["receipt"] += perf_counter_ns() - at
                        return value
                    return original(path, *args)
                stack.enter_context(patch.object(target, name, with_receipt))
            call_start = perf_counter_ns()
            if mode == "product":
                result = audit(capture, PIN)
            else:
                result = audit(capture, PIN, None)
            end = perf_counter_ns()
        peak, io_after = counters()
        elapsed = (end - (start if state == "cold" else call_start)) / 1e6
        sample = dict(wall_ms=elapsed, audit_ms=(end - call_start) / 1e6,
                      observations_per_s=ROWS * 1000 / elapsed, peak_working_set_bytes=peak,
                      io={key: value - io_before[key] for key, value in io_after.items()})
        if state == "stages":
            sample["stages"] = stages(mode, probes, call_start, end)
        verify_output(mode, result, baseline)
        del result
        samples.append(sample)
    return samples


def summarize(samples):
    wall = sorted(x["wall_ms"] for x in samples)
    return dict(median_ms=median(wall), p95_ms=wall[ceil(len(wall) * .95) - 1],
                observations_per_s=ROWS * 1000 / median(wall),
                max_peak_working_set_bytes=max(x["peak_working_set_bytes"] for x in samples),
                median_read_bytes=median(x["io"]["read_bytes"] for x in samples),
                median_write_bytes=median(x["io"]["write_bytes"] for x in samples))


def collect(args, baseline):
    require(os.name == "nt", "Windows process counters required")
    def git(*arguments):
        return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()

    paths = ["scripts/verify_nov25_windows.py", "scripts/nov25_source_oracle.py",
             "scripts/month_mt5_audit.py", "research_backend/app/indicator_coverage.py",
             "research_backend/app/recorded_market_entry.py", str(EVIDENCE.relative_to(ROOT))]
    git("diff", "--exit-code", BASE, "--", *paths)
    require(sha256(args.capture.read_bytes()).hexdigest() == PIN, "capture mismatch")
    daily = sorted(args.capture.parent.glob("*.npy"))
    require(len(daily) == len(baseline["captured_days"]) == 30, "expected 30 daily files")
    report = dict(source_revision=BASE, runtime=platform.platform(), python=sys.version,
                  manifest_sha256=PIN, daily_files=len(daily), daily_bytes=sum(p.stat().st_size for p in daily),
                  observations=ROWS, warmups=WARMUPS, measured_trials=TRIALS, runs={})
    call = [sys.executable, str(Path(__file__).resolve()), str(args.capture)]
    with TemporaryDirectory(prefix="tf-r01-audit-") as scratch:
        env = os.environ | {"TRADEFORGE_STORAGE": scratch, "TRADEFORGE_DATA_DIR": str(Path(scratch) / "research")}
        def run(mode, state, count):
            return json.loads(subprocess.check_output(call + ["--worker", mode, "--state", state,
                                                              "--count", str(count)], cwd=ROOT, env=env))

        # The standard preflight compares all hourly records and ordered gap endpoints.
        from verify_nov25_windows import mt5_calendar_report
        with patch.dict(os.environ, {key: env[key] for key in ("TRADEFORGE_STORAGE", "TRADEFORGE_DATA_DIR")}):
            parity = mt5_calendar_report(args.capture, PIN, ROOT)
        verify_output("verifier", parity, baseline)
        receipt = parity["product_preflight"]
        require(receipt["parity_passed"] and receipt["hourly_receipts_sha256"] ==
                baseline["product_preflight"]["hourly_receipts_sha256"], f"preflight parity failed: {receipt}")
        require(parity["source_rows"] == ROWS and parity["closed_rows"] == 12071 and
                parity["counts"]["CLOSED_WITH_OBSERVATIONS"] == 8 and len(parity["open_gaps_ms"]) == 45,
                "acceptance totals differ")
        report["parity"] = dict(receipt, hourly_records=sum(parity["counts"].values()),
                                counts=parity["counts"], closed_rows=parity["closed_rows"],
                                gap_count=len(parity["open_gaps_ms"]), ordered_gap_sha256=digest(parity["open_gaps_ms"]))
        for state in ("cold", "warm", "stages"):
            for mode in ("verifier", "product"):
                if state == "cold":
                    samples = [run(mode, state, 1)[0] for _ in range(WARMUPS + TRIALS)]
                else:
                    samples = run(mode, state, WARMUPS + TRIALS)
                measured = samples[WARMUPS:]
                report["runs"][f"{state}_{mode}"] = dict(warmups=samples[:WARMUPS],
                                                        samples=measured, summary=summarize(measured))
    import numpy
    report["numpy"] = numpy.__version__
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", choices=("product", "verifier"), help=argparse.SUPPRESS)
    parser.add_argument("--state", choices=("cold", "warm", "stages"), help=argparse.SUPPRESS)
    parser.add_argument("--count", type=int, default=1, help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.capture = args.capture.resolve()
    baseline = json.loads(EVIDENCE.read_bytes())["mt5"]
    if args.worker:
        print(json.dumps(worker(args.worker, args.state, args.capture, args.count, baseline)))
    else:
        if not args.output:
            parser.error("--output is required")
        result = collect(args, baseline)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
