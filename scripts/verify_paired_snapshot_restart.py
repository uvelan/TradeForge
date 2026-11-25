"""Run recorded-pair capture and reopen in separate API and browser processes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def main():
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise SystemExit("npm is required")
    with tempfile.TemporaryDirectory(prefix="tradeforge-e2e-") as storage:
        receipts = Path(storage) / "receipts.json"
        env = {**os.environ, "TRADEFORGE_PAIR_STORAGE": storage,
               "TRADEFORGE_PAIR_RECEIPTS": str(receipts), "TRADEFORGE_COMPARISON_DAY": "2026-11-13"}
        env.setdefault("TRADEFORGE_E2E_API_PORT", "8537")
        env.setdefault("TRADEFORGE_E2E_UI_PORT", "4537")
        for phase in ("0", "1"):
            subprocess.run([npm, "run", "e2e", "--", "--config", "playwright.comparison.config.ts", "--retries=0"],
                           cwd=frontend, env={**env, "TRADEFORGE_PAIR_REOPEN": phase}, check=True)
        for item in json.loads(receipts.read_text()):
            meta = item["meta"]
            print(meta["id"], item["digest"], meta["cutoff"],
                  [(lane["role"], lane["symbol"], lane["last_bar"]["close"]) for lane in meta["lanes"]])


if __name__ == "__main__":
    main()
