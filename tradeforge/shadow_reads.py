"""Opt-in, offline Phase B resolver for the two public candle projections."""
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import sqlite3
from tempfile import TemporaryDirectory, TemporaryFile

from .catalog import CatalogIntegrityError, verify_artifact
from .shadow_manifest import APP_ID, CONTRACT_VERSION, MANIFEST_VERSION, raw_record


class ShadowReadError(ValueError):
    """An enabled shadow read could not establish its mapping or integrity."""


def manifest_path():
    value = os.environ.get("TRADEFORGE_CANDLE_SHADOW_MANIFEST")
    return Path(value) if value is not None else None


class CandleResolver:
    def __init__(self, catalog, root, manifest, source):
        self.catalog = catalog
        self.root = root
        self.manifest = manifest
        self.source = source

    def rows(self, query, params):
        return [dict(row) for row in self.source.execute(query, params)]

    def resolve_mapping(self, entry):
        """Check the pinned source row and revision without opening its Parquet file."""
        row = dict(entry)
        rowid = row.pop("_snapshot_rowid", None)
        key = row["id"]
        if key is None:
            predicate, parameter = "diagnostic_rowid=?", rowid
        else:
            predicate, parameter = "legacy_id=?", key
        mapping = self.manifest.execute(f"""SELECT legacy_record,revision,reason,contract
            FROM mappings LEFT JOIN artifacts USING(revision)
            WHERE catalog=? AND {predicate}""", (self.catalog, parameter)).fetchone()
        if not mapping:
            raise ShadowReadError("SHADOW_MAPPING_MISSING")
        evidence, revision, reason, contract = mapping
        if evidence != raw_record(row):
            raise ShadowReadError("SHADOW_MAPPING_RECORD_MISMATCH")
        if reason:
            raise ShadowReadError(f"SHADOW_UNRESOLVED: {reason} ({self.catalog}, {parameter})")
        if not contract or hashlib.sha256(contract.encode()).hexdigest() != revision:
            raise ShadowReadError("SHADOW_REVISION_MISMATCH")
        claim = json.loads(contract)
        fixed = self.catalog == "fixed"
        required = dict(version=CONTRACT_VERSION, source_catalog=self.catalog, kind="candles",
                        feed=row["feed" if fixed else "feed_id"], rows=row["row_count" if fixed else "rows_count"])
        required.update({k: row[k] for k in ("sha256","provider","symbol","timeframe","price_basis")})
        if fixed:
            required.update(price_scale=row["price_scale"], schema_version=row["schema_version"],
                            request_half_open_utc_s=[row["start_utc"], row["end_utc"]])
        if not (fixed and row["provider"] == "simulator" and row["feed"] == "deterministic-eurusd-v1"
                and row["symbol"] == "EURUSD" and row["price_basis"] == "synthetic_bid"):
            required["source_claim_id"] = key
        if any(claim.get(k) != v for k,v in required.items()):
            raise ShadowReadError("SHADOW_REVISION_BINDING_MISMATCH")
        return claim

    @contextmanager
    def artifact(self, entry, *, max_rows=None):
        claim = self.resolve_mapping(entry)
        if max_rows is not None and claim["rows"] > max_rows:
            raise ShadowReadError("Partition exceeds the bounded range reader")
        relative = PureWindowsPath(entry["relative_path"])
        fixed = self.catalog == "fixed"
        parts = relative.parts
        if relative.anchor or ".." in parts or (fixed and parts[:1] != ("market-data",)):
            raise ShadowReadError("SHADOW_INVALID_PATH")
        path = self.root.joinpath(*(parts[1:] if fixed else parts)).resolve()
        if not path.is_relative_to(self.root):
            raise ShadowReadError("SHADOW_INVALID_PATH")
        with TemporaryFile() as snapshot:
            try:
                verify_artifact(path, claim["sha256"], snapshot)
            except CatalogIntegrityError as error:
                raise ShadowReadError(f"SHADOW_ARTIFACT_INTEGRITY: {error}") from error
            snapshot.seek(0)
            yield snapshot


@contextmanager
def open_resolver(path, catalog, database, archive_root):
    # Use one manifest transaction and select rows from the exact database bytes
    # that passed the hash check. Phase A pins offline databases without WAL frames.
    root = archive_root.resolve()
    def check_journals(stage):
        for suffix in ("-journal", "-wal"):
            try:
                sidecar = Path(str(database) + suffix).stat()
            except FileNotFoundError:
                # SQLite can remove a clean sidecar as its last reader closes.
                # A separate exists() check can race with that removal.
                continue
            except OSError as error:
                raise ShadowReadError(
                    f"SHADOW_SOURCE_SNAPSHOT_MISMATCH: cannot inspect SQLite {suffix[1:]} {stage}"
                ) from error
            if suffix == "-journal" or sidecar.st_size > 0:
                raise ShadowReadError(
                    f"SHADOW_SOURCE_SNAPSHOT_MISMATCH: SQLite {suffix[1:]} present {stage}"
                )

    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True)) as manifest:
            manifest.execute("BEGIN")
            version = manifest.execute("PRAGMA user_version").fetchone()[0]
            if manifest.execute("PRAGMA application_id").fetchone()[0] != APP_ID:
                raise ShadowReadError("SHADOW_MANIFEST_VERSION_MISMATCH")
            if version == 2:
                raise ShadowReadError("SHADOW_REGISTRATION_INCOMPLETE: rerun registration")
            if version != MANIFEST_VERSION:
                raise ShadowReadError("SHADOW_MANIFEST_VERSION_MISMATCH")
            if manifest.execute("SELECT complete FROM registration WHERE id=1").fetchone() != (1,):
                raise ShadowReadError("SHADOW_REGISTRATION_INCOMPLETE: rerun registration")
            pin = manifest.execute("SELECT database_sha256,archive_root FROM sources WHERE catalog=?",
                                   (catalog,)).fetchone()
            if not pin or Path(pin[1]) != root:
                raise ShadowReadError("SHADOW_SOURCE_SNAPSHOT_MISMATCH")
            check_journals("before source copy")
            with TemporaryDirectory() as private:
                copy = Path(private) / "source.sqlite"
                with copy.open("wb") as handle:
                    try:
                        verify_artifact(database, pin[0], handle)
                    except CatalogIntegrityError as error:
                        raise ShadowReadError(f"SHADOW_SOURCE_SNAPSHOT_MISMATCH: {error}") from error
                check_journals("after source copy")
                with closing(sqlite3.connect(copy.as_uri()+"?immutable=1&mode=ro",uri=True)) as source:
                    source.row_factory = sqlite3.Row
                    yield CandleResolver(catalog,root,manifest,source)
    except sqlite3.Error as error:
        raise ShadowReadError(f"SHADOW_DATABASE_ERROR: {error}") from error
