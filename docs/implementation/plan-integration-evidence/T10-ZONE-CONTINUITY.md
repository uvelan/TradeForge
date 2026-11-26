# Missing M5 candle evidence in saved zone replay

PR #37 started this correction at `4e03463a36f176b701ba36558bf03ffa9c8795a2`. The fixtures below use disposable Parquet files labeled `controlled-t10-zone-gaps`; they exercise the saved Dukascopy M5 BID chart path. Genuine-provider acceptance is reported separately.

## Public API before and after

`GET /api/replay/sessions/{id}/zones` returns the selected family at a UTC cutoff. The two public reproductions ran on the reviewed backend before product edits. In both wick and close modes the terminal observation was already identified, but the quiet predecessor appeared in neither the revision proof nor the displayed receipt. A reviewer could see the post-gap candle without being able to verify where open-market coverage stopped.

| Controlled family | Last available response before the transition | Terminal response | Boundary opens | Missing open interval in UTC |
|---|---|---|---|---|
| EURUSD bullish FVG, known by 13:10 | 13:29:59 ACTIVE, revision 1 | 13:30:00 INVALIDATED / MISSING_OPEN_CANDLES, revision 2 | 13:15 and 13:25 | [13:20, 13:25) |
| GBPUSD bullish OB following a qualifying BOS | 13:49:59 ACTIVE, revision 1 | 13:50:00 INVALIDATED / MISSING_OPEN_CANDLES, revision 2 | 13:35 and 13:45 | [13:40, 13:45) |

All times refer to 13 November 2026 in this table. The boundary timestamps identify the **opens** of the last observed and first resumed M5 candles. The receipt becomes available at the resumed candle's modeled or supplied arrival. For the default fixture this is five minutes after its open. The producer knows which candle it last processed, including a quiet candle that did not itself change the zone. The selected revision passes that timestamp to the receipt. Both source rows come from the same checksum-verification path used for the chart. Their roles contain the invalidating revision ID. Retry, rewind and reopening can therefore reproduce the source rows without guessing from the most recent price transition.

The admitted calendar uses the New York 17:00 Friday close and Sunday reopen. For the mixed weekend example the missing intervals are **Friday [21:55, 22:00) and Sunday [22:00, 22:05) UTC**, with no missing-open claim for the intervening closure. A clean weekend and a contiguous weekday keep both zones active. A +17-second controlled delay postpones invalidation until that arrival. A Saturday observation leaves its original `UNVERIFIABLE_CLOSURE` reason, exposes both observed sources and returns `null` for missing-open intervals. No classification or historical identity was rewritten.

## Operator steps

Open **Market Terminal**, resume the saved M5 replay and expand **Replay FVG and order blocks**. Choose the chart pane, mitigation rule and zone. Advance through the first available post-gap candle. The selected zone shows its missing-open interval, both observed opens, their exact source IDs and partition hashes. Rewind to the preceding cutoff to inspect the active revision. Advance again, save the replay checkpoint, restart both processes and reopen that session from the same browser profile.

[Restarted FVG screenshot](../evidence/T10-ZONES-GAP/reopened-FVG.png) · [Restarted OB screenshot](../evidence/T10-ZONES-GAP/reopened-OB.png) · [Four reopened revision IDs and paired source pins](../evidence/T10-ZONES-GAP/reopened.json)

## Same-host measurements

A fresh process at each revision read the same four terminal selections 30 times each after a priming request. Its peak working set includes fixture creation, the API, SQLite and Parquet; OS file caches were uncontrolled. Both halves produced the same session, family and terminal revision IDs. The genuine benchmark separately ran the existing paired 264-bar workload on the same Windows host, with eight separate backend processes per cold action and 30 warm samples per action. All archived bytes and decoded-row counters matched between revisions. A zero here means the benchmark observed no reads through the verified zone-inspection path for that action.

