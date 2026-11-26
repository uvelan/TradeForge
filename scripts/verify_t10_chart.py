"""Run the recorded chart journey twice, restarting the API and Chromium on one disposable DB."""
import argparse
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
from time import perf_counter


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Directory for receipts and screenshots")
    options = parser.parse_args()
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        parser.error("npm is required")
    with tempfile.TemporaryDirectory(prefix="tradeforge-e2e-") as storage:
        env = {**os.environ, "TRADEFORGE_PAIR_STORAGE": storage,
               "TRADEFORGE_T10_CHART": "1", "TRADEFORGE_T10_CHART_OUTPUT": str(output),
               "TRADEFORGE_COMPARISON_DAY": "2026-11-13"}
        env.setdefault("TRADEFORGE_E2E_API_PORT", "8283")
        env.setdefault("TRADEFORGE_E2E_UI_PORT", "4283")
        for reopen in ("0", "1"):
            start = perf_counter()
            subprocess.run([npm, "run", "e2e", "--", "--config", "playwright.comparison.config.ts",
                            "--retries=0"], cwd=root / "frontend", check=True,
                           env={**env, "TRADEFORGE_PAIR_REOPEN": reopen})
            print(f"{'Restart' if reopen == '1' else 'Initial journey'} passed in {perf_counter()-start:.2f}s",
                  flush=True)


if __name__ == "__main__":
    main()
