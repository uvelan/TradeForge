# TF-I08D integrity plan

Reviewed PR #13 head is `09a0040612ea852bb4bb1fa519b11cb3fe1f7c1e`.

1. Reproduce a receipt delayed by a competing SQLite writer and the publication of stale M1 constituent revisions.
2. Publish strict availability using a post-commit receipt witness and fresh transactional lease fencing. Preserve legacy receipt and manifest bytes.
3. Compare exact per-slot M1 revision vectors inside the aggregate writer transaction. Keep aggregate candle identity stable and capture each version’s source manifest and cutoff.
4. Diagnose MT5 using the local terminal and official documentation. Block production capture until the feed's raw timestamp domain is verified.
5. Run adversarial and existing offline checks, inspect broker and archive readiness, and update PR #13 with measured evidence.
