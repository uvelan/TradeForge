# Tick replay and review audit hardening

## A. Baseline

- Repository is `uvelan/TradeForge`. After fetching, local `main` and `origin/main` both resolved to `0dcc2156becf58e6f4f571435bf635dc8c7a58f2`. The checkout was clean. Work proceeded on `fix/tick-replay-audit-hardening`.
- Scope covers archive identity, replay evidence, AI review intake and submission history, quote pagination, and offline CI. The existing applications, SQLite stores, Parquet archives, and market adapters were retained.
- Before edits, 77 Python tests and 4 Vitest tests passed. The production build passed. Baseline Chromium and live MT5 were not run during the opening audit. [Baseline output excerpts](evidence/baseline.txt)
- Loading the old merge function from Git and passing overlapping legacy partitions produced two executable events for one ambiguous observation. Review of the HTTP path also found that FastAPI parsed the body before checking its size, that duplicate review originals were dropped, and that each quote page decoded all pinned input rows. The bid validator derived candle buckets by flooring to an epoch grid, which can miss disagreement on a shifted candle axis.
- Reviewed the existing research roadmap, UI API contracts, CI, BI5 import/aggregation, saved run calculation and history, review import/export, Parquet read paths, and frontend callers.

## B. Implementation

| Issue | Root cause and decision | Changed files, rejected alternatives, and limitations |
|---|---|---|
| Tick identity | File-local ordinals are meaningful only within their file. New BI5 rows retain the submitted compressed file SHA-256, declared half-open UTC file window, original ordinal and tick data. Provider, feed and instrument come from the pinned partition manifest. Compare the entire overlap as ordered timestamp buckets, including bid, ask, scale, both volumes and multiplicity. Collapse matching sequences only. A deterministic partition ID chooses the displayed provenance after equivalence is established. Multi-file execution uses the ordinal within the timestamp bucket. | `storage.py`, `saved_history.py`, `main.py`, `SimulationEvidence.tsx`. Rejected dedup by price, timestamp, or local ordinal and tie-breaks based on file/import order. Equivalence assumes both parsed windows represent the same provider stream and that the selected UTC file windows are correct. A checksum identifies submitted bytes and does not authenticate the provider. |
| Legacy evidence | Single legacy partitions retain their local order. Disjoint legacy spans can be replayed. Overlapping legacy selections fail with a source-provenance message. Old multi-partition runs remain immutable and readable with aggregate metrics withheld in the API projection. Exact quote paths on those runs require a rerun. | `saved_history.py`, `main.py`. Rejected inferring provenance from matching prices or rewriting saved rows. Reimport the original BI5 payloads under the correct UTC windows, then select a clean set of matching partitions. Old overlapping partitions in the same auto-selected range can still cause a conservative rejection. |
| Replay intervals | Bid checks now use actual half-open candle intervals, including offset starts. Overlapping candles are rejected. Existing next-bar, executable-side, adverse slip, per-side fee, gap and missing-entry/exit rules remain covered. Open positions do not cross unverified candle boundaries. | `tick_replay.py`. Rejected inferring a market closure from missing quotes or falling back to OHLC fills for tick runs. Candle-extreme checks can prove contradictions; they cannot establish that every source observation was captured. |
| Review intake | The review endpoint receives and counts raw stream chunks before JSON decode or schema validation. Exactly 1,000,000 bytes are allowed. Oversize is 413. Malformed UTF-8/JSON, duplicate keys, non-finite numbers and excessive nesting are 422. Disconnects are 400. | `main.py`. Rejected trusting Content-Length and post-parse limits. The bound applies per request; aggregate disconnected clients/requests remain subject to the local HTTP server. |
| Review audit | Each accepted submission is inserted in the same SQLite transaction as canonical review association. The write lock serializes canonical duplicate detection. Separate immutable rows retain original bytes, SHA-256, acceptance and duplicate classification. Legacy originals are projected into history by idempotent migration. The UI lists and downloads submissions. | `db.py`, `domain.py`, `main.py`, `App.tsx`, `types.ts`. Rejected deduplication that discards accepted bytes. Rejected requests are stateless and their bodies are never persisted by the importer. Old attempts discarded at baseline cannot be reconstructed. Free-text findings still require human review. |
| Saved quotes | Bounded, synchronized cache for four paths of up to 10,000 quotes each. Every hit hashes every pinned file. Cold reads hash and decode the same open file and check its metadata for concurrent changes. Pages verify exact first and last execution keys before slicing. | `saved_history.py`, `storage.py`, `main.py`, `SimulationEvidence.tsx`. Rejected metadata-only freshness and pruning without whole-file integrity. Timestamp/row-group pruning remains possible for large cold paths but was not adopted based on the measured short-path use case. Long paths bypass the cache. |
| Offline CI | Retained the existing Windows workflow with Python 3.13, Node 24, pinned Python requirements and npm lockfile. Added dependency consistency and Python syntax checks. Playwright uses an isolated store and can use explicit local ports. | `.github/workflows/checks.yml`, Vite/Playwright configs, browser tests and `tradeforge/api.py`. Default origin restrictions remain. An explicit alternate port permits only loopback origins on that port. Live broker integration remains separate. |