| Action | n | Median ms | p95 ms | Verified bytes / request or action | Rows decoded |
|---|---:|---:|---:|---:|---:|
| Controlled gap EURUSD_wick | 30 | 14.03 → 15.94 (+13.7%) | 18.58 → 20.85 (+12.2%) | 2188 | 10 |
| Controlled gap EURUSD_close | 30 | 13.55 → 14.66 (+8.2%) | 14.63 → 18.20 (+24.4%) | 2188 | 10 |
| Controlled gap GBPUSD_wick | 30 | 14.80 → 15.33 (+3.6%) | 16.27 → 31.81 (+95.5%) | 2182 | 8 |
| Controlled gap GBPUSD_close | 30 | 15.05 → 16.37 (+8.8%) | 18.18 → 20.58 (+13.2%) | 2182 | 8 |
| Genuine cold_reopen_and_overlays | 8 | 100.38 → 92.33 (-8.0%) | 119.59 → 110.07 (-8.0%) | 0 | 0 |
| Genuine cold_reopen_only | 8 | 58.58 → 56.43 (-3.7%) | 64.24 → 59.36 (-7.6%) | 0 | 0 |
| Genuine collapsed_step | 30 | 33.12 → 32.21 (-2.7%) | 39.12 → 34.84 (-11.0%) | 0 | 0 |
| Genuine collapsed_rewind | 30 | 33.06 → 31.93 (-3.4%) | 37.44 → 37.87 (+1.1%) | 0 | 0 |
| Genuine new_list | 30 | 61.57 → 61.58 (+0.0%) | 67.47 → 66.27 (-1.8%) | 10538 | 264 |
| Genuine new_selection | 30 | 82.15 → 67.21 (-18.2%) | 131.04 → 71.97 (-45.1%) | 10538 | 264 |
| Genuine new_list_and_selection | 30 | 138.17 → 128.28 (-7.2%) | 194.11 → 135.15 (-30.4%) | 21076 | 528 |

Sparse request latency **degraded in this sample**. The largest p95 increase was GBPUSD wick. The additional terminal proof increased the response while performing the same number of decodes. The sparse process peak was 133,644 → 132,296 KiB. The genuine benchmark's warm process peak including inspection was 128,116 → 129,284 KiB. Overall memory improvement is **inconclusive**. The genuine inspection and cold medians improved in this sample. These are local process measurements. [Sparse baseline](../evidence/T10-ZONES-GAP/sparse-baseline.json) · [Sparse candidate](../evidence/T10-ZONES-GAP/sparse-final.json) · [Genuine baseline](../evidence/T10-ZONES-GAP/timing-baseline.json) · [Genuine candidate](../evidence/T10-ZONES-GAP/timing-final.json)

## Verification and delivery

The three workers were dispatched before the owner waited and all appeared as running in the post-dispatch snapshot. Each worked in a sibling checkout with private storage. Timing below is UTC on 26 November 2026.

| Lane | Model / effort | Dispatch | Start | Finish | Worker commits |
|---|---|---|---|---|---|
| gap_api | gpt-6.2-astra / high | 09:57:23.658 | 09:57:25.527 | 10:04:52.406 | 4dd979e, 018df92 |
| gap_ref | gpt-6.2-sol / high | 09:57:55.307 | 09:57:57.058 | 10:07:52.107 | c7df90c |
| gap_ui | gpt-6.2-sol / high | 09:57:44.299 | 09:57:46.660 | 10:07:49.918 | 144161b, 3fff17e |

The owner started at 09:57:11.036. Integration began 10:03:40.221. Code and test integration ended at 10:07:58.372, giving a 258.2-second integration span including rework. The API follow-up handled archived observations outside the admitted schedule. The first browser attempt reached the FVG invalidation but failed on a test helper that expected tuple intervals. The helper was corrected to read the fixture’s named start and end fields. The worker reran both browser phases successfully. During integration the benchmark adapter was updated to reuse the finalized fixture setup for both revisions. All three workers were active together for 415.35 seconds; their combined wall-clock window was 626.58 seconds. Measured from the first working commit to its corrective follow-up, the API change occupied 74 seconds, the browser harness 80 seconds, and the benchmark adapter 59 seconds. These rework windows overlap and cover 169 distinct wall-clock seconds. They measure elapsed intervals, including checks. No comparable serial implementation run exists.

**Starting revision** `4e03463a36f176b701ba36558bf03ffa9c8795a2`. **Combined-tested revision** `57157f1f4898d818c99505e95447bd99b774ab8b`. The final commit packages the Windows results and report. From start to tested commit, production changed 5 files (+67/−6 lines). Tests, browser configuration and runnable verification changed 7 files (+731/−1 lines). This separates code changes from evidence JSON, screenshots and documentation.

The portable [token accounting checkpoint](../evidence/T10-ZONES-GAP/usage-checkpoint.json) lists all observed owner, worker and approval-reviewer counters by model. Totals count cached input within input and reasoning within output. Its sampling cutoff is explicit. Internal provider retries and billed cost are unavailable. The post-push receipt in the handoff adds measured wall time from the user's request to verification of the remote branch and open PR head.

### Combined Windows results

