"""Exercise the verifier entry point in a fresh process with controlled worker receipts."""
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_nov25_windows.py"


def replay_cli(fault):
    # Replace only the retained BI5 inputs and product-worker I/O. Run the real
    # comparison, measurement orchestration, file writer and __main__ guard.
    sys.path.insert(0, str(SCRIPT.parent))
    import nov25_source_oracle as oracle

    gaps = [] if fault in ("missing-at-zero", "agree-zero") else [[1000, 1001]]
    want = dict(inventory=[dict(start=1, status="REGISTERED")],
                coverage=[dict(start=1, status="OBSERVED", gaps=gaps)],
                events=[dict(kind="QUALIFIED", quote=dict(time_ms=1001))],
                closed_observations=[], quote_count=1, slot_quote_hashes=[])
    actual_run = subprocess.run

    def worker(args, **kwargs):
        if "--worker" not in args:
            return actual_run(args, **kwargs)
        mode = args[args.index("--worker") + 1]
        directory = Path(kwargs["env"]["TRADEFORGE_STORAGE"])
        if mode == "seed":
            directory.mkdir()
            return subprocess.CompletedProcess(args, 0, "{}")
        result = {key: deepcopy(want[key]) for key in ("inventory", "coverage", "events", "quote_count")}
        result.update(mode=mode, policy="test", ledger_sha256="a" * 64,
                      verified_quote_rows=1, first_quote_divergence=None)
        # All baseline workers emulate the legacy missing inspection field.
        if directory.name.startswith("candidate-"):
            result["service_open_gap_intervals"] = len(gaps)
            if mode == "parity":
                if fault in ("missing", "missing-at-zero"):
                    del result["service_open_gap_intervals"]
                elif fault == "wrong-zero":
                    result["service_open_gap_intervals"] = 0
                elif fault == "failed-worker":
                    return subprocess.CompletedProcess(args, 1, "", "AssertionError: decision_id refers to another event")
                elif fault == "verified-count":
                    result["verified_quote_rows"] = 0
                elif fault == "quote":
                    result["first_quote_divergence"] = dict(stage="ordered_quotes", at_ms=1001,
                                                            expected=120001, actual=120002)
                elif fault == "coverage":
                    result["coverage"][0]["gaps"] = []
                elif fault == "events":
                    result["events"][0]["kind"] = "EXIT"
            elif fault == mode:
                result["ledger_sha256"] = "b" * 64
            elif fault == "cold-coverage" and mode == "cold":
                result["coverage"].clear()
            elif fault == "warm-events" and mode == "warm":
                result["events"].clear()
            elif fault == "warm-count" and mode == "warm":
                result["quote_count"] = 0
            elif fault == "warm-gap" and mode == "warm":
                result["service_open_gap_intervals"] = 0
        return subprocess.CompletedProcess(args, 0, json.dumps(result))

    with (patch.object(oracle, "BI5Source", return_value=SimpleNamespace(files={})),
          patch.object(oracle, "expected", return_value=want),
          patch.object(oracle, "verify_control"), patch.object(subprocess, "run", worker)):
        runpy.run_path(str(SCRIPT), run_name="__main__")


@pytest.fixture(scope="module")
def clean_repo(tmp_path_factory):
    path = tmp_path_factory.mktemp("product")
    subprocess.run(["git", "init", "--quiet"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
                    "commit", "--quiet", "--allow-empty", "-m", "fixture"], cwd=path, check=True)
    return path