## C. Test evidence

| ID | Scenario | Expected | Actual | Status | Test file and function |
|---|---|---|---|---|---|
| T01 | Daily/hourly overlap, equal repeat quotes and changing prices at the same millisecond | Merge equivalent streams, preserve real repeats and choose the earlier stop | Five quotes retained including boundary quote; stop precedes target | Pass | `test_audit_hardening.py::test_daily_hourly_overlap_preserves_repeated_quotes_and_execution` |
| T02 | Reverse import and selection order | Identical stream and outcome | Both orderings gave the same stream and STOP | Pass | `test_audit_hardening.py::test_daily_hourly_overlap_preserves_repeated_quotes_and_execution` |
| T03 | Changed sequence or missing observation in a proven overlap | Reject archive | Both variants rejected | Pass | `test_audit_hardening.py::test_different_order_or_missing_overlap_is_rejected` |
| T04 | Legacy provenance and adjacent partitions | Adjacent boundary supported; overlapping legacy rejected | Both asserted | Pass | `test_audit_hardening.py::test_legacy_overlap_rejected_but_disjoint_boundary_is_supported` |
| T05 | Partial overlap, two pages, new conflicting archive, warmed cache and later file corruption | Stable pages, conflict rejected, corrupt pinned file causes 409 | Both timestamps paginated exactly once; rejection and 409 asserted | Pass | `test_workspace_tools.py::test_archive_tick_run_merges_and_pins_verified_partitions` |
| T06 | Missing entry and incomplete exit | No fabricated fill | Missing entry and incomplete data surfaced as unresolved | Pass | `test_tick_replay.py::test_missing_entry_and_incomplete_exit_quote_data` |
| T07 | Executable quote gap before later profitable quote | No exit or expectancy | No trades; aggregates null | Pass | `test_tick_replay.py::test_intraday_quote_hole_blocks_later_profitable_exit` |
| T08 | Boundary/reopen and early completed fills | No fill across weekend; early exits retained | Boundary unresolved; early target/stop fixtures completed | Pass | `test_tick_replay.py::test_weekend_boundary_cannot_be_bridged_by_reopening_quote`, `test_early_exit_with_no_quotes_through_deadline` |
| T09 | Shifted candle starts | Check quote in actual half-open interval | Boundary quote passed; one millisecond earlier contradicted preceding candle | Pass | `test_audit_hardening.py::test_bid_check_uses_actual_half_open_candle_intervals` |
| T10 | Exact limit, one byte over, streamed body, malformed/deep and Unicode | 201/413/422 and exact original bytes | All boundaries and retention checked; third ASGI chunk never requested | Pass | `test_audit_hardening.py::test_review_size_is_byte_exact_and_checked_before_validation`, `test_streaming_limit_stops_receiving_before_a_third_chunk` |
| T11 | Exact and equivalent submission, different assessment and concurrent duplicates | Every original retained; canonical grouping; immutable retention | SHA and payload matched each download; delete rejected; four concurrent submissions linked to one review | Pass | `test_audit_hardening.py::test_each_accepted_review_submission_is_immutable_and_traceable`, `test_concurrent_review_duplicates_share_one_canonical_record` |

The pytest baselines are fixture-based. For example, the raw BI5 same-millisecond fixture encodes a stop observation before a target observation and asserts STOP without deriving that expected answer from the merger. [Adversarial test output](evidence/adversarial-tests.txt)

## D. Execution evidence

