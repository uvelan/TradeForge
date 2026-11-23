# Isolated backup and restore rehearsal

Run from a development checkout with the normal Python test dependencies installed:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_restore_rehearsal.py
```

The test publishes synthetic Parquet and associated catalog rows in both TradeForge stores under pytest's temporary directory. It keeps earlier SQLite read snapshots open while committing to WAL and then uses SQLite's online backup API to snapshot each database. A SHA-256 manifest binds the database snapshots and Parquet copies.

After the original synthetic store is removed, the test restores into a third directory without copying SQLite WAL sidecars. It verifies all file hashes, runs SQLite integrity and foreign-key checks, and reads the original dataset IDs and expected records using the application readers. It then confirms that the readers reject deliberately corrupted restored Parquet. No live storage, network, account, or browser state is used.

This is a repeatable check of the isolated restore procedure, not a production backup command or a cross-store atomic snapshot. It does not cover concurrent independent publishers, abrupt termination, low disk space, large archives, or restoration of a real Windows profile. Those remain separate operational acceptance tests.
