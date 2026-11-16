# TF-I03 structure continuity and pagination review

TF-I02 merged at `bb84f60a539fe962d56de6a819ba04a57151d0c6`. It is the branch base. The TF-I03 implementation and tests are in `8510a6a60f6b2d0ebb6d188ec14b3af48ab0501c`. The evidence commit follows it.

## Implementation

- `structure_continuity.py` delegates UTC/IANA session bounds to TF-I01's calendar. Policy v1 recognizes a scheduled gap only when both bracketing candles are completely within aligned open intervals and the entire skipped interval is closed under that schedule. No synthetic observations are inserted. An unknown or ambiguous schedule, an unaligned boundary, or expected missing open candles resets continuity and produces a structured reason. A continuous market expects every slot.
- `market_structure.py` retains algorithm v1 as the default for existing API clients and workspace settings. The previously reviewed first pivot and CHOCH IDs are pinned as exact 64-character legacy fixtures. V2 is explicit and retains active references and the real-bar pivot window across a scheduled closure. It exposes reason counts and the 40 latest transitions. V2 uses new event identities. The closure claim is a configured assumption about trading hours. In an archive calculation, the source integrity status is a separate verification of the Parquet checksums.
- On reopening, the engine compares the real candle open, wick and close with earlier eligible references. The result can record an opening cross that reclaims the level, a wick-only crossing, or a close-confirmed crossing. A CHOCH still needs established opposing structure, the protected swing and a qualifying close. Reopening data and right-side swing evidence remain unavailable until the real candle is closed and available. A pending pivot can use observations on both sides of a verified scheduled gap.
- `structure_pages.py` keeps a bounded deque for each result collection while the full state machine continues. Default page limits are 80 pivots and 80 breaks. Each category can request 0–500, with a separate opaque backward cursor. Pages keep increasing engine emission order, including deterministic same-candle order. Metadata includes independent totals, bounds, returns and truncation. Complete evidence can be exported by requesting successive pages until both cursors are exhausted. A zero limit omits an already exhausted collection in a combined request.
- `indicator_engine.py` and `indicator_routes.py` bind each cursor to its collection and a digest of cutoff, rule and source metadata, resolved provenance, and candle inputs eligible at the cutoff. The digest also includes eligible arrivals discarded as late. A changed snapshot returns HTTP 409. No chart events are inferred from page contents or recomputed in the browser. Each requested archive page reruns the bounded evaluation and the original SHA-256 checks.
- `frontend/src/indicators.tsx` adds a v2 selector, per-feed IANA schedule settings, closure warnings and source status. Older pages stay in a separate evidence section; the chart continues to use the latest response. Active scope and request keys discard old pages and pending results when the timeframe, symbol or replay cutoff changes. No new chart series are created for browsing.

## Exact continuity and chronology fixtures

New York local 17:00, after autumn DST. Friday close is **2026-11-06 22:00 UTC** and Sunday reopen is **2026-11-08 22:00 UTC**. The last M1 candle starts Friday at 21:59. The Sunday candle first becomes eligible at 22:01. With closes **10, 12, 10, 13, 11, 14, 12** ending Friday and a Sunday close of **15**, v2 retains bullish structure and emits a bullish BOS at level **14**. The pivot at 21:58 was available Friday at 22:00. V1 resets and emits no reopening BOS. Moving the exact gap three days forward places both bracketing candles and the missing interval during trading hours. The same elapsed time is then `MISSING_OPEN_CANDLES` and v2 resets.

A second fixture appends 15 and 13 before Friday close, leaving an eligible HL at 12, then closes at 10 on Sunday. V2 emits bearish CHOCH at 22:01, while the one-second-earlier cutoff has no Sunday break. Reflecting the sequences around 15 independently verifies bearish BOS and bullish CHOCH. An opening above 14 with close 13.5 remains provisional, even though the wick crosses. A confirmed weekend pivot with a delayed Sunday observation becomes available at 22:02, never at its nominal 22:01 close. Spring/autumn New York and London timezone cases are independently specified.

The tests replay earlier cutoffs again after evaluating later ones and compare the visible observations exactly. The 10,000-bar archive fixture emits 8,180 pivots and 4,544 breaks, with independent page sizes and no duplicate IDs across pages. Same-timestamp HIGH and LOW split cleanly; requesting a cursor twice returns the same event. Changed input, cutoff, symbol, and corrupted Parquet all reject stale cursors.

