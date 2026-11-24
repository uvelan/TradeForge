"""Opt-in historical-writer comparison. Requires the reviewed commit in the clone."""
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
from types import ModuleType

from tradeforge.shadow_manifest import register
from test_shadow_manifest import catalog_pair, source_hashes


def mappings(manifest):
    with closing(sqlite3.connect(manifest)) as conn:
        return {key:(rev, reason) for key, rev, reason in conn.execute(
            "SELECT legacy_id,revision,reason FROM mappings")}


def test_retry_manifest_from_reviewed_writer(catalog_pair, tmp_path):
    args, fid, gaps, rid = catalog_pair
    root = Path(__file__).resolve().parents[1]
    path = "tradeforge/shadow_manifest.py"
    version = "a9014d71e102a55e8f5b2ab38b4dfad6ea2e1430"
    source = subprocess.check_output(["git", "show", f"{version}:{path}"], cwd=root)
    old = ModuleType("tradeforge.reviewed_shadow")
    old.__package__ = "tradeforge"
    exec(compile(source, path, "exec"), old.__dict__)
    manifest = tmp_path/"reviewed.sqlite"
    before = source_hashes(args)
    assert old.register(*args, manifest)["fixed"] == dict(inspected=8, mapped=8, unresolved=0)
    previous = mappings(manifest)
    fresh = tmp_path/"fresh.sqlite"
    assert register(*args, manifest) == register(*args, fresh)
    corrected = mappings(manifest)
    assert corrected == mappings(fresh)
    for key in (fid, "same-origin", rid):
        assert corrected[key] == previous[key]
    for index, key in enumerate(gaps):
        bad = f"false-complete-{index}"
        assert previous[bad][0] and corrected[bad] == (None, "FIXED_COVERAGE_MISMATCH")
        assert corrected[key][0] and corrected[key][0] != previous[key][0]
    with closing(sqlite3.connect(manifest)) as conn:
        retained = {row[0] for row in conn.execute("SELECT revision FROM artifacts")}
    assert all(rev in retained for rev, _ in previous.values() if rev)
    assert source_hashes(args) == before
