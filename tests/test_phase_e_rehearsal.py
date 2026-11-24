"""Two quiescent checkpoints and an actual HTTP reader rollback, offline only."""
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3

from fastapi.testclient import TestClient

from research_backend.app import db, storage
from research_backend.app.main import app
from scripts.t04_cutover_rehearsal import DATABASES, digest, inventory, readonly, reconcile, snapshot
from tradeforge.api import create_app
from tradeforge.catalog import Catalog
from tradeforge.database import open_database
from tradeforge.market import Timeframe
from tradeforge.shadow_manifest import register
from test_shadow_candle_reads import FLAG, T, fixed_bar, forbid_legacy, research_bar


def configure(root, patch):
    patch.setattr(db, "DATABASE", root / DATABASES[1])
    patch.setattr(storage, "MARKET_DIR", root / "research/market")


def publish(root, start):
    engine = open_database(root)
    times = [start, start + 300]
    try:
        key = Catalog(root, engine).publish(
            "simulator", "deterministic-eurusd-v1", "synthetic_bid", 100000,
            "EURUSD", Timeframe.M5, start, start + 600, [fixed_bar(t) for t in times],
        )
    finally:
        engine.dispose()
    partition = storage.write_partition("DUKASCOPY", "phase-e-fixture", "EURUSD", "candles",
                                        "M5", "BID", [research_bar(t) for t in times])
    return key, partition["id"], start


def probe(root, anchors):
    responses = {}
    # Start only the fixed catalog. The research GET routes need no background workers.
    with TestClient(create_app(root), base_url="http://localhost") as fixed:
        with readonly(root / DATABASES[0]) as conn:
            for key, start, tf in conn.execute("SELECT id,start_utc,timeframe FROM dataset_versions ORDER BY id"):
                cutoff = start + Timeframe(tf).seconds
                for t in (cutoff - 1, cutoff):
                    responses[f"fixed/{key}/{t}"] = fixed.get(f"/api/datasets/{key}", params={"cutoff": t})
    with readonly(root / DATABASES[1]) as conn, closing(TestClient(app, base_url="http://localhost")) as client:
        for key, provider, symbol, tf, first in conn.execute(
            """SELECT id,provider,symbol,timeframe,first_time FROM datasets
               WHERE kind='candles' AND (provider!='DUKASCOPY' OR price_basis='BID') ORDER BY id"""
        ):
            start = int(datetime.fromisoformat(first).timestamp())
            responses["research/" + key] = client.get("/api/market/candles", params={
                "source": provider + "_SAVED", "symbol": symbol, "timeframe": tf,
                "start": start, "end": start + Timeframe(tf).seconds,
            })
    for response in responses.values():
        assert response.status_code == 200, response.text
    payloads = {key: res.json() for key, res in responses.items()}
    for key, part, start in anchors:
        assert payloads[f"fixed/{key}/{start + 299}"]["candles"] == []
        assert payloads[f"fixed/{key}/{start + 300}"]["candles"] == [vars(fixed_bar(start))]
        assert payloads["research/" + part]["candles"] == [research_bar(start)]
    return payloads


def test_two_checkpoint_cutover(tmp_path, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    source_arg = os.environ.get("T04_REHEARSAL_SOURCE")
    source = Path(source_arg).resolve() if source_arg else tmp_path / "fixture"
    anchors = []
    if not source_arg:
        (source / "research").mkdir(parents=True)
        configure(source, monkeypatch)
        db.initialize()
        anchors.append(publish(source, T))
    work = tmp_path / "restore"
    initial = snapshot(source, work)
    configure(work, monkeypatch)
    initial_state = reconcile(work)
    previous_responses = {}
    report = {"restored": initial_state, "checkpoints": []}
    prior_hashes = initial
    for ordinal in (1, 2):
        configure(work, monkeypatch)
        if ordinal == 2:
            anchors.append(publish(work, T if source_arg else T + 600))
        checkpoint = tmp_path / f"checkpoint-{ordinal}"
        hashes = snapshot(work, checkpoint)
        for name, checksum in prior_hashes.items():
            if name not in DATABASES:
                assert hashes[name] == checksum
        prior_hashes = hashes
        configure(checkpoint, monkeypatch)
        manifest = tmp_path / f"manifest-{ordinal}.sqlite"
        registration = register(checkpoint, db.DATABASE, storage.MARKET_DIR, manifest)
        state = reconcile(checkpoint)
        assert state["dangling_receipts"] == initial_state["dangling_receipts"]
        for lane in ("fixed", "research"):
            assert state["counts"][lane] == initial_state["counts"][lane] + (ordinal - 1)
        assert state["counts"]["receipts"] == initial_state["counts"]["receipts"] + 2 * (ordinal - 1)
        for lane in ("fixed", "research"):
            assert registration[lane]["inspected"] == state["counts"][lane]
        before = probe(checkpoint, anchors)
        assert all(before[key] == payload for key, payload in previous_responses.items())
        previous_responses = before
        with monkeypatch.context() as enabled:
            enabled.setenv(FLAG, str(manifest))
            forbid_legacy(enabled)
            assert probe(checkpoint, anchors) == before
            bad = tmp_path / f"incomplete-{ordinal}.sqlite"
            shutil.copyfile(manifest, bad)
            with closing(sqlite3.connect(bad)) as conn, conn:
                conn.execute("UPDATE registration SET complete=0")
            enabled.setenv(FLAG, str(bad))
            with TestClient(create_app(checkpoint), base_url="http://localhost") as fixed:
                first_fixed = next(key.split("/")[1] for key in before if key.startswith("fixed/"))
                rejected = [fixed.get("/api/datasets/" + first_fixed)]
            with closing(TestClient(app, base_url="http://localhost")) as client:
                rejected.append(client.get("/api/market/candles", params={
                    "source": "DUKASCOPY_SAVED", "start": T, "end": T + 300}))
            for res in rejected:
                assert res.status_code == 409 and "SHADOW_REGISTRATION_INCOMPLETE" in res.json()["detail"]
        assert probe(checkpoint, anchors) == before
        assert inventory(checkpoint) == {k: v for k, v in hashes.items() if k not in DATABASES}
        for name in DATABASES:
            assert digest(checkpoint / name) == hashes[name]
        with readonly(manifest) as conn:
            reasons = conn.execute(
                "SELECT catalog,reason,count(*) FROM mappings WHERE reason IS NOT NULL GROUP BY catalog,reason"
            ).fetchall()
        report["checkpoints"].append({
            **state, "files": hashes, "registration": registration, "registration_rejections": reasons,
            "parity": {lane: sum(k.startswith(lane + "/") for k in before) for lane in ("fixed", "research")},
            "expected_http_rejections": [r.json() for r in rejected], "responses": before,
        })
    output = os.environ.get("T04_REHEARSAL_OUTPUT")
    if output:
        with Path(output).open("x", encoding="utf-8") as handle:
            json.dump(report, handle, sort_keys=True, indent=2)
