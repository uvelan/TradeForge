"""Read-only coherent backup and reconciliation for the T04 offline rehearsal."""
import hashlib
import json
import shutil
import sqlite3
import zipfile
from contextlib import ExitStack, closing
from pathlib import Path, PureWindowsPath

DATABASES = ("database/trading.sqlite3", "research/tradeforge.sqlite3")
ROOTS = ("market-data", "research/market", "research/ai-exports", "research/journal")


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def readonly(path):
    return closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True))


def inventory(root):
    result = {}
    for directory in ROOTS:
        base = root / directory
        if base.is_symlink() or base.resolve() != base.absolute():
            raise ValueError("Unexpected archive link")
        for path in sorted(base.rglob("*")):
            if path.is_symlink() or path.resolve() != path.absolute():
                raise ValueError("Unexpected archive link")
            if path.is_file():
                result[path.relative_to(root).as_posix()] = digest(path)
    return result


def snapshot(source, target):
    """Copy all roots and both DBs. A concurrent change invalidates the capture."""
    source, target = source.resolve(), target.resolve()
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("Snapshot requires independent roots")
    target.mkdir(parents=True, exist_ok=False)
    with ExitStack() as stack:
        readers = [stack.enter_context(readonly(source / name)) for name in DATABASES]
        versions = [conn.execute("PRAGMA data_version").fetchone() for conn in readers]
        files = inventory(source)
        for name, reader in zip(DATABASES, readers):
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path)) as writer:
                reader.backup(writer)
                if writer.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or writer.execute(
                    "PRAGMA foreign_key_check"
                ).fetchone():
                    raise ValueError(f"Invalid restored database: {name}")
        for directory in ROOTS:
            (target / directory).mkdir(parents=True, exist_ok=True)
        for name in files:
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target / name)
        if inventory(target) != files or inventory(source) != files or versions != [
            conn.execute("PRAGMA data_version").fetchone() for conn in readers
        ]:
            raise ValueError("Inconsistent capture; retry into a fresh workspace")
    return {**files, **{name: digest(target / name) for name in DATABASES}}


def reconcile(root):
    files = inventory(root)
    rows = {}
    for lane, table, database in zip(("fixed", "research"), ("dataset_versions", "datasets"), DATABASES):
        with readonly(root / database) as conn:
            conn.row_factory = sqlite3.Row
            rows[lane] = {}
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY id"):
                locator = PureWindowsPath(row["relative_path"]).as_posix()
                name = locator if lane == "fixed" else "research/market/" + locator
                if name not in files or files[name] != row["sha256"] or row["id"] in rows[lane]:
                    raise ValueError(f"Invalid legacy dataset {lane}/{row['id']}")
                rows[lane][row["id"]] = dict(row)
    counts = {lane: len(items) for lane, items in rows.items()}
    counts.update(receipts=0, import_refs=0, smt_revisions=0, unpinned_lanes=0, smt_refs=0, exports=0)
    dangling = []
    with readonly(root / DATABASES[1]) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='partition_publications_v1'").fetchone():
            for lane, key, metadata in conn.execute("SELECT * FROM partition_publications_v1 ORDER BY 1,2"):
                counts["receipts"] += 1
                if key not in rows[lane]:
                    dangling.append([lane, key])
                elif rows[lane][key] != json.loads(metadata):
                    raise ValueError(f"Receipt mismatch: {lane}/{key}")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='historical_bi5_sources'").fetchone():
            for pin, payload in conn.execute("SELECT sha256,payload FROM historical_bi5_sources"):
                if hashlib.sha256(payload).hexdigest() != pin:
                    raise ValueError(f"Retained BI5 source mismatch: {pin}")
        for manifest, in conn.execute("SELECT manifest FROM historical_imports"):
            item = json.loads(manifest)
            parts = list(item["candle_datasets"])
            if item.get("tick_dataset"):
                parts.append(item["tick_dataset"])
            for part in parts:
                if rows["research"][part["id"]]["sha256"] != part["sha256"]:
                    raise ValueError("Import evidence digest mismatch")
                counts["import_refs"] += 1
        for event_key, payload in conn.execute("SELECT event_key,payload FROM smt_event_history"):
            counts["smt_revisions"] += 1
            for lane in ("primary", "comparison"):
                ref = json.loads(payload)["provenance"][lane]
                ids = set(ref.get("dataset_ids") or [])
                dataset_id = ref.get("dataset_id")
                if not ref.get("archive_range") and (dataset_id is not None or ref.get("sha256") is not None):
                    verified = rows["research"].get(dataset_id)
                    if verified is None or verified["sha256"] != ref.get("sha256"):
                        raise ValueError(f"Invalid SMT partition pin: event={event_key} "
                                         f"lane={lane} dataset={dataset_id}")
                if dataset_id is not None:
                    ids.add(dataset_id)
                for part in ref.get("partitions") or []:
                    ids.add(part["id"])
                    if rows["research"][part["id"]]["sha256"] != part["sha256"]:
                        raise ValueError("SMT partition checksum mismatch")
                if ids - rows["research"].keys():
                    raise ValueError("Missing SMT dataset")
                counts["smt_refs"] += len(ids)
                counts["unpinned_lanes"] += int(not ids)
        # Old captures legitimately have no lane references. Always verify the original image identity.
        for key, metadata, png in conn.execute("SELECT id,metadata,png FROM chart_snapshots"):
            if hashlib.sha256(metadata.encode()+png).hexdigest() != key:
                raise ValueError(f"Chart snapshot integrity failure: {key}")
            for lane in json.loads(metadata).get("lanes", []):
                for pin in lane["partitions"]:
                    part = rows["research"].get(pin["id"])
                    if (not part or part["sha256"] != pin["sha256"] or
                            any(part[k] != lane[k] for k in ("symbol","feed_id","timeframe","price_basis"))):
                        raise ValueError(f"Chart snapshot archive mismatch: {key}/{lane['role']}/{pin['id']}")
        for key, location, checksum in conn.execute("SELECT id,location,sha256 FROM ai_packages"):
            # Export locators are absolute. Match the issued basename and checksum
            # inside the restored archive without opening the old absolute path.
            if PureWindowsPath(location).name != key + ".zip" or files.get(
                f"research/ai-exports/{key}.zip"
            ) != checksum:
                raise ValueError(f"Unverified export {key}")
            with zipfile.ZipFile(root / "research/ai-exports" / (key + ".zip")) as package:
                if json.loads(package.read("manifest.json"))["package_id"] != key:
                    raise ValueError("Export identity mismatch")
                if "data/signal.json" in package.namelist():
                    signal = json.loads(package.read("data/signal.json"))
                    found = conn.execute(
                        "SELECT payload FROM smt_event_history WHERE event_key=? AND status=? "
                        "UNION ALL SELECT payload FROM smt_source_corrections WHERE event_key=? "
                        "AND ?='SMT_SOURCE_CORRECTED'",
                        (signal["event_key"], signal["status"], signal["event_key"], signal["status"]),
                    ).fetchone()
                    original = json.loads(found[0]) if found else {}
                    original.pop("capture", None)
                    if signal != original:
                        raise ValueError("Export signal revision mismatch")
            counts["exports"] += 1
    return {"counts": counts, "dangling_receipts": dangling}