@pytest.mark.parametrize(("fault", "stage", "mode"), [
    ("quote", "ordered_quotes", "parity"), ("coverage", "coverage", "parity"),
    ("events", "events", "parity"), ("missing", "reopened_gap_count", "parity"),
    ("missing-at-zero", "reopened_gap_count", "parity"), ("wrong-zero", "reopened_gap_count", "parity"),
    ("cold", "timed_ledger_sha256", "cold"), ("warm", "timed_ledger_sha256", "warm"),
    ("warm-gap", "reopened_gap_count", "warm"), ("cold-coverage", "coverage", "cold"),
    ("warm-events", "events", "warm"), ("warm-count", "checkpoint_quote_count", "warm"),
    ("failed-worker", "comparison_error", "parity"), ("verified-count", "quote_count", "parity"),
    ("agree", None, None), ("agree-zero", None, None),
])
def test_replay_cli_receipt_and_exit(tmp_path, clean_repo, fault, stage, mode):
    output = tmp_path / "acceptance.json"
    proc = subprocess.run([sys.executable, __file__, fault, "--controls", str(tmp_path),
                           "--workspace", str(tmp_path), "--baseline", str(clean_repo),
                           "--candidate", str(clean_repo), "--repeats", "1", "--output", str(output)],
                          capture_output=True, text=True)
    assert proc.returncode == int(stage is not None), proc.stderr
    report = json.loads(output.read_text())
    assert report["passed"] is (stage is None)
    assert report["dukascopy_control"]["variants"]["baseline"]["parity"]["passed"]
    diff = report["first_divergence"]
    if stage is None:
        assert diff is None
        if fault == "agree-zero":
            candidate = report["dukascopy_control"]["variants"]["candidate"]
            assert candidate["parity"]["reopened_gap_intervals"] == 0
            assert [r["service_open_gap_intervals"] for r in candidate["measurements"]] == [0, 0]
        return
    assert (diff["source"], diff["variant"], diff["mode"], diff["stage"]) == (
        "dukascopy_control", "candidate", mode, stage)
    if fault == "failed-worker":
        assert "decision_id refers to another event" in diff["diagnostic"]
        return
    assert diff["expected"] != diff["actual"]
    if mode in ("cold", "warm"):
        assert diff["trial"] == 1
    if fault == "missing-at-zero":
        assert diff["expected"] == 0 and diff["actual"] is None


@pytest.fixture(scope="module")
def mt5_source(tmp_path_factory):
    path = tmp_path_factory.mktemp("mt5")
    manifest = dict(symbol="EURUSD", digits=5, source="MT5_COPY_TICKS_ALL", server_hash="1"*24, days=[])
    empty = np.empty(0, dtype=[("time", "<i8"), ("time_msc", "<i8"), ("bid", "<f8"),
                              ("ask", "<f8"), ("flags", "<u4")])
    for day in range(1, 31):
        date = datetime(2025, 11, day, tzinfo=UTC)
        stamp = int(date.timestamp())
        source = path / f"{date.date()}.npy"
        np.save(source, empty)
        manifest["days"].append(dict(day=str(date.date()), start=stamp, end=stamp + 86400,
                                     attempts=[dict(code=1)], rows=0, sha256=sha256(source.read_bytes()).hexdigest()))
    receipt = path / "capture.json"
    receipt.write_text(json.dumps(manifest))
    return receipt


def mt5_cli(receipt, output, repo=None):
    pin = sha256(receipt.read_bytes()).hexdigest()
    args = [sys.executable, str(SCRIPT), "--mt5-capture", str(receipt),
            "--mt5-sha256", pin, "--output", str(output)]
    if repo:
        args += ["--mt5-preflight", str(repo)]
    proc = subprocess.run(args, env=os.environ | {"PYTHONPATH": str(ROOT)},
                          capture_output=True, text=True)
    assert output.is_file(), (proc.returncode, proc.stderr)
    return proc.returncode, json.loads(output.read_text())


def failed_query_receipt(tmp_path, mt5_source, day):
    directory = tmp_path / "capture"
    shutil.copytree(mt5_source.parent, directory)
    receipt = directory / "capture.json"
    manifest = json.loads(receipt.read_text())
    claim = manifest["days"][day - 1]
    del claim["sha256"], claim["rows"]
    claim["attempts"] = [dict(code=-1, response_rows=None), dict(code=-10005, response_rows=None)]
    source_file = directory / (claim["day"] + ".npy")
    if day == 1:
        # A leftover file from a different run cannot turn a failed query into a success.
        source_file.write_bytes(b"unclaimed bytes")
    else:
        source_file.unlink()
    receipt.write_text(json.dumps(manifest))
    return receipt, claim


