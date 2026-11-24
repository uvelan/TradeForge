"""Opt-in paired writer check requiring the reviewed commit in the local Git clone."""
import sqlite3
import subprocess
from contextlib import closing
from types import ModuleType

import pytest

from test_restore_rehearsal import _digest
from test_shadow_manifest import catalog_pair, malformed_clone, source_hashes
from tradeforge.shadow_manifest import register


@pytest.mark.parametrize("blob", [False, True], ids=["null-id", "blob-basis"])
def test_reviewed_writer(catalog_pair, tmp_path, blob):
    args, _, _, rid = catalog_pair
    malformed_clone(args, rid, "z-blob" if blob else None, blob=blob)
    before = source_hashes(args)
    path = "tradeforge/shadow_manifest.py"
    sha = "81448271a0085475272191bde383cea383951588"
    writer = ModuleType("tradeforge.reviewed_shadow")
    writer.__package__ = "tradeforge"
    exec(compile(subprocess.check_output(["git","show",f"{sha}:{path}"]), path, "exec"), writer.__dict__)
    output = tmp_path / "reviewed.sqlite"
    if blob:
        with pytest.raises(TypeError, match="bytes is not JSON serializable") as error:
            writer.register(*args, output, 1)
        print("reviewed BLOB:", error.value)
    else:
        for n in (4, 5):
            counts = writer.register(*args, output, 1)["research"]
            print("reviewed NULL:", counts)
            assert counts == dict(inspected=n, mapped=n-2, unresolved=2)
    def valid_records(file):
        with closing(sqlite3.connect(file)) as conn:
            return conn.execute("""SELECT catalog, legacy_id, legacy_record, revision, contract
                FROM mappings JOIN artifacts USING(revision) WHERE legacy_id IS NOT NULL
                ORDER BY catalog,legacy_id""").fetchall()
    previous = valid_records(output)
    assert len(previous) == 6
    if not blob:
        original_hash = _digest(output)
        with closing(sqlite3.connect(output)) as conn:
            assert conn.execute("SELECT count(*) FROM mappings WHERE legacy_id IS NULL").fetchone() == (2,)
        with pytest.raises(ValueError, match="fresh shadow"):
            register(*args, output)
        assert _digest(output) == original_hash
        output = tmp_path / "fresh.sqlite"
    assert register(*args, output)["research"] == dict(inspected=4,mapped=1,unresolved=3)
    assert valid_records(output) == previous
    assert source_hashes(args) == before
