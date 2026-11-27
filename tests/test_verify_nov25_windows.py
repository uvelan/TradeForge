"""Exercise the verifier entry point in a fresh process with controlled worker receipts."""
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
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


@pytest.mark.parametrize("mismatch", [False, True])
def test_mt5_rejection_and_preflight_disagreement(tmp_path, clean_repo, mt5_source, mismatch):
    output = tmp_path / "acceptance.json"
    env = os.environ | {"PYTHONPATH": str(ROOT)}
    # Supply the real audit code through PYTHONPATH, or a deliberately divergent
    # product checkout whose audit reports eligibility for this empty archive.
    repo = clean_repo
    if mismatch:
        repo = tmp_path / "mutant"
        subprocess.run(["git", "clone", "--quiet", str(clean_repo), str(repo)], check=True)
        scripts = repo / "scripts"
        scripts.mkdir()
        (scripts / "__init__.py").touch()
        (scripts / "month_mt5_audit.py").write_text(
            "import json, sys\nfrom pathlib import Path\n"
            "Path(sys.argv[-1]).write_text(json.dumps({'status': 'ELIGIBLE_FOR_INDEPENDENT_REPLAY'}))\n")
        subprocess.run(["git", "add", "scripts"], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
                        "commit", "--quiet", "-m", "divergent product audit"], cwd=repo, check=True)
    proc = subprocess.run([sys.executable, str(SCRIPT), "--mt5-capture", str(mt5_source),
                           "--mt5-preflight", str(repo), "--output", str(output)],
                          env=env, capture_output=True, text=True)
    assert proc.returncode == int(mismatch), proc.stderr
    report = json.loads(output.read_text())
    assert report["mt5"]["accepted_for_replay"] is False
    assert report["passed"] is (not mismatch)
    if mismatch:
        assert report["first_divergence"] == dict(source="mt5", stage="mt5_preflight_status",
                                                 expected="REJECTED", actual="ELIGIBLE_FOR_INDEPENDENT_REPLAY")
    else:
        assert report["mt5"]["product_preflight"]["status"] == "REJECTED"
        assert report["first_divergence"] is None


if __name__ == "__main__":
    replay_cli(sys.argv.pop(1))
