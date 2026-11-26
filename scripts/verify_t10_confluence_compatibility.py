"""Fingerprint the original standalone CRT and SMT public responses on the confluence window."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.verify_t10_structure_liquidity import START, SYMBOLS, load_provider


def get_json(response):
    response.raise_for_status()
    return response.json()


def check_revision_links(client, session_id, cutoff, crt, smt):
    receipt = get_json(client.get(f"/api/replay/sessions/{session_id}/confluence",
                                  params={"cutoff": cutoff, "profile": "CRT_SMT_M5_V1"}))
    for name, families in (("crt", crt["ranges"]), ("smt", smt["events"])):
        selected = receipt["components"][name]
        if selected.get("family_id") is None:
            assert not families, (cutoff, name, "standalone API produced an unreported family")
            continue
        key = "range_id" if name == "crt" else "id"
        family, = (item for item in families if item[key] == selected["family_id"])
        published = [family] if name == "crt" else family["revisions"]
        revision = published[-1]
        assert (selected["revision_id"], selected["available_at"]) == (revision["id"], revision["available_at"]), (
            cutoff, name, revision["id"], selected["revision_id"])
    return receipt["receipt_id"]


def capture(storage, check_join=False):
    from fastapi.testclient import TestClient
    from research_backend.app.main import app

    start, end = START + 12 * 3600, START + 16 * 3600
    results = []
    with TestClient(app) as client:
        for symbol in SYMBOLS:
            _, _, path, pin = load_provider(storage, symbol)
            result = get_json(client.post("/api/market/import/reviewed", files={"file": (path.name, path.read_bytes())}))
            assert result["source_sha256"] == pin["sha256"] and result["partitions"][0]["id"] == pin["id"]
        session = get_json(client.post("/api/replay/sessions", json={
            "source": "DUKASCOPY_SAVED", "symbol": "EURUSD", "timeframe": "M5", "start": start, "end": end,
            "views": [{"view": "comparison", "symbol": "GBPUSD", "timeframe": "M5"}],
        }))
        feeds = {}
        for side, lane in (("primary", session["market"]), ("comparison", session["views"]["comparison"])):
            feeds[side] = {k: lane[k] for k in ("source", "symbol", "timeframe", "feed_id", "price_basis", "partitions")}
            feeds[side].update(dataset_id=lane.get("dataset_id"), calendar={"kind": "forex_weekly"}, start=start, end=end)
        for cutoff in (start + 3599, start + 3600, start + 5100, end):
            crt = get_json(client.get(f"/api/replay/sessions/{session['id']}/crt", params={"cutoff": cutoff, "view": "primary"}))
            smt = get_json(client.post("/api/indicators/smt/strict", json={
                **feeds, "cutoff": cutoff, "availability_mode": "HISTORICAL_SIMULATION", "period": 2,
                "alignment_bars": 0, "confirmation_bars": 1, "expiry_bars": 64, "tolerance_bps": 0, "limit": 100,
            }))
            assert smt["status"] in ("PARTIAL", "EVALUATED"), smt
            if check_join:
                check_revision_links(client, session["id"], cutoff, crt, smt)
            # Wall-clock telemetry varies on an identical run. All lineage, state,
            # archive/resource counts, timestamps and exact evidence remain in the comparison.
            smt = {**smt, "resources": {k: v for k, v in smt["resources"].items() if k != "matching_frontier_ms"}}
            for component, body in (("CRT", crt), ("SMT", smt)):
                canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
                results.append({"component": component, "cutoff": cutoff, "bytes": len(canonical),
                                "sha256": hashlib.sha256(canonical).hexdigest()})
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-storage", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--check-join", action="store_true", help="Also resolve the joined IDs against the original APIs")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="tradeforge-confluence-compat-") as workspace:
        os.environ["TRADEFORGE_STORAGE"] = workspace
        os.environ["TRADEFORGE_DATA_DIR"] = str(Path(workspace) / "research")
        fingerprints = capture(args.source_storage.resolve(), args.check_join)
    args.output.write_text(json.dumps(fingerprints, indent=2) + "\n", encoding="utf-8")
    if args.compare:
        assert fingerprints == json.loads(args.compare.read_text(encoding="utf-8")), "Standalone component responses changed"
    print(f"{len(fingerprints)} standalone component responses verified")