| Check | Outcome | Elapsed |
|---|---|---:|
| Controlled sparse API | All 24 comparisons passed, including rewind | 68.72 s |
| Sparse Chromium | Initial and restarted selections passed for both modes and families | 13.91 s initial, 8.44 s restart |
| Genuine oracle | All 805 pre/at-availability records, 31 BOS proofs and previous controlled sequences matched | 231.85 s |
| Eight legacy fingerprints | Byte-identical to accepted file | 4.63 s |
| Genuine chart Chromium | Two initial and two restart tests passed | 118.69 s |
| Full backend | 1,160 passed, one POSIX-only FIFO skip, one existing Starlette TestClient warning | 772.13 s |
| Frontend unit tests and build | 13 passed, production build succeeded | 2.96 s and 21.82 s |
| Standard Chromium | 88 passed | 470.66 s |

Each full suite ran once on the combined candidate. All combined browser runs used zero retries. The backend completed last, 780.56 seconds after dispatch of the full suite group. The genuine oracle used the original 13 November 2026 EURUSD/GBPUSD M5 BID pair. [Genuine sequence and fingerprint comparison](../evidence/T10-ZONES-GAP/genuine-regression.json) · [Provider data pins](T10-ZONES.md#inputs) · [Exact combined commands and timings](../evidence/T10-ZONES-GAP/verification.json)

## Reproduction on Windows

The historical comparison checkout was created with `git worktree add --detach K:/Projects/TradeForge-t10-gap-base 4e03463a36f176b701ba36558bf03ffa9c8795a2`. The controlled verifier checks the Parquet bytes, independently reads the fixture rows, then queries the public API. [Before and after receipts for six cases](../evidence/T10-ZONES-GAP/API-ACCEPTANCE.md)

```powershell
New-Item -ItemType Directory -Force storage/t10-gap-integration | Out-Null
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
$out='storage/t10-gap-integration'
$baseline='K:/Projects/TradeForge-t10-gap-base'
foreach ($case in 'missing','contiguous','closure','closure-missing','delayed','unverifiable') {
    .venv/Scripts/python.exe -m scripts.verify_t10_zone_gaps --case $case --repo $baseline --output "$out/$case-base.json"
    if ($LASTEXITCODE) { throw "Baseline failed for $case" }
    .venv/Scripts/python.exe -m scripts.verify_t10_zone_gaps --case $case --phase fixed --baseline "$out/$case-base.json" --output "$out/$case-fixed.json"
    if ($LASTEXITCODE) { throw "Comparison failed for $case" }
}
$evidence='docs/implementation/evidence/T10-ZONES-GAP'
.venv/Scripts/python.exe -m scripts.benchmark_t10_zone_gaps --repo $baseline --output "$evidence/sparse-baseline.json"
.venv/Scripts/python.exe -m scripts.benchmark_t10_zone_gaps --output "$evidence/sparse-final.json"
.venv/Scripts/python.exe -m scripts.benchmark_t10_zones --source-storage storage --repo $baseline --output "$evidence/timing-baseline.json"
.venv/Scripts/python.exe -m scripts.benchmark_t10_zones --source-storage storage --output "$evidence/timing-final.json"
.venv/Scripts/python.exe -m scripts.verify_t10_zones --source-storage storage --output "$out/genuine.json"
.venv/Scripts/python.exe -m scripts.verify_t10_zone_compatibility --source-storage storage --output "$out/fingerprints.json"
$env:TRADEFORGE_E2E_API_PORT='8712'
$env:TRADEFORGE_E2E_UI_PORT='4712'
.venv/Scripts/python.exe -m scripts.verify_t10_zone_gap_browser --output "$out/sparse-browser"
.venv/Scripts/python.exe -m scripts.verify_t10_chart --zones --source-storage storage --output "$out/genuine-browser"
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_replay_zone_continuity.py
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8710'
$env:TRADEFORGE_E2E_UI_PORT='4710'
npm --prefix frontend run e2e -- --retries=0
```

## Rollback and scope

Stop the local frontend and API and restart them from the starting revision in a separate worktree against the same local storage. Keep the archived partitions, saved sessions, databases and browser profile. The correction adds descriptive evidence to a read-only projection and requires no database migration. Independent CRT lifecycle and confluence are later T10 gates. The genuine broker statement and 480 missing November 2025 EURUSD open hours remain separate dependencies. The public cap remains 160 cutoffs, historical intents retain `AWAITING_EXECUTION_VALIDATION`, and production recorded trading stays `BLOCKED`.
