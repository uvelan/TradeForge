"""Exercise archive source discovery and saved CLI receipts in a fresh process."""
from hashlib import sha256
import json
import lzma
import os
from pathlib import Path
import runpy
import struct
import subprocess
import sys
from unittest.mock import patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_nov25_windows.py"
DATE = "2025-11-13"


def run_verifier(unreadable_day):
    # Only the pinned test controls and product replay I/O are substituted.
    # File discovery, duplicate checks and the CLI itself all run as shipped.
    sys.path.insert(0, str(SCRIPT.parent))
    import nov25_source_oracle as oracle

    controls = Path(sys.argv[sys.argv.index("--controls") + 1])
    pins = {hour: sha256((controls / f"{hour}h_ticks.bi5").read_bytes()).hexdigest() for hour in (13, 14)}
    actual_run = subprocess.run
    actual_scandir = os.scandir

    def scandir(path):
        if unreadable_day != "-" and Path(path) == Path(unreadable_day):
            raise PermissionError(f"Test directory is unreadable: {path}")
        return actual_scandir(path)

    def worker(args, **kwargs):
        if "--worker" not in args:
            return actual_run(args, **kwargs)
        mode = args[args.index("--worker") + 1]
        if mode == "seed":
            Path(kwargs["env"]["TRADEFORGE_STORAGE"]).mkdir()
            return subprocess.CompletedProcess(args, 0, "{}")
        file_map = Path(args[args.index("--file-map") + 1])
        source = oracle.BI5Source(controls)
        source.files = {int(stamp): Path(path) for stamp, path in json.loads(file_map.read_text()).items()}
        wanted = oracle.expected(source)
        result = {key: wanted[key] for key in ("inventory", "coverage", "events", "quote_count")}
        result.update(verified_quote_rows=wanted["quote_count"], first_quote_divergence=None,
                      ledger_sha256="a" * 64, policy="test")
        return subprocess.CompletedProcess(args, 0, json.dumps(result))

    with (patch.object(oracle, "CONTROL", pins), patch.object(oracle, "verify_control"),
          patch.object(os, "scandir", scandir), patch.object(subprocess, "run", worker)):
        runpy.run_path(str(SCRIPT), run_name="__main__")


@pytest.fixture(scope="module")
def clean_baseline(tmp_path_factory):
    repo = tmp_path_factory.mktemp("archive-product")
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test",
                    "commit", "--quiet", "--allow-empty", "-m", "fixture"], cwd=repo, check=True)
    return repo


@pytest.mark.parametrize(("kind", "diagnostic", "counts"), [
    ("omitted", None, None),
    ("missing", "FileNotFoundError", None),
    ("file", "NotADirectoryError", None),
    ("unreadable", "PermissionError", None),
    ("empty", None, (2, 0, 2)),
    ("duplicate", None, (2, 2, 2)),
    ("augmented", None, (2, 3, 3)),
    ("conflict", "Conflicting input files", None),
])
def test_archive_report(tmp_path, clean_baseline, kind, diagnostic, counts):
    controls = tmp_path / "controls"
    controls.mkdir()
    for hour in (13, 14):
        payload = struct.pack(">IIIff", 1, 115_001 + hour, 115_000 + hour, 1.0, 1.0)
        (controls / f"{hour}h_ticks.bi5").write_bytes(lzma.compress(payload))

    archive = tmp_path / "archive"
    day = archive / DATE
    if kind == "file":
        archive.touch()
    if kind == "empty":
        archive.mkdir()
    if kind in ("unreadable", "duplicate", "augmented", "conflict"):
        day.mkdir(parents=True)
    if kind in ("duplicate", "augmented", "conflict"):
        for path in controls.iterdir():
            (day / path.name).write_bytes(path.read_bytes())
    if kind == "augmented":
        (day / "15h_ticks.bi5").write_bytes((controls / "13h_ticks.bi5").read_bytes())
    if kind == "conflict":
        (day / "13h_ticks.bi5").write_bytes((controls / "14h_ticks.bi5").read_bytes())

    output = tmp_path / "verifier.json"
    args = [sys.executable, __file__, str(day) if kind == "unreadable" else "-",
            "--controls", str(controls), "--workspace", str(tmp_path),
            "--baseline", str(clean_baseline), "--repeats", "0", "--output", str(output)]
    if kind != "omitted":
        args.extend(["--archive", str(archive)])
    proc = subprocess.run(args, capture_output=True, text=True)
    report = json.loads(output.read_text(encoding="utf-8"))
    assert proc.returncode == int(diagnostic is not None), report["first_divergence"] or proc.stderr
    assert report["passed"] is (diagnostic is None)
    control = report["dukascopy_control"]
    assert control["variants"]["baseline"]["parity"]["passed"]
    assert (control["control_files"], control["unique_inputs"]) == (2, 2)
    if diagnostic is not None:
        difference = report["first_divergence"]
        assert difference["source"] == "dukascopy_archive"
        assert difference["stage"] == "source_validation"
        assert diagnostic in difference["diagnostic"]
        return
    if kind == "omitted":
        assert "dukascopy_archive" not in report
        return

    archive_result = report["dukascopy_archive"]
    assert tuple(archive_result[key] for key in ("control_files", "archive_files", "unique_inputs")) == counts
    if kind == "augmented":
        assert archive_result["variants"]["baseline"]["parity"]["passed"]
    else:
        assert archive_result["same_sha256_inputs_as_control"]
        assert archive_result["registered"] == 2


if __name__ == "__main__":
    run_verifier(sys.argv.pop(1))