@pytest.mark.parametrize(("day", "open_hours", "closed_hours"), [(1, 0, 24), (2, 2, 22), (3, 24, 0)])
def test_partial_mt5_queries(tmp_path, clean_repo, mt5_source, day, open_hours, closed_hours):
    receipt, claim = failed_query_receipt(tmp_path, mt5_source, day)
    code, report = mt5_cli(receipt, tmp_path / "report.json", clean_repo)
    assert code == 0 and report["passed"], report["first_divergence"]
    mt5 = report["mt5"]
    assert not mt5["accepted_for_replay"]
    assert mt5["failed_days"] == [claim]
    assert len(mt5["captured_days"]) == 29
    assert all(item["rows"] == 0 for item in mt5["captured_days"])
    counts = mt5["counts"]
    assert counts.get("OPEN_QUERY_FAILED", 0) == open_hours
    assert counts.get("CLOSED_QUERY_FAILED", 0) == closed_hours
    assert counts["MISSING_OPEN"] == 482 - open_hours
    assert counts["SCHEDULED_CLOSURE"] == 238 - closed_hours
    assert mt5["reasons"] == ["INCOMPLETE_TERMINAL_QUERIES", "OPEN_SESSION_COVERAGE_INCOMPLETE"]
    preflight = mt5["product_preflight"]
    assert preflight["parity_passed"] and preflight["status"] == "REJECTED"
    assert preflight["reasons"] == mt5["reasons"]


@pytest.mark.parametrize(("fault", "diagnostic"), [
    ("missing-file", "missing"), ("missing-pin", "missing"), ("corrupt-file", "sha-256 mismatch"),
])
def test_successful_mt5_query_requires_pinned_bytes(tmp_path, mt5_source, fault, diagnostic):
    directory = tmp_path / "capture"
    shutil.copytree(mt5_source.parent, directory)
    receipt = directory / "capture.json"
    day = "2025-11-03"
    path = directory / (day + ".npy")
    if fault == "missing-file":
        path.unlink()
    elif fault == "missing-pin":
        manifest = json.loads(receipt.read_text())
        del manifest["days"][2]["sha256"]
        receipt.write_text(json.dumps(manifest))
    else:
        path.write_bytes(b"corrupt bytes")
    code, report = mt5_cli(receipt, tmp_path / "report.json")
    assert code == 1 and not report["passed"]
    diff = report["first_divergence"]
    assert (diff["source"], diff["stage"]) == ("mt5", "source_validation")
    assert day in diff["diagnostic"] and diagnostic in diff["diagnostic"].lower()


@pytest.mark.parametrize("fault", [None, "status", "partial-hour"])
def test_mt5_rejection_and_preflight_disagreement(tmp_path, clean_repo, mt5_source, fault):
    receipt = mt5_source
    repo = clean_repo
    if fault:
        repo = tmp_path / "mutant"
        subprocess.run(["git", "clone", "--quiet", str(clean_repo), str(repo)], check=True)
        scripts = repo / "scripts"
        scripts.mkdir()
        (scripts / "__init__.py").touch()
        if fault == "partial-hour":
            receipt, claim = failed_query_receipt(tmp_path, mt5_source, 3)
            mutation = 'result["hours"][48]["status"] = "MISSING_OPEN"'
        else:
            mutation = 'result["status"] = "ELIGIBLE_FOR_INDEPENDENT_REPLAY"'
        audit = ROOT / "scripts" / "month_mt5_audit.py"
        (scripts / "month_mt5_audit.py").write_text(
            f"import runpy, sys, json\nfrom pathlib import Path\nrunpy.run_path({str(audit)!r}, run_name='__main__')\n"
            f"path = Path(sys.argv[-1])\nresult = json.loads(path.read_text())\n{mutation}\n"
            "path.write_text(json.dumps(result))\n")
        subprocess.run(["git", "add", "scripts"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
                        "commit", "--quiet", "-m", "divergent product audit"], cwd=repo, check=True)
    code, report = mt5_cli(receipt, tmp_path / "acceptance.json", repo)
    assert code == int(bool(fault)) and report["passed"] is (fault is None), report["first_divergence"]
    assert report["mt5"]["accepted_for_replay"] is False
    diff = report["first_divergence"]
    if fault == "status":
        assert diff == dict(source="mt5", stage="mt5_preflight_status",
                            expected="REJECTED", actual="ELIGIBLE_FOR_INDEPENDENT_REPLAY")
    elif fault == "partial-hour":
        assert diff["source"] == "mt5" and diff["stage"] == "mt5_preflight_hours"
        assert diff["at_ms"] == claim["start"] * 1000
        assert diff["expected"]["status"] == "OPEN_QUERY_FAILED"
        assert diff["actual"]["status"] == "MISSING_OPEN"
        assert report["mt5"]["product_preflight"]["status"] == "REJECTED"
    else:
        assert report["mt5"]["product_preflight"]["status"] == "REJECTED"
        assert diff is None


if __name__ == "__main__":
    replay_cli(sys.argv.pop(1))
