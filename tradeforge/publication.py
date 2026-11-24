"""One SQLite partition registry for fixed and research publications.

Callers hold the owner's write transaction through publish() and its dependent
metadata writes. The file is immutable and complete before any row can commit.
"""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
from tempfile import TemporaryFile

import pyarrow.parquet as pq

from .catalog import CatalogIntegrityError, verify_artifact


@contextmanager
def owner_transaction(database):
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database, timeout=30)) as db, db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        yield db


@contextmanager
def encoded_partition(table, row_group_size=None):
    # An anonymous spool lets the research adapter derive the original
    # content-based ID before staging on the destination volume.
    with TemporaryFile() as spool:
        pq.write_table(table, spool, compression="zstd", row_group_size=row_group_size)
        spool.seek(0)
        checksum = hashlib.file_digest(spool, "sha256").hexdigest()
        spool.seek(0)
        yield spool, checksum


def complete_stage(stage, spool):
    """Under the owner lock, reuse a full stage or discard a verified prefix."""
    try:
        info = stage.lstat()
    except FileNotFoundError:
        return False
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise CatalogIntegrityError("Invalid publication stage")
    with stage.open("rb") as partial:
        if not os.path.samestat(info, os.fstat(partial.fileno())):
            raise CatalogIntegrityError("Publication stage changed")
        while block := partial.read(1024 * 1024):
            if spool.read(len(block)) != block:
                raise CatalogIntegrityError("Publication stage checksum mismatch")
        if not spool.read(1):
            return True
    spool.seek(0)
    stage.unlink()
    return False


def install(spool, checksum, path):
    stage = path.with_name(f".{path.name}.{checksum}.pending")
    if not complete_stage(stage, spool):
        output = stage.open("xb")
        try:
            with output:
                shutil.copyfileobj(spool, output, length=1024*1024)
                output.flush()
                os.fsync(output.fileno())
            verify_artifact(stage, checksum)
        except BaseException:
            stage.unlink(missing_ok=True)
            raise
    os.replace(stage, path)
    verify_artifact(path, checksum)
    if os.name == "posix":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def publish(db, namespace, root, record, spool, *, registered):
    """Stage the intent and return the canonical, first-committed metadata."""
    if not db.in_transaction:
        raise RuntimeError("Partition publication requires an owner transaction")
    db.execute("""CREATE TABLE IF NOT EXISTS partition_publications_v1(
        namespace TEXT NOT NULL CHECK(namespace IN ('fixed','research')),
        legacy_id TEXT NOT NULL, metadata TEXT NOT NULL CHECK(json_valid(metadata)),
        PRIMARY KEY(namespace,legacy_id)) WITHOUT ROWID""")
    for operation in ("UPDATE", "DELETE"):
        db.execute(f"""CREATE TRIGGER IF NOT EXISTS publication_v1_no_{operation}
            BEFORE {operation} ON partition_publications_v1 BEGIN
            SELECT RAISE(ABORT,'Immutable partition publication'); END""")
    key = (namespace, record["id"])
    existing = db.execute("SELECT metadata FROM partition_publications_v1 "
                          "WHERE namespace=? AND legacy_id=?", key).fetchone()
    if existing:
        saved = json.loads(existing[0])
        # The original creation time belongs to the first successful request.
        timestamp = "acquired_utc" if namespace == "fixed" else "created_at"
        if any(saved.get(k) != v for k,v in record.items() if k != timestamp):
            raise CatalogIntegrityError("Partition publication metadata mismatch")
        record = saved
    else:
        db.execute("INSERT INTO partition_publications_v1 VALUES(?,?,?)",
                   (*key, json.dumps(record,sort_keys=True,separators=(",",":"))))
    relative = Path(record["relative_path"])
    root = root.resolve()
    if namespace == "fixed":
        if relative.parts[:1] != ("market-data",):
            raise CatalogIntegrityError("Invalid catalog path")
        relative = Path(*relative.parts[1:])
    if relative.is_absolute() or ".." in relative.parts:
        raise CatalogIntegrityError("Invalid catalog path")
    path = root / relative
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise CatalogIntegrityError("Invalid catalog path")
    if existing or registered or path.exists():
        verify_artifact(path, record["sha256"])
    else:
        path.parent.mkdir(parents=True,exist_ok=True)
        install(spool, record["sha256"], path)
    return record
