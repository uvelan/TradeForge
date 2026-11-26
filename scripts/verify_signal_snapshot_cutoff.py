"""Offline API acceptance using the reviewed November 2026 chart partitions."""
import argparse
import base64
from hashlib import sha256
import json
import shutil
import subprocess
from datetime import date
import os
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import zlib

from scripts.u01_comparison_archive import reference


def check_legacy_reader(storage, exact_id):
    """Recreate the prior writer's receipt in the disposable database and check its original hashes."""
    from research_backend.app.db import one, transaction

    original_id = "2ed01141c01ec11c4b56eadd377867b6f806f138b4f8d77bb3f8fa3a36f3f8ef"
    valid = one("SELECT metadata,png,created_at FROM chart_snapshots WHERE id=?", (exact_id,))
    metadata = json.loads(valid["metadata"])
    metadata["cutoff"] -= 1
    raw = json.dumps(metadata, sort_keys=True, separators=(",",":")).encode()
    assert sha256(raw).hexdigest() == "dfec8afdf319d64baccf2a933fe66c8e98a83880e485bc15ea608a26d2eb7c65"
    assert sha256(raw + valid["png"]).hexdigest() == original_id
    with transaction() as db:
        db.execute("INSERT INTO chart_snapshots VALUES(?,?,?,?)",
                   (original_id, raw.decode(), valid["png"], valid["created_at"]))
    env = {**os.environ, "TRADEFORGE_PAIR_STORAGE":storage, "TRADEFORGE_LEGACY_RECEIPT":"1",
           "TRADEFORGE_PAIR_REOPEN":"1"}
    env.setdefault("TRADEFORGE_E2E_API_PORT","8538")
    env.setdefault("TRADEFORGE_E2E_UI_PORT","4538")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise SystemExit("npm is required for the browser check")
    subprocess.run([npm, "run", "e2e", "--", "--config", "playwright.comparison.config.ts", "--retries=0"],
                   cwd=Path(__file__).resolve().parents[1]/"frontend", env=env, check=True)
    reopened = one("SELECT metadata,png FROM chart_snapshots WHERE id=?", (original_id,))
    assert reopened["metadata"].encode() == raw and reopened["png"] == valid["png"]


def verify(legacy_ui=False):
    with TemporaryDirectory(prefix="tradeforge-e2e-") as storage:
        data = Path(storage) / "research"
        os.environ.update(TRADEFORGE_STORAGE=storage, TRADEFORGE_DATA_DIR=str(data))
        from fastapi.testclient import TestClient
        from research_backend.tests.recorded_browser_app import app
        from research_backend.app.db import initialize, one

        initialize()
        archives = reference(date(2026, 11, 13), data)
        def chunk(kind, content):
            return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
        png = (bytes.fromhex("89504e470d0a1a0a") + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(bytes(2))) + chunk(b"IEND", b""))
        count = lambda: one("SELECT COUNT(*) AS n FROM chart_snapshots")["n"]
        exact_id = None
        with TestClient(app, base_url="http://localhost") as client:
            api = "/research/api"
            for symbols in [("EURUSD", "GBPUSD"), ("GBPUSD", "EURUSD")]:
                lanes = []
                detection = {"timeframe":"M5"}
                for role, symbol in zip(("primary","comparison"), symbols):
                    archive = archives[symbol]
                    feed = dict(symbol=symbol, feed_id="dukascopy-web-m1-v1", price_basis="BID")
                    detection[role] = archive["bars"]
                    detection[role+"_feed"] = dict(**feed, source="DUKASCOPY_SAVED", dataset_id=archive["partition"]["id"])
                    lanes.append(dict(**feed, role=role, timeframe="M5", partitions=[archive["partition"]],
                                      bars=archive["window"][11:13]))
                response = client.post(api+"/indicators/smt", json=detection)
                assert response.status_code == 200, response.text
                event = next(e for e in response.json()["events"] if e["confirmed_at"] and e["confirmed_at"] < 1794578699)
                ref = dict(event_key=event["event_key"],status="SMT_CONFIRMED")
                revision = client.get(api+"/indicators/signals/"+ref["event_key"], params={"status":ref["status"]})
                assert revision.status_code == 200, revision.text
                transition = revision.json()["transitions"][-1]["time"]
                ready = []
                for lane in lanes:
                    bar = max((b for b in archives[lane["symbol"]]["bars"] if b["time"]+300 < transition),
                              key=lambda b:b["time"])
                    ready.append({**lane, "bars":[bar]})
                snapshot = dict(symbol=symbols[0], source="DUKASCOPY_SAVED", timeframe="M5",
                                lanes=ready, png=base64.b64encode(png).decode())
                for offset, linked, expected in [(-1, True, 409), (-1, False, 201), (0, True, 201)]:
                    payload = {**snapshot, "cutoff":transition+offset}
                    if linked:
                        payload["signal_reference"] = ref
                    before = count()
                    response = client.post(api+"/chart-snapshots", json=payload)
                    after = count()
                    print("revision", ref, "T", transition, "bar opens", [l["bars"][0]["time"] for l in ready],
                          "cutoff",payload["cutoff"],"linked",linked,response.status_code,
                          response.json().get("id", response.json().get("detail")), "rows",before,after)
                    assert (response.status_code,after-before) == (expected,int(expected==201))
                    if linked and offset == 0 and symbols[0] == "EURUSD":
                        exact_id = response.json()["id"]
                    retry = client.post(api+"/chart-snapshots", json=payload)
                    assert retry.status_code == expected and retry.json() == response.json() and count() == after
                for peer_only in (False, True):
                    shown = [{**lanes[0], "bars":lanes[0]["bars"][:1]} if peer_only else lanes[0], lanes[1]]
                    for cutoff in (1794578699,1794578700):
                        for linked in (False,True):
                            payload = dict(symbol=symbols[0],source="DUKASCOPY_SAVED",timeframe="M5",
                                           cutoff=cutoff, lanes=shown, png=base64.b64encode(png).decode())
                            if linked:
                                payload["signal_reference"] = ref
                            before = count()
                            saved = client.post(api+"/chart-snapshots", json=payload)
                            delta = count()-before
                            print(symbols[0], "peer-only" if peer_only else "both", cutoff, "linked" if linked else "unlinked",
                                  saved.status_code, saved.json().get("detail",saved.json().get("id")), "rows added",delta)
                            expected = 201 if cutoff==1794578700 else 409
                            assert (saved.status_code,delta)==(expected,int(expected==201))
        if legacy_ui:
            assert exact_id
            check_legacy_reader(storage, exact_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-legacy-ui", action="store_true")
    verify(parser.parse_args().check_legacy_ui)
