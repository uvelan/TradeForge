"""Offline API acceptance using the reviewed November 2026 chart partitions."""
import base64
from datetime import date
import os
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import zlib

from scripts.u01_comparison_archive import reference


def verify():
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


if __name__ == "__main__":
    verify()
