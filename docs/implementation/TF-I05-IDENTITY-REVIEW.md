# TF-I05 identity policy verification

- PR #7, branch `feat/TF-I05-fvg-imbalance`.
- Reviewed HEAD and actual starting HEAD: `71f5458ecb790d6e656329c28aec10281d4d999f`.
- Implementation commit: `defa4dfcc35d79eb32b198f4df1d05a77c79b252`. The final review commit adds this report and evidence.
- Baseline: 187 backend tests passed in a local re-run. Reviewed commit evidence records 8 Vitest and 53 Chromium tests and a successful build.

## Identity contract

The previous algorithm included the digest of all input from the start of the request in formation and revision IDs. Dropping an unrelated leading candle relabeled unchanged events. The inspector also scoped its selection to the serialized request, so a normal refresh cleared it even when the evidence stayed valid.

Identity policy **v2** uses domain-specific SHA-256 digests of canonical serialized evidence. The numerical algorithm remains **FVG v1**.

| Concept | What determines it |
| --- | --- |
| Market and rule identity | Source kind, symbol, timeframe, step, feed, logical dataset and price basis. Configuration includes numerical, continuity and lifecycle settings. History start, history limit and page delivery are retrieval settings. |
| Formation evidence | Exact A/B/C candle representations and effective availability. If ATR is enabled, also the timestamped true-range sequence from the current contiguous seed through C and the resulting Wilder ATR. |
| Zone and formation IDs | Policy and algorithm versions, market, rule configuration, direction and formation evidence distinguish zones. The formation event has its own domain and ordinal. |
| Lifecycle evidence and ID | Prior event ID, exact observation candle and effective availability, transition state and reason, depth and observed price. Continuity invalidation also includes the preceding candle time. The stable zone ID, revision ordinal and transition evidence determine the event ID. |
| Snapshot and cursor | The independent snapshot includes cutoff, retrieval options, evidence sequence and authority, including manifests and checksum evidence. An unrelated correction can leave an event ID stable and still invalidate the old cursor. |
| Inspector | Keep selection scoped to instrument, timeframe, source, feed, basis, dataset and replay mode. Redisplay the selected ID only when it exists in the current response or displayed evidence page. |

The structural tests use exact bounds and times independently of expected hash values. Domain tags and unambiguous field encoding separate market, configuration, formation, transition, event and snapshot hashes. Checksummed archive partitions remain verified by the existing reader. File repartitioning can preserve stable logical identity for unchanged evidence. Physical lineage remains on the snapshot. Overlap annotations and data-quality warnings describe the evaluated context.

## Reproduction and correction

Two real 2,000-candle M1 requests retained the same 100–105 bullish FVG and the same observed 25% mitigation. v1 relabeled it after moving one bar. v2 kept its zone, formation and revision IDs at offsets 0, 1 and 100, while all three snapshots changed. The automated regression checks every offset between 0 and 100, including equality of the returned current projection. [Reproducible comparison](evidence/TF-I05-identity/rolling-comparison.txt).

- Stable formation event: `3a9d15069bc5c6291cd62d351acf5c4658d46af0d4b77fe14bd48a0e4ff4487b`.
- Stable 25% revision: `1fd0ca894047d6c42c6356b7b5abec56851b9a1aeca05aa720bc47237bec8c4c`. Remaining bounds are 100–103.75.
- Correcting B close from 105 to 104 changes the zone to `411f37ee545ed23a11d78071d65056e8e33223d0a30300f98ff5025b58ef2cdb` and the revision to `d6180e658442c8b68ab9857dde3b8ff73c4eb0998ec5db8aba4d2f11b03c76be`.
- An ATR seed correction changes the independently expected value from 4.75 to 4.875 and changes the evidence ID. With multiplier 1.04, the larger ATR rejects the width-5 gap. Dropping a candle in the relevant ATR seed history may likewise change identity.
- A live MT5 route fixture feeds a 600-candle terminal chart. Its poll appends one candle, drops the oldest, and retains one selected partial revision. A subsequent correction retires the selected ID. [Browser screenshot](evidence/TF-I05-identity/fvg-selection-rolling.png).

