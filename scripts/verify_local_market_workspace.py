"""Offline Windows operator drill. All writes and injected failures are in temporary profiles."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter
from datetime import date
from contextlib import closing

from scripts.restore_local_workspace import restore
from scripts.u01_comparison_archive import reference, ROOT
from scripts.u01_recorded_quotes import reference as quotes


def main(output=None):
    inputs = ROOT/"storage/t06-input"
    bars = quotes(inputs)
    references = reference(date(2026,11,13))
    paths = {}
    live = ROOT/"storage/research"
    with closing(sqlite3.connect((live/"tradeforge.sqlite3").as_uri()+"?mode=ro",uri=True)) as db:
        for symbol, ref in references.items():
            row = db.execute("SELECT relative_path FROM datasets WHERE id=?",(ref["partition"]["id"],)).fetchone()
            paths[symbol] = str(live/"market"/row[0])
    with TemporaryDirectory(prefix="tradeforge-e2e-") as source, TemporaryDirectory(prefix="tradeforge-drill-") as work:
        root,work = Path(source),Path(work)
        (root/"fault-probe-enabled").touch()
        plan = work/"inputs.json"
        plan.write_text(json.dumps({"paths":paths,"references":references,"bi5":str(inputs),"quotes":bars}))
        env = {**os.environ,"TRADEFORGE_PAIR_RECEIPTS":str(work/"receipts.json"),"TRADEFORGE_COMPARISON_DAY":"2026-11-13",
               "TRADEFORGE_DRILL_INPUT":str(plan),"TRADEFORGE_E2E_API_PORT":"8551","TRADEFORGE_E2E_UI_PORT":"4551"}
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm:
            raise SystemExit("npm required")
        timings = {}
        def run(storage, phase):
            start = perf_counter()
            subprocess.run([npm,"run","e2e","--","--config","playwright.reliability.config.ts","--retries=0"],
                cwd=ROOT/"frontend",check=True,env={**env,"TRADEFORGE_PAIR_STORAGE":str(storage),"TRADEFORGE_DRILL_PHASE":phase})
            timings[phase] = round(perf_counter()-start,2)
            print(phase,timings[phase],"seconds",flush=True)
        run(root,"import")
        run(root,"pair")
        run(root,"publication")
        restored = work/"clean-profile"/"storage"
        start = perf_counter()
        report = restore(root,restored)
        timings["restore"] = round(perf_counter()-start,2)
        print("restore",timings["restore"],"seconds",report["references"],report["registration"],flush=True)
        assert report["references"]["counts"]["research"] == report["references"]["counts"]["receipts"] == 12
        assert report["registration"]["research"] == {"inspected":12,"mapped":10,"unresolved":2}
        for table in ("datasets","partition_publications_v1","historical_imports","historical_bi5_sources","chart_snapshots"):
            def records(directory):
                with closing(sqlite3.connect((directory/"research/tradeforge.sqlite3").as_uri()+"?mode=ro",uri=True)) as db:
                    return db.execute("SELECT * FROM "+table+" ORDER BY 1,2").fetchall()
            assert records(root) == records(restored), table
        run(restored,"restored")
        run(restored,"reopen")
        with closing(sqlite3.connect((restored/"research/tradeforge.sqlite3").as_uri()+"?mode=ro",uri=True)) as db:
            db.row_factory = sqlite3.Row
            catalog = [dict(r) for r in db.execute("SELECT id,sha256,symbol,feed_id,timeframe,kind,price_basis,"
                "first_time,last_time,rows_count FROM datasets ORDER BY first_time,symbol,timeframe,price_basis")]
            retained = [dict(r) for r in db.execute("SELECT sha256,length(payload) AS bytes FROM historical_bi5_sources ORDER BY sha256")]
        evidence = {"times_seconds":timings,"registration":report["registration"],"references":report["references"],
                    "catalog":catalog,"bi5_sources":retained,"database_sha256":{key:value for key,value in report["hashes"].items()
                    if key.endswith(".sqlite3")},"lessons":[{"id":item["meta"]["id"],"png_sha256":item["digest"]}
                    for item in json.loads((work/"receipts.json").read_text())]}
        if output:
            output.write_text(json.dumps(evidence,indent=2)+"\n",encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path)
    main(parser.parse_args().output)
