"""Verify exact CRT–SMT confluence receipts across real local API and Chromium restarts."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter


def run(mode, args, root, npm, completion_order=None):
    output = args.output.resolve() / mode
    if completion_order:
        output /= completion_order
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="isolated-", dir=output) as directory:
        work = Path(directory)
        storage = work / "storage"
        manifest = output / "reference.json"
        env = {
            **os.environ,
            "TRADEFORGE_STORAGE": str(storage),
            "TRADEFORGE_DATA_DIR": str(storage / "research"),
            "TRADEFORGE_PAIR_STORAGE": str(storage),
            "TRADEFORGE_T10_CONFLUENCE_OUTPUT": str(output),
            "TRADEFORGE_T10_CONFLUENCE_PROFILE": str(work / "chromium"),
            "TRADEFORGE_T10_CONFLUENCE_REFERENCE": str(manifest),
            "TRADEFORGE_T10_CONFLUENCE_CASE": mode,
            "TRADEFORGE_T10_CONFLUENCE_SAMPLES": str(args.samples),
            "TRADEFORGE_E2E_API_PORT": str(args.api_port),
            "TRADEFORGE_E2E_UI_PORT": str(args.ui_port),
            "TRADEFORGE_E2E_PYTHON": sys.executable,
        }
        seed = [sys.executable, "-m", "scripts.verify_t10_confluence", "--output", str(manifest)]
        seed += (["--seed-browser"] if mode == "controlled" else
                 ["--seed-genuine", "--source-storage", str(args.source_storage.resolve())])
        subprocess.run(seed, check=True, cwd=root, env=env)

        phases = {}
        label = completion_order or mode
        phases_to_run = (("two-tab-save", "two-tab-restart") if completion_order else
                         (args.phase or ("initial", "restart")))
        for phase in phases_to_run:
            phase_env = {**env, "TRADEFORGE_T10_CONFLUENCE_PHASE": phase}
            if completion_order:
                phase_env["TRADEFORGE_T10_CONFLUENCE_COMPLETION_ORDER"] = completion_order
                if phase == "two-tab-restart":
                    phase_env["TRADEFORGE_T10_CONFLUENCE_PROFILE"] = str(work / "fresh-chromium")
            started = perf_counter()
            completed = subprocess.run(
                [npm, "run", "e2e", "--", "--config", "playwright.t10-confluence.config.ts",
                 "--retries=0", "--grep", f"@phase-{phase}@end"],
                cwd=root / "frontend", env=phase_env, check=False,
            )
            phases[phase] = {"exit_code": completed.returncode, "elapsed_seconds": round(perf_counter() - started, 2)}
            print(f"{label} {phase}: {phases[phase]}", flush=True)
            if completed.returncode:
                break
        (output / "browser-phases.json").write_text(json.dumps(phases, indent=2) + "\n", encoding="utf-8")
        return any(phase["exit_code"] for phase in phases.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-storage", type=Path)
    parser.add_argument("--case", choices=["controlled", "genuine", "all"], default="all")
    parser.add_argument("--phase", action="append", choices=["initial", "restart", "discovery"],
                        help="Run selected phases in order. By default runs initial and restart.")
    parser.add_argument("--two-tab-completion-orders", action="store_true",
                        help="Test both completion orders with separate storage and fresh API and Chromium for reopen.")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--api-port", type=int, default=8742)
    parser.add_argument("--ui-port", type=int, default=4742)
    args = parser.parse_args()
    if args.two_tab_completion_orders and (args.case != "controlled" or args.phase):
        parser.error("--two-tab-completion-orders requires --case controlled and runs its own phases")
    if args.case != "controlled" and not args.source_storage:
        parser.error("--source-storage is required for genuine archive evidence")
    if args.samples < 1:
        parser.error("--samples must be at least one")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        parser.error("npm is required")
    root = Path(__file__).resolve().parents[1]
    if args.two_tab_completion_orders:
        for order in ("first-tab-first", "second-tab-first"):
            if run("controlled", args, root, npm, order):
                return 1
        return 0
    for mode in (("controlled", "genuine") if args.case == "all" else (args.case,)):
        if run(mode, args, root, npm):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
