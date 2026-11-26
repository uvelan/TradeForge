"""Fingerprint the existing historical indicator API on the two pinned provider archives."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def fingerprint(source_storage):
    from fastapi.testclient import TestClient
    from research_backend.app.main import app
    from scripts.benchmark_t10_structure_liquidity import (
        END, PROFILE, SESSIONS, START, checked, evaluate, panes, replay,
    )

    delivery = {"formation_limit": 250, "revision_limit": 250, "current_limit": 100}
    specifications = [
        {"id": "fair_value_gap", "fvg_delivery": delivery},
        {"id": "order_blocks", "structure": PROFILE, "ob_delivery": delivery},
    ]
    receipts = []
    with TestClient(app) as client:
        session_id = replay(client, source_storage)
        session = checked(client.get(f"{SESSIONS}/{session_id}"))
        for pane in panes(session):
            for cutoff in (START + 12 * 3600, END):
                for spec, result in zip(specifications, evaluate(client, pane, cutoff, specifications), strict=True):
                    payload = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                    receipts.append({
                        "symbol": pane["symbol"], "cutoff": cutoff, "indicator": spec["id"],
                        "sha256": hashlib.sha256(payload).hexdigest(), "serialized_bytes": len(payload),
                    })
    return receipts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix="tradeforge-zone-compat-") as workspace:
        os.environ["TRADEFORGE_STORAGE"] = workspace
        os.environ["TRADEFORGE_DATA_DIR"] = str(Path(workspace) / "research")
        results = fingerprint(args.source_storage.resolve())
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