Windows 11 build 26200, Python 3.13.14, pytest 9.2.0, Node 24.14.0, npm 11.1.0 and Vitest 5.1.0.

From the repository root, unless noted:

```powershell
git fetch origin
git rev-parse HEAD origin/main
.\.venv\Scripts\python.exe -m pytest -q research_backend/tests tests
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q -v research_backend/tests/test_audit_hardening.py research_backend/tests/test_tick_replay.py research_backend/tests/test_workspace_tools.py -k 'not plan_revisions and not chart_capture and not saved_drawings and not replay_manifest'
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q research_backend tradeforge
.\.venv\Scripts\python.exe -m docs.audits.benchmark_quote_path
# From frontend
npm run build
npm test
$env:TRADEFORGE_E2E_API_PORT='8098'
$env:TRADEFORGE_DEV_PORT='4198'
npm run e2e
```

- 87 Python tests passed, including 24 selected adversarial/regression cases. The existing Starlette/httpx deprecation warning remains. [Backend output](evidence/backend-tests.txt)
- TypeScript compilation, Vite production build and 4 Vitest tests passed. [Frontend output](evidence/frontend-checks.txt)
- Dependency and Python syntax checks passed. No Python linter or static type checker is configured. [Static check output](evidence/static-checks.txt)
- Six isolated Chromium tests passed. An initial run on an alternate port saw four POST-driven failures due to the loopback origin allowlist. The rerun used explicit loopback configuration. [Browser output](evidence/browser-tests.txt)
- Hosted CI is configured and will run on push. Its status is external to the immutable evidence commit. Live broker integration was not run.

## E. Replay correctness evidence

Run the benchmark and targeted pytest commands in D. The benchmark loads the actual baseline merge from Git. On the same ambiguous legacy input it emitted two events, while the hardened code rejected the archive. For files with verified, matching overlap, T01 demonstrates that duplicate publication does not change multiplicity or execution. Reversing imports/selection retains STOP in the stop-before-target fixture. Altering the overlap sequence or quotes produces a hard error, while a gap during an open position returns unresolved and suppresses expectancy. The weekend fixture verifies that a profitable reopening quote cannot close the earlier position. The shift test verifies that a quote exactly at the next candle start belongs only to that candle.

## F. Performance evidence

| Path | Median latency | Peak traced Python allocations |
|---|---:|---:|
| Baseline full decode | 171.606 ms | 65.037 MiB |
| Hardened uncached | 284.820 ms | 72.423 MiB |
| Hardened cached, SHA-256 checked | 4.336 ms | 0.265 MiB |

120,000 deterministic quotes in three local Parquet files totaling 717,758 bytes. The path contains 500 quotes across a partition boundary; each operation requests 250. The harness times the storage/path data operation rather than HTTP, with five repetitions per case and a warm OS file cache. Peaks are separately measured with `tracemalloc` and exclude native Arrow allocations, OS file cache, and resident cache entries. An uncached request incurs extra validation and is slower; repeated short pages avoid decoding while still hashing the sources. Large-path, cold-disk, broker-feed and concurrent-load performance were not measured. [Benchmark script](benchmark_quote_path.py) · [Raw measurements](evidence/quote-path-benchmark.txt)

## G. Remaining risks

- Venue hours and legitimate closures require authoritative calendars. The current model treats unverified discontinuities conservatively and cannot infer individual executable size or order-book depth.
- The BI5 payload hash binds the imported bytes. It does not attest to download completeness, user-selected UTC range, broker entitlement or provider identity beyond the local import contract.
- Overlapping legacy archives require reimport or separate selection. Historical ambiguous fills remain recorded for audit and should be rerun before use.
- Full-file SHA-256 remains an I/O cost per page. Long, uncached paths decode the bounded archive on each request. Benchmarks for long paths and representative broker data remain open.
- Every accepted body occupies up to 1 MB plus database/WAL overhead. Submission history continues to grow until the user archives the local database.
- Live MT5, broker calendars, quote execution depth, multi-process cache behavior under sustained load and native peak memory remain unverified.
- Self-review checked chronological half-open boundaries, empty/missing inputs, duplicate-order policy, exact quote cursors, file integrity during reads, SQLite BEGIN IMMEDIATE and rollback, foreign keys and immutable triggers, historical API compatibility, and replay availability. Live validation requires the broker environment.
