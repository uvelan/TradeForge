# TF-I01 historical coverage review

Original review SHA and actual starting SHA were both `c51f7133ecff916b3c9463ff734bdb924a68509f`. The coverage implementation and tests are in `069d1489a20a8c5007f81f65b8241989641233ec`. The evidence commit follows it on the existing PR #3 branch.

## Before and after

The previous-week evaluator required every hour in a UTC calendar week. A complete November H1 forex week with 120 observed trading bars had no high or low because the engine treated the 48 market-closed hours as missing data. The independent baseline fixture and 90-test baseline are committed below.

Under the configured New York Sunday 17:00 open and Friday 17:00 close, the same UTC week now expects 120 H1 candles and reports `COMPLETE`. The fixture places high 14 on Sunday reopening and low 8 before Friday close. Both are available from the Monday reference boundary given complete, timely evidence. Removing one expected open candle yields `MISSING_OPEN_CANDLES`, provisional candidate evidence, and no confirmed level. A completely absent week yields `REFERENCE_ABSENT`. Neither case falls back to an older period.

[Baseline reproduction](evidence/TF-I01-coverage/baseline-forex.txt) · [Baseline pytest](evidence/TF-I01-coverage/baseline-pytest.txt) · [Confirmed forex screenshot](evidence/TF-I01-coverage/forex-confirmed.png) · [Incomplete forex screenshot](evidence/TF-I01-coverage/forex-incomplete.png)

## Coverage rules and provenance

- `indicator_coverage.py` separates version-2 previous-range calculations from version-1 schedule coverage. Explicit schedule modes are continuous, IANA Sunday-open/Friday-close, and unknown. UTC midnight and Monday UTC midnight remain the reference boundaries. The calculation clips scheduled open intervals to the reference, verifies their candle-grid alignment, validates every observed candle start against the open intervals, then compares the expected and observed counts. Existing duplicate and overlap checks make the count comparison exact. Bars during scheduled closure conflict with the schedule. An entirely closed reference day reports `MARKET_CLOSED`.
- Real FX and MT5 CFD schedules default to unknown. Demo and spot default to continuous. Configured hours are scoped to source, symbol, feed and price basis in the terminal. They are an explicit local assumption, independent of the checksum status of price evidence. The v1 schedule does not infer holidays, daily maintenance windows or broker session exceptions. Ambiguous and nonexistent local boundary times and candle frames that straddle an open/close boundary cannot confirm.
- `indicator_engine.py` returns a coverage reason code, confirmation status, expected and actual counts, candidate range, reference bounds and availability. All bars must be closed and available at the cutoff. A completed reference cannot be available before its end or before the last required candle becomes available. Chart points begin on a current-period candle whose close can observe the confirmed reference. A test evaluates just before the weekly boundary, at the boundary, just before a delayed final candle, and at its arrival. The partial current week never changes the prior range.
- `indicator_history.py`, `indicator_routes.py` and `market.py` serve the current previous-day/week windows separately from chart input. The JSON request still accepts up to 2,000 chart candles. Each lookback is bounded by 10,080 requested grid bars, 50 archive partitions, 2,000 rows per partition and 30,000 decoded archive rows. Historical range data never extends or replaces the chart candles.
- Archive retrieval uses catalog provider, feed, basis, instrument and timeframe, and rechecks each selected Parquet checksum. Frozen archive replay permits only the matching partitions in its manifest. Conflicting hashes, missing/corrupted files and duplicate or overlapping archived candles cannot supply a confirmed range. Live MT5 checks the observed broker feed and chart price basis and is unavailable for frozen replay. The demo path preserves the main chart's existing 600-bar sequence and provides a deterministic synthetic extension. The request's overall authority stays `CLIENT_SUPPLIED_UNVERIFIED`; any independently checked archive provenance applies only to the reference read.
- `indicators.tsx`, `App.tsx` and `components.tsx` configure and present coverage. The terminal displays the current reference status and provisional evidence explicitly and hides historical price-scale tags that could be mistaken for the current reference. Older confirmed chart points remain attached to their own periods. The previous-period engine does not interpolate missing prices. Numerical indicators and existing session ranges keep their own behavior.

