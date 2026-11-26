"""Verify gap invalidation before and after a real Chromium and API restart."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
from sys import executable
from tempfile import TemporaryDirectory
from time import perf_counter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Directory for browser receipts and screenshots")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        parser.error("npm is required")
    root = Path(__file__).resolve().parents[1]

    with TemporaryDirectory(prefix="tradeforge-e2e-") as storage:
        reference = Path(storage) / "gap-reference.json"
        env = {**os.environ, "TRADEFORGE_PAIR_STORAGE": storage, "TRADEFORGE_T10_CHART_OUTPUT": str(output),
               "TRADEFORGE_T10_STRUCTURE_REFERENCE": str(reference)}
        env.setdefault("TRADEFORGE_E2E_PYTHON", executable)
        env.setdefault("TRADEFORGE_E2E_API_PORT", "8702")
        env.setdefault("TRADEFORGE_E2E_UI_PORT", "4702")
        subprocess.run([executable, "-m", "scripts.verify_t10_zone_gaps", "--seed-only", "--storage", storage,
                        "--output", str(reference)], check=True, cwd=root, env=env)
        manifest = json.loads(reference.read_text(encoding="utf-8"))
        manifest["range"] = [manifest["start"], manifest["end"]]
        reference.write_text(json.dumps(manifest), encoding="utf-8")

        phases = {}
        for phase in ("initial", "restart"):
            started = perf_counter()
            completed = subprocess.run([npm, "run", "e2e", "--", "--config", "playwright.t10-zone-gaps.config.ts",
                                        "--retries=0", "--grep", "^" + phase],
                                       cwd=root / "frontend", env=env)
            phases[phase] = {"exit_code": completed.returncode, "elapsed_seconds": round(perf_counter() - started, 2)}
            print(f"{phase}: {phases[phase]}", flush=True)
            if completed.returncode:
                break
        (output / "browser-phases.json").write_text(json.dumps(phases, indent=2) + "\n", encoding="utf-8")
        return int(any(phase["exit_code"] for phase in phases.values()))


if __name__ == "__main__":
    raise SystemExit(main())
