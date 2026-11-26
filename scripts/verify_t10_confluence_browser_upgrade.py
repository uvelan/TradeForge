"""Exercise saved V1 and V2 recovery evidence across two real Windows browser/API runs."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from scripts.verify_t10_confluence import with_checkpoints, write_json
from scripts.verify_t10_confluence_correction import BASELINE, controlled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--api-port", type=int, default=8774)
    parser.add_argument("--ui-port", type=int, default=4774)
    args = parser.parse_args()
    old = args.baseline.resolve()
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=old, text=True).strip() != BASELINE:
        parser.error(f"The V1 writer must be checked out at {BASELINE}")
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        parser.error("npm is required")
    with TemporaryDirectory(prefix="quiet-", dir=output) as scratch:
        work = Path(scratch)
        storage = work / "storage"
        manifest = work / "reference.json"
        write_json(manifest, with_checkpoints(controlled(("EURUSD",))))
        writer = work / "originals.json"
        env = {**os.environ, "TRADEFORGE_STORAGE": str(storage), "TRADEFORGE_DATA_DIR": str(storage/"research"),
               "TRADEFORGE_PAIR_STORAGE": str(storage), "TRADEFORGE_T10_CONFLUENCE_OUTPUT": str(output),
               "TRADEFORGE_T10_CONFLUENCE_REFERENCE": str(manifest), "TRADEFORGE_T10_CONFLUENCE_CASE": "quiet-eurusd",
               "TRADEFORGE_T10_CONFLUENCE_PROFILE": str(work/"profile"), "TRADEFORGE_T10_CONFLUENCE_SAMPLES": "1",
               "TRADEFORGE_T10_CONFLUENCE_QUIET_WRITER": str(writer), "TRADEFORGE_E2E_PYTHON": sys.executable,
               "TRADEFORGE_E2E_API_PORT": str(args.api_port), "TRADEFORGE_E2E_UI_PORT": str(args.ui_port)}
        command = [sys.executable, str(root/"scripts"/"verify_t10_confluence_correction.py"),
                   "--worker", "writer", "--case", "quiet-eurusd", "--source-storage", str(storage)]
        with writer.open("wb") as original:
            subprocess.run(command, cwd=old, env={**env, "PYTHONPATH": str(old)}, stdout=original, check=True)
        for phase in ("quiet-recovery", "quiet-reopen"):
            subprocess.run([npm, "run", "e2e", "--", "--config", "playwright.t10-confluence.config.ts",
                            "--retries=0", "--grep", f"@phase-{phase}@end"], cwd=root/"frontend",
                           env={**env, "TRADEFORGE_T10_CONFLUENCE_PHASE": phase}, check=True)


if __name__ == "__main__":
    main()
