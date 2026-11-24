"""Use the actual Phase A writer to test publication at SQLite commit boundaries."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

from research_backend.app import storage
from research_backend.app.main import app as research_app
from tradeforge.shadow_manifest import register
from test_shadow_manifest import catalog_pair, source_hashes
from test_shadow_candle_reads import FLAG, T, fixture, fixed_request, forbid_legacy, research_bar

V1 = "a9014d71e102a55e8f5b2ab38b4dfad6ea2e1430"
V2 = "1b335e644ce83ae8df04c396ea4e5fc118424eab"


def historical_register(sha, args, manifest):
    path = "tradeforge/shadow_manifest.py"
    code = subprocess.check_output(["git","show",f"{sha}:{path}"],cwd=Path(__file__).resolve().parents[1])
    writer = ModuleType("tradeforge.historical_shadow_manifest")
    writer.__package__ = "tradeforge"
    exec(compile(code,path,"exec"),writer.__dict__)
    return writer.register(*args, manifest)


def inventory(args):
    base = args[1].parent
    return {str(p.relative_to(base).as_posix()):v for p,v in source_hashes(args).items()}


# Terminate only after SQLite's real context manager has committed on the output
# connection. No production failpoints or graceful exception cleanup are used.
CRASH = """
import os, runpy, sqlite3, sys
from pathlib import Path
output = Path(sys.argv[sys.argv.index('--manifest')+1])
connect = sqlite3.connect
class Interrupted(sqlite3.Connection):
    commits = 0
    def __exit__(self,*exc):
        result = super().__exit__(*exc)
        self.commits += 1
        if self.commits == int(os.environ['CRASH_COMMIT']):
            os._exit(75)
        return result
def intercept(path,*args,**kwargs):
    if path == output:
        kwargs['factory'] = Interrupted
    return connect(path,*args,**kwargs)
sqlite3.connect = intercept
runpy.run_module('tradeforge.shadow_manifest',run_name='__main__')
"""


@pytest.mark.parametrize("writer,crash_commit", [(V1,1),(V1,2),(V2,1)])
def test_upgrade_visibility(catalog_pair,tmp_path,monkeypatch,writer,crash_commit):
    args, good, _, _ = catalog_pair
    manifest = tmp_path/"manifest.sqlite"
    monkeypatch.delenv(FLAG,raising=False)
    control = fixed_request(args,good,cutoff=1020)
    assert control.status_code == 200
    before = inventory(args)
    historical_register(writer,args,manifest)
    with closing(sqlite3.connect(manifest)) as conn:
        old_fixed = dict(conn.execute("SELECT legacy_id,revision FROM mappings WHERE catalog='fixed'"))
        original = conn.execute("SELECT revision, reason FROM mappings WHERE legacy_id='false-complete-2'").fetchone()
    assert bool(original[0]) == (writer == V1)
    flags = ["--fixed-root",str(args[0]),"--research-db",str(args[1]),"--research-market",str(args[2]),
             "--manifest",str(manifest),"--batch-size","1"]
    monkeypatch.setenv(FLAG,str(manifest))
    forbid_legacy(monkeypatch)
    if writer == V2:
        assert fixed_request(args,good).status_code == 409
    assert subprocess.run([sys.executable,"-c",CRASH,*flags],
                          env=os.environ | {"CRASH_COMMIT":str(crash_commit)}).returncode == 75
    if writer == V1:
        with closing(sqlite3.connect(manifest)) as conn:
            assert conn.execute("SELECT revision,reason FROM mappings WHERE legacy_id='false-complete-2'").fetchone() == original
            now = dict(conn.execute("SELECT legacy_id,revision FROM mappings WHERE catalog='fixed'"))
            assert sum(now[k] != old_fixed[k] for k in now) == crash_commit-1
    blocked = fixed_request(args,"false-complete-2")
    print("interrupted",writer[:7],crash_commit,blocked.status_code)
    assert blocked.status_code == 409 and "SHADOW_REGISTRATION_INCOMPLETE" in blocked.json()["detail"]
    subprocess.run([sys.executable,"-m","tradeforge.shadow_manifest",*flags],check=True)
    rejected = fixed_request(args,"false-complete-2")
    assert rejected.status_code == 409 and "FIXED_COVERAGE_MISMATCH" in rejected.json()["detail"]
    complete = fixed_request(args,good,cutoff=1020)
    assert complete.status_code == 200 and complete.json() == control.json()
    assert inventory(args) == before
    print("HASHES",writer[:7],crash_commit,json.dumps(before,sort_keys=True))


@pytest.mark.parametrize("case", ["blob","oversized"])
def test_registered_row_count_precedes_decode(fixture,monkeypatch,case):
    args, _, part, manifest = fixture
    if case == "blob":
        with closing(sqlite3.connect(args[1])) as conn, conn:
            conn.execute("UPDATE datasets SET rows_count=? WHERE id=?",(b"2",part["id"]))
        start = T
        symbol = "EURUSD"
        reason = "ROW_COUNT_MISMATCH"
    else:
        start = T
        symbol = "GBPUSD"
        part = storage.write_partition("DUKASCOPY","fixture",symbol,"candles","M5","BID",
                                       [research_bar(T+i*300) for i in range(2001)])
        reason = "Partition exceeds the bounded range reader"
    before = inventory(args)
    register(*args,manifest)
    with closing(sqlite3.connect(manifest)) as conn:
        assert conn.execute("SELECT reason FROM mappings WHERE catalog='research' AND legacy_id=?",
                            (part["id"],)).fetchone() == (reason if case == "blob" else None,)
    monkeypatch.setenv(FLAG,str(manifest))
    forbid_legacy(monkeypatch)
    def no_decode(*args):
        pytest.fail("A rejected request reached the Parquet decoder")
    monkeypatch.setattr("research_backend.app.saved_history.read_partition_tail",no_decode)
    results = []
    with TestClient(research_app,base_url="http://localhost",raise_server_exceptions=False) as api:
        for window in ({"start":start,"end":start+300}, {}):
            r = api.get("/api/market/candles",params={"symbol":symbol,"source":"DUKASCOPY_SAVED",
                                                     "timeframe":"M5",**window})
            results.append((r.status_code,r.text))
    print(case, results)
    assert all(code == 409 and reason in body for code,body in results)
    assert inventory(args) == before
    print("HASHES",case,json.dumps(before,sort_keys=True))

@pytest.mark.parametrize("takeover_at", ["fixed","research"])
def test_superseded_registration_cannot_write_or_publish(fixture,monkeypatch,takeover_at):
    args, valid, _, manifest = fixture
    monkeypatch.setenv(FLAG,str(manifest))
    def interrupt(*_):
        raise InterruptedError
    def supersede(catalog, _):
        if catalog == takeover_at:
            with pytest.raises(InterruptedError):
                register(*args,manifest,after_batch=interrupt)
    with pytest.raises(ValueError,match="superseded"):
        register(*args,manifest,after_batch=supersede)
    blocked = fixed_request(args,valid)
    assert blocked.status_code == 409 and "SHADOW_REGISTRATION_INCOMPLETE" in blocked.json()["detail"]
    register(*args,manifest)
    assert fixed_request(args,valid).status_code == 200
