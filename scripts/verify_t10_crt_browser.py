"""Verify H1 CRT evidence in a persistent Chromium profile across fresh local servers."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter


def run(mode, args, root, npm):
    output = args.output.resolve() / mode
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=f"tradeforge-crt-{mode}-") as jar:
        work = Path(jar)
        env = {**os.environ, "TRADEFORGE_T10_CHART_OUTPUT": str(output),
               "TRADEFORGE_T10_CRT_PROFILE": str(work / "chromium"), "TRADEFORGE_T10_CRT_CASE": mode,
               "TRADEFORGE_E2E_API_PORT": str(args.api_port), "TRADEFORGE_E2E_UI_PORT": str(args.ui_port)}
        env.setdefault("TRADEFORGE_E2E_PYTHON", sys.executable)

        phases = {}
        for phase in ("initial", "restart", "legacy", "legacy-restart"):
            if phase in ("initial", "legacy"):
                manifest = work / (phase + "-reference.json")
                storage = work / phase
                env.update(TRADEFORGE_PAIR_STORAGE=str(storage), TRADEFORGE_STORAGE=str(storage),
                           TRADEFORGE_DATA_DIR=str(storage / "research"), TRADEFORGE_T10_CRT_REFERENCE=str(manifest))
                seed = [sys.executable, "-m", "scripts.verify_t10_crt", "--output", str(manifest)]
                seed += (["--seed-browser"] if mode == "controlled" else
                         ["--seed-genuine", "--source-storage", str(args.source_storage.resolve())])
                subprocess.run(seed, check=True, cwd=root, env=env)
            started = perf_counter()
            completed = subprocess.run(
                [npm, "run", "e2e", "--", "--config", "playwright.t10-crt.config.ts", "--retries=0",
                 "--grep", f"@phase-{phase}@end"], cwd=root / "frontend", env=env, check=False,
            )
            phases[phase] = {"exit_code": completed.returncode,
                             "elapsed_seconds": round(perf_counter() - started, 2)}
            print(f"{mode} {phase}: {phases[phase]}", flush=True)
            if completed.returncode:
                break
        (output / "browser-phases.json").write_text(json.dumps(phases, indent=2) + "\n", encoding="utf-8")
        return any(phase["exit_code"] for phase in phases.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-storage", type=Path)
    parser.add_argument("--case", choices=["controlled", "genuine", "all"], default="all")
    parser.add_argument("--api-port", type=int, default=8722)
    parser.add_argument("--ui-port", type=int, default=4722)
    args = parser.parse_args()
    if args.case != "controlled" and not args.source_storage:
        parser.error("--source-storage is required for genuine archive evidence")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        parser.error("npm is required")
    root = Path(__file__).resolve().parents[1]
    for mode in (("controlled", "genuine") if args.case == "all" else (args.case,)):
        if run(mode, args, root, npm):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
