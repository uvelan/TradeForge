"""Run the local paper journey across five isolated Chromium/API lifetimes."""
import argparse
from contextlib import closing
from hashlib import sha256
import json
import lzma
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
from time import monotonic

from scripts.benchmark_t06 import PINS
from scripts.u01_recorded_quotes import verify

ROOT=Path(__file__).resolve().parents[1]


def verify_journey(output):
    raw=(ROOT/"storage/t06-input/13h_ticks.bi5").read_bytes()
    verify(raw,PINS[13])
    ticks=list(struct.iter_unpack(">IIIff",lzma.decompress(raw)))
    expected=[]
    cash=1_000_000_000
    for ms,quantity in ((300000,1000),(360000,-400),(420000,-600)):
        ordinal,tick=next((i,t) for i,t in enumerate(ticks) if t[0]>ms)
        price=tick[1 if quantity>0 else 2]
        cash-=quantity*price
        expected.append((ordinal,price,cash))
    assert expected==[(506,116211,883789000),(558,116199,930268600),(595,116194,999985000)]
    with tempfile.TemporaryDirectory(prefix="tradeforge-e2e-paper-") as tmp:
        root=Path(tmp)
        file=root/"receipt.json"
        file.write_text("{}")
        env={**os.environ,"TRADEFORGE_PAPER_STORAGE":tmp,"TRADEFORGE_PAPER_RECEIPT":str(file),
             "TRADEFORGE_E2E_API_PORT":"8558","TRADEFORGE_E2E_UI_PORT":"4558"}
        durations={}
        checkpoints=[]
        evidence=None
        for phase in ("entry","partial","close","signal","signal-reopen"):
            if phase=="signal":
                subprocess.run([sys.executable,"-m","scripts.verify_archive_smt_watch","uninterrupted"],
                               cwd=ROOT,env={**env,"TRADEFORGE_DATA_DIR":str(root/"research")},check=True)
            started=monotonic()
            subprocess.run([shutil.which("npm.cmd") or "npm","run","e2e","--","--config",
                            "playwright.paper.config.ts","--retries=0"],cwd=ROOT/"frontend",
                           env={**env,"TRADEFORGE_PAPER_PHASE":phase},check=True)
            durations[phase]=round(monotonic()-started,2)
            current=json.loads(file.read_text())
            with closing(sqlite3.connect((root/"research/tradeforge.sqlite3").as_uri()+"?mode=ro",uri=True)) as db:
                meta,png=db.execute("SELECT metadata,png FROM chart_snapshots WHERE id=?",(current["snapshot"],)).fetchone()
                assert sha256(meta.encode()+png).hexdigest()==current["snapshot"]
                plan_id,plan_bytes=db.execute("SELECT id,payload FROM plan_versions WHERE trade_id=? AND version=1",
                                              (current["trade"],)).fetchone()
                frozen=dict(plan_version_id=plan_id,plan_bytes_sha256=sha256(plan_bytes.encode()).hexdigest(),
                            snapshot_id=current["snapshot"],png_sha256=sha256(png).hexdigest(),lanes=json.loads(meta)["lanes"])
                if evidence is None:
                    evidence=frozen
                assert frozen==evidence
                events=[json.loads(row[0]) for row in db.execute("SELECT payload FROM plan_paper_events ORDER BY sequence")]
                assert events==current["events"]
                checkpoints.append(dict(phase=phase,orders=len(events),fills=[e["fill_id"] for e in events],
                                        position_eur=events[-1]["event"]["position_units"],cash_units=events[-1]["event"]["cash_units"]))

        receipt=json.loads(file.read_text())
        for event,(ordinal,price,balance) in zip(receipt["events"],expected,strict=True):
            assert (event["quote"]["source_order"],event["event"]["price_units"],event["event"]["cash_units"])==(ordinal,price,balance)
        receipt.update(seconds=durations,checkpoints=checkpoints,original_evidence=evidence)
        if output:
            output.write_text(json.dumps(receipt,indent=2)+"\n",encoding="utf8")
        print("Verified three economic events, both restarts, journal, no-trade and separate SMT plan.",durations)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path)
    verify_journey(parser.parse_args().output)