## Verification and commands

Windows, Python 3.13.14, Node 24.14.0. Browser fixtures use the local Playwright backend, with no broker connection.

| Command | Outcome |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` at repository root | 198 passed, including 31 FVG numerical/API and 11 identity cases. [Log](evidence/TF-I05-identity/pytest.txt) |
| `npm test` in frontend | 8 passed. [Log](evidence/TF-I05-identity/vitest.txt) |
| `npm run build` in frontend | TypeScript and production build succeeded. [Log](evidence/TF-I05-identity/build.txt) |
| `npm run e2e -- --reporter=line` in frontend | 54 passed. [Full Chromium log](evidence/TF-I05-identity/playwright.txt) |

The backend suite includes liquidity, market-structure, exact mitigation, delayed availability, simultaneous revisions, checksum failures and stale-cursor rejection. Chromium covers drawing edits, liquidity, structure replay, direct FVG replay and the rolling MT5 selection test.

## Performance

The original TF-I05 benchmark was run against both revisions on the same machine and fixtures, with 12 timing samples, separate tracemalloc sample, concurrent test activity and no RSS measurement. Warm comparison of the rolling evaluator was also run head-to-head within one process. Archive metrics include verification and decoding of five Parquet partitions. Response size includes the identity policy tag. The extra digest work increases CPU cost while keeping output and Python allocations bounded. There is no identity cache.

| Fixture | Calculation P50/P95 ms v1 → v2 | JSON P50/P95 ms v1 → v2 | Bytes v1 → v2 | Python peak KiB v1 → v2 |
| --- | ---: | ---: | ---: | ---: |
| 2,000 dense | 158.81/164.06 → 235.29/257.05 | 0.96/1.07 → 1.33/3.03 | 125902 → 129066 | 1541.5 → 1546.2 |
| 2,000 frequent partials | 57.59/61.76 → 74.48/78.31 | 0.83/1.04 → 0.81/0.94 | 106262 → 109090 | 500.4 → 502.0 |
| 10,000 archive, first page | 313.16/332.11 → 390.81/431.34 | 0.88/1.62 → 0.90/1.00 | 108912 → 111768 | 15417.3 → 15417.8 |
| 10,000 archive, next page | 312.23/347.52 → 388.51/435.83 | 0.88/1.02 → 0.94/1.09 | 108912 → 111768 | 15420.3 → 15420.0 |
| 2,000 rolling, evaluator only | 12.17/12.46 → 16.10/17.27 | 0.05/0.07 → 0.05/0.10 | 3727 → 3839 | 2381.4 → 2382.5 |

Set `PYTHONPATH=.` and run `.venv/Scripts/python.exe -u docs/implementation/evidence/TF-I05/benchmark.py` for [v1](evidence/TF-I05-identity/benchmark-before.txt) and [v2](evidence/TF-I05-identity/benchmark-after.txt) fixture results from the associated revisions. Run `.venv/Scripts/python.exe docs/implementation/evidence/TF-I05-identity/rolling.py` for the head-to-head rolling comparison.

## Limits and rollback

- Stable IDs require enough data to reconstruct the formation and all relevant transitions. A changed Wilder ATR seed or genuine source correction can produce different IDs. Unverified client candles retain their authority classification.
- Existing persisted or exported v1 evidence retains its original IDs. Recalculation emits v2 IDs and cursors. Refresh historical pages using a fresh snapshot after deployment.
- The correction does not add a persistent cross-snapshot correction journal or validate fills from OHLC. This report supersedes the request-prefix ID limitation in the original TF-I05 review.
- To roll back, revert the implementation commit and redeploy backend and frontend together. Refresh active evidence pages. No migrations or persistent drawing changes are involved.