## Verification

| Check | Result |
| --- | --- |
| Backend | 133 passed, including 14 new continuity/pagination tests |
| Continuity / compatibility | 12 cases passed, included above |
| Pagination | 3 new cases passed, with one overlapping continuity case, plus the updated legacy archive traversal |
| Replay | 5 new backend cutoff cases passed, including four reopening comparisons and delayed pivot evidence |
| Vitest | 8 passed |
| TypeScript/Vite production build | Passed |
| Chromium | 46 passed, including 44 existing cases |

`test_structure_continuity.py` covers the new numeric and archive cases. Chromium verifies a real reopening BOS and rewind, bounded older-page navigation, and the existing scope-switch, drawing and structure cases. It uses deterministic candle/replay fixtures with the real evaluator. Verification used an isolated test workspace on ports 4198/8098. Run `..venvScriptspython.exe -m pytest -q` from the repository root and `npm test`, `npm run build`, `npm run e2e -- --reporter=line` in `frontend`.

[Backend](evidence/TF-I03/backend.txt) · [Vitest](evidence/TF-I03/vitest.txt) · [Build](evidence/TF-I03/build.txt) · [Chromium](evidence/TF-I03/playwright.txt) · [Weekend replay](evidence/TF-I03/weekend-structure-replay.png) · [Older page](evidence/TF-I03/bounded-structure-evidence.png)

## Performance

Windows, Python 3.13, 12 warmed samples, concurrent local verification. Calculation includes verification and decode for archive rows. Serialization measures compact JSON of the structure result. Python allocations use one separate traced call; RSS and native Arrow allocations were not measured. TF-I02's previously recorded 10,000-row calculation was 576.65/717.13 ms median/P95. The same-run baseline below runs the original detector from the merge commit and includes an equivalent verified read for archive cases. Current timings include the evaluator's request and provenance overhead.

| Fixture | TF-I02 vs TF-I03 calculation P50/P95, ms | TF-I02 vs TF-I03 serialization P50/P95, ms | TF-I02 vs TF-I03 bytes |
| --- | --- | --- | --- |
| 2,000 dense | 76.80/92.98 → 82.18/106.51 | 18.30/24.29 → 0.77/1.11 | 1,601,969 → 106,451 |
| 2,000 flat | 19.41/38.91 → 33.65/38.51 | 0.05/0.24 → 0.10/0.22 | 261 → 1,367 |
| 2,000 over scheduled closure | 64.46/86.65 → 99.69/115.27 | 14.92/21.52 → 1.08/2.75 | 1,600,691 → 106,722 |
| 2,000 with missing open slots | 8.43/10.27 → 21.36/36.93 | 0.03/0.09 → 0.15/0.27 | 285 → 7,737 |
| 10,000 archive, first page | 459.41/711.57 → 512.78/749.36 | 84.78/130.14 → 1.10/3.25 | 8,011,745 → 107,332 |
| 10,000 archive, next page | 459.41/711.57 → 559.00/764.08 | 84.78/130.14 → 1.00/3.65 | 8,011,745 → 107,279 |

Each archive page examined five partitions and decoded 10,000 candles. Traced Python peak for the same-run archive fell from 21,828.3 KiB to 15,417.0 KiB on the first page, and the next page used 15,413.9 KiB. The complete log includes counts and memory for each case. Reproduce from the root with `Get-Content docs/implementation/evidence/TF-I03/benchmark.py -Raw | ..venvScriptspython.exe -`. [Benchmark script](evidence/TF-I03/benchmark.py) · [Measured output](evidence/TF-I03/benchmark.txt)

## Limits and rollback

Configured weekly schedules have no broker holiday or intraday-exception feed. Coarse bars straddling a boundary reset conservatively. Each page replays the bounded input and rechecks archive files, so latency scales with the evaluation window. Cursor export requires a fixed cutoff and consistent input evidence. Live MT5 and physical device behavior were outside local verification.

Revert the implementation and evidence commits to roll back. No schema or datasets migrate. Older cached v1 consumers can continue to use the same algorithm and IDs, with bounded delivery on this version of the API.