## Verification

| Check | Observed result |
| --- | --- |
| Full backend | 105 passed |
| Historical coverage fixtures | 15 passed as part of the backend suite |
| Existing indicator reference suite | 13 passed as part of the backend suite |
| Vitest | 8 passed |
| TypeScript/Vite production build | Passed |
| Full Chromium | 41 passed, including 39 existing cases |

The coverage fixtures in `research_backend/tests/test_indicator_coverage.py` check the following independently specified outcomes.

- `test_complete_forex_week_allows_friday_close_and_sunday_reopen`, `test_missing_open_candle_and_completely_absent_week` and `test_closed_saturday_and_unexpected_candle` verify expected grid counts, observed absence and extrema.
- `test_sunday_open_and_friday_close_follow_iana_dst` has four New York and London transition cases. `test_delayed_final_reference_bar_and_exact_week_boundary` verifies chronological availability.
- `test_server_lookback_beyond_chart_budget_for_continuous_week` covers M1 and M5. `test_exact_endpoint_limit_and_empty_history` covers 2,000 versus 2,001 available input slots and no input.
- `test_unverified_schedule_cannot_confirm_even_with_caller_hashes`, `test_archive_provenance_and_corruption` and `test_reject_duplicate_and_overlap_in_saved_evidence` exercise source identity and corruption.
- Chromium checks M5 bounded retrieval and the terminal's confirmed versus missing forex statuses using the actual evaluator with an injected H1 candle fixture. Full Chromium includes the existing symbol/timeframe, replay, pane and chart drawing regressions.

[Backend and numerical tests](evidence/TF-I01-coverage/backend.txt) · [Vitest](evidence/TF-I01-coverage/vitest.txt) · [Build](evidence/TF-I01-coverage/build.txt) · [Chromium](evidence/TF-I01-coverage/playwright.txt) · [M5 historical lookback screenshot](evidence/TF-I01-coverage/continuous-week.png)

Run `..venvScriptspython.exe -m pytest -q` from the root. From `frontend`, run `npm test`, `npm run build`, and `npm run e2e -- --reporter=line`. Local Chromium used an isolated SQLite directory and ports 4198 and 8098.

## Performance and limits

The benchmark measures warm reads from temporary SQLite and Parquet fixtures with SHA-256 checks enabled, then measures pure calculation and the combined call. Each latency figure uses 25 repetitions. Python heap peak is a separate tracemalloc sample. Native Arrow memory and RSS were not measured.

| Archive scenario | Requested grid slots | Decoded rows | Partitions | Read median / P95 | Calculation median / P95 | Combined median / P95 | Combined Python peak |
| --- | ---: | ---: | ---: | --- | --- | --- | --- |
| Configured forex, M1 | 10,080 | 7,200 | 5 | 107.52 / 202.63 ms | 6.22 / 10.86 ms | 107.68 / 204.02 ms | 11,146.7 KiB |
| Continuous, M1 maximum | 10,080 | 10,080 | 7 | 116.19 / 193.27 ms | 9.42 / 21.90 ms | 118.73 / 252.38 ms | 15,533.3 KiB |

The benchmark output also records a full 7,200-candle forex reference and the result after removing an expected candle. Reproduce with PowerShell from the repo root using `Get-Content docs/implementation/evidence/TF-I01-coverage/benchmark.py -Raw | ..venvScriptspython.exe -`. [Benchmark script](evidence/TF-I01-coverage/benchmark.py) · [Measured output](evidence/TF-I01-coverage/benchmark.txt)

Reference retrieval is limited to the current immediate windows. Older periods visible on the chart require adequate evidence in the chart input. Frozen saved replay can run out of history if its capture did not include enough archive lineage. The calendar does not estimate holiday evidence or support coarse bars crossing market boundaries. A live broker was unavailable for verification. Reads are revalidated on repeat requests, which keeps integrity checks in the hot path.

To roll back, revert the coverage implementation and evidence commits on the feature branch. The change adds no persistence schema or archive migration. Existing drawings, archives and replay captures retain their formats.
