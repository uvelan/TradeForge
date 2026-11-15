# Phase 2 verification report

## A. Repository state

- Repository is `uvelan/TradeForge`. Starting HEAD and fetched remote branch both matched the reviewed baseline `565143014c1e482ad4e652b5cdceada64b2ce27a`. The worktree was clean. This report and its code are in the review commit on `fix/tick-replay-audit-hardening`.
- Scope covers replay trust projections, Strategy Lab comparisons and audit views, optional AI export classifications, and quote retrieval performance. The phase does not expand indicators or add trading.
- Main changes are in `replay_trust.py`, `main.py`, `domain.py`, `storage.py`, `saved_history.py`, Strategy Lab React components and types, and regression and benchmark fixtures. [Implementation plan](phase2-remediation-plan.md) · [Baseline checks](evidence/phase2/baseline.txt)

## B. Findings resolution

| Finding | Before | After | Evidence and residual risk |
|---|---|---|---|
| Legacy results exposed individual outcomes | The API nulled selected totals but still returned fills and R values. The browser derived distribution and performance from them. | `replay_trust.py` projects restricted records with no trade outcomes or economics. `main.py` applies this to newly calculated, saved and listed results. The Strategy Lab displays a blocking warning, a raw audit download and a rerun action that retains the recorded UTC candle window. | Positive and negative legacy fixtures show the same restriction after restart. Raw payload and checksum are unchanged. Legacy overlapping files may still need reimport or selection of a clean archive. |
| A marker was treated as enough evidence | A legacy check relied on the presence of a tick-ordering marker. | The version 1 trust contract reports separate data provenance, source integrity, execution coverage, simulation status and broker execution verification. Current engine snapshots are content-addressed and created after complete archive validation. Reads also validate pinned provider/feed/instrument/frame/basis identity, model settings, coverage and current file digests. Older candidate snapshots must reproduce from their pinned inputs. | Tests exercise a valid old single partition, a verified profitable multi-partition run, an altered outcome with a marker, and post-save Parquet corruption. Revalidating old eligible runs is CPU/I/O intensive. |
| Mixed comparisons and exports could be misleading | Saved-run comparisons used the listed metrics. AI packages had no replay classification contract. | Strategy Lab disables ineligible comparison choices and rereads both detail projections. Optional AI export association records only run ID and trust. The pre-trade package omits all replay performance and outcomes, including for verified runs. | Chromium injects a legacy result with a 99 R outcome and checks that it and its curve are absent. API tests inspect the ZIP for classification and confirm that original immutable results remain in audit storage. |
| Cold quote pages decoded full history | Correctness checks and Python conversion ran across every archived row for each cold path. | For validated archive order, `saved_history.py` keeps complete boundary milliseconds, then applies saved ordinals. `storage.py` prunes by row-group statistics and filters before converting to Python. Every file still receives whole-file SHA-256 verification. Cold and warm reads retain immutable cache copies and recheck pinned sources. | Concurrent API pagination crosses a partition boundary with exact expected quotes and no repeats. The warm corruption test returns 409. Large historical row groups may yield less pruning. |
| CI needed exact-SHA evidence | Phase 1 CI was still pending when the earlier report was written. | The existing offline Windows workflow remains active on branch pushes and pull requests. It already installs locked dependencies and runs syntax, dependency, Python, TypeScript/Vite, Vitest and Chromium checks. No step was removed. | [Baseline CI passed on 5651430](https://github.com/uvelan/TradeForge/actions/runs/43726036906). [Review branch workflow runs](https://github.com/uvelan/TradeForge/actions/workflows/checks.yml?query=branch%3Afix%2Ftick-replay-audit-hardening) provide the check for the commit containing this report. Live MT5 is outside the offline check. |

### Trust state semantics

- `VERIFIED_PROVENANCE` supports comparisons of modelled tick results with pinned and checked sources, a supported execution model and sufficient coverage under that model.
- `LEGACY_UNVERIFIED_ORDER` restricts old cross-file executions lacking the supported ordering policy.
- `SOURCE_INTEGRITY_FAILURE` restricts missing, altered, mismatched or irreproducible evidence.
- `INCOMPLETE_EXECUTION_DATA` restricts replay with sufficient identity but insufficient quote or candle coverage.
- `SYNTHETIC_RESEARCH` and `OHLC_RESEARCH` remain explicitly illustrative and are excluded from validated performance comparisons.

All categories have `broker_execution_verified: false`. The dossier distinguishes valid simulation from broker fills, realized profitability and real executable liquidity.

## C. Verification

Windows 11 build 26200, Python 3.13.14, pytest 9.2.0, Node 24.14.0 and npm 11.1.0. Exact commands, from repository root unless marked:

```powershell
git fetch origin
git rev-parse HEAD origin/fix/tick-replay-audit-hardening
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q -v research_backend/tests/test_phase2_trust.py research_backend/tests/test_tick_replay.py research_backend/tests/test_audit_hardening.py
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q research_backend tradeforge
.\.venv\Scripts\python.exe -m docs.audits.benchmark_phase2_quote_path
# From frontend
npm run build
npm test
$env:TRADEFORGE_E2E_API_PORT='8098'
$env:TRADEFORGE_DEV_PORT='4198'
npm run e2e
```

- 93 Python tests passed, including 28 selected adversarial cases. The existing Starlette/httpx deprecation warning remained. [Backend](evidence/phase2/backend.txt) · [Adversarial](evidence/phase2/adversarial.txt)
- TypeScript and Vite production build passed. Four Vitest and seven isolated Chromium tests passed. [Frontend](evidence/phase2/frontend.txt) · [Browser](evidence/phase2/browser.txt)
- Python syntax and dependency consistency checks passed. There is no configured standalone Python linter or type checker. [Static checks](evidence/phase2/static.txt)
- Live MT5 and real brokerage fills were not verified.

## D. Trading integrity verification

The tests in `research_backend/tests/test_phase2_trust.py` persist independent +99 R and −99 R legacy outcomes. After restart the normal detail/list responses withhold both. Raw audit downloads retain the original trades, and the database payload is checked byte for byte. An independently computed two-partition BUY fixture still yields 2.5 R from an entry of 101, stop of 99 and exit of 106. Its exact paginated bids are 100 then 106. A gap fixture leaves the trade unresolved. A Parquet file altered after cache warming restricts the result and fails quote access. The legacy AI ZIP includes only its ineligible classification. Phase 1 adversarial tests continue to cover same-millisecond STOP/TARGET ordering and conflicting overlaps.

## E. Performance comparison

Same 500-quote path and identical deterministic Parquet inputs for both implementations. Five wall-clock measurements per scenario with a warm OS file cache. Cold samples clear the application cache. Sequential samples request two consecutive 250-quote pages and concurrent samples request those pages simultaneously on two threads. Cold measurements cover the quote data layer after whole-run trust is established. They exclude HTTP, SQLite projection and old-run re-execution. Files range from 59,896 to 2,890,204 compressed bytes.

| Ticks | Files | Phase 1 cold median (ms) | Phase 2 cold median (ms) | Phase 1 → Phase 2 decoded rows |
|---:|---:|---:|---:|---:|
| 10,000 | 1 | 14.426 | 3.954 | 10,000 → 10,000 |
| 10,000 | 3 | 33.285 | 7.226 | 10,000 → 6,666 |
| 100,000 | 1 | 165.006 | 5.463 | 100,000 → 16,384 |
| 100,000 | 3 | 227.633 | 9.676 | 100,000 → 16,949 |
| 500,000 | 1 | 830.648 | 9.213 | 500,000 → 16,384 |
| 500,000 | 3 | 1,351.733 | 15.191 | 500,000 → 19,210 |

For 500,000 ticks in three files, cold p95 was 1,375.416 → 18.478 ms. Warm median was 9.959 → 8.190 ms, sequential 2,857.575 → 21.414 ms, and concurrent 2,760.059 → 16.957 ms. Cold traced Python allocation peak was 307.022 → 0.414 MiB with all three file digests checked in both implementations. The process working set was 329.0 → 347.48 MiB in that sample. This is a post-sample RSS observation and includes earlier operations and native pools. At 10,000 ticks in one file, cold p95 regressed from 16.046 to 47.132 ms, and warm timings are broadly comparable. Five-sample p95 is the observed maximum. [Full CSV](evidence/phase2/quote-benchmark.csv) · [Benchmark harness](benchmark_phase2_quote_path.py)

## F. Remaining limitations

- Calendar authority, completeness of provider input, real executable size, spread evolution and fills against an actual broker remain unverified.
- Historical candidates from the older engine require source verification and re-execution. Lists of older candidates can be slower. Per-page hashes remain mandatory and I/O scales with compressed file size.
- One large row group may still require native decoding of its full contents. Python conversion is limited to matching timestamps. The benchmark uses local synthetic fixtures and does not establish peak process memory under sustained load or guarantee real-feed latency.
- OHLC and synthetic estimates are visible as illustrative research. Raw audit downloads may contain restricted historical figures and are explicitly marked audit-only. AI review classification is frozen at export; later file corruption does not rewrite an exported package.
