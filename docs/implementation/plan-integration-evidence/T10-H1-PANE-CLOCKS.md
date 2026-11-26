# H1 evidence across unequal pane clocks and empty hours

Starting revision `f273342db68aa61a8694d61693d9e1c1afbb5ee0`. This increment applies to saved historical Dukascopy EURUSD and GBPUSD M5 chart replay. The admitted strategy cutoff limit is 160. Production recorded trading remains BLOCKED.

## What to inspect

In **Market Terminal**, freeze a saved M5 replay and open **H1 replay evidence**. Choose the primary or linked pane and an hour. Both panes answer as of the same replay clock. Each completed H1 receipt retains the availability and the twelve contributing M5 references of its own instrument.

A missing hour inside the captured range can be selected after later candles have appeared. The API distinguishes a scheduled closure from an open hour with missing evidence. Rewinding restricts the choices to hours reached by that cutoff. After restarting both processes, reopen the saved replay position and select the interval again.

## Before and after

The controlled fixture copies prices from the checksummed 13 November 2026 Dukascopy M5 BID CSVs. It changes the EURUSD final child's availability from 14:00:00 to 14:00:20 and omits the GBPUSD children opening at 14:00 through 14:55. The short session ends at 14:00. The separate longer session includes priced GBPUSD candles after the missing hour. These are controlled changes to disposable files.

| Probe (UTC) | Starting revision | Integrated result |
|---|---|---|
| 13:00 EURUSD hour at 14:00:19 | Unresolved | Unresolved, no OHLC, last child unavailable |
| Same EURUSD hour at 14:00:20 | Complete | Complete, available 14:00:20; O/H/L/C 1.10503 / 1.10693 / 1.10486 / 1.10499 |
| Same GBPUSD hour at 14:00:20 | HTTP 422, also on resume | HTTP 200, complete, available 14:00:00; O/H/L/C 1.32256 / 1.32503 / 1.32238 / 1.32319; twelve original child identities |
| GBPUSD 14:00 hour selected from 16:00 | API correctly reported REFERENCE_ABSENT. Picker offered only 13:00 and 15:00, also on resume | Picker offers 14:00. Its receipt is UNRESOLVED / REFERENCE_ABSENT with twelve missing opens, zero children and no OHLC before and after restart |
| GBPUSD 15:00 hour at 16:00 | Complete | Complete, available 16:00:00; O/H/L/C 1.32455 / 1.32465 / 1.32178 / 1.32210 |

The independent API check passed six of seven probes on the starting revision and seven of seven after the correction. The same four controlled browser assertions failed on the baseline and passed after integration. Swapping the primary and comparison instruments, choosing a shorter recent peer range and rejecting a cutoff outside the primary session were also exercised through the public API. A UI regression with controlled responses tests a scheduled closure, an unresolved open hour, a pending peer response, the truncated left boundary and rewind. Long sparse ranges use UTC day navigation to bound the number of hourly options.

[Unmodified archive hashes, child references and four genuine H1 expectations](../evidence/T10-H1/provider-reference.json)

[Before/after API and browser receipts, controlled partition IDs and hashes](../evidence/T10-H1-CLOCK/acceptance.json) · [Restarted peer pane](../evidence/T10-H1-CLOCK/delayed-restart.png) · [Restarted missing-hour inspection](../evidence/T10-H1-CLOCK/missing-restart.png)

## Parallel timeline

All times are UTC on 26 November 2026. The dispatch time is the clock sample immediately after the three-worker snapshot. Finish markers use the time each worker committed its last tested deliverable. No sequential comparison was timed.

| Worker ID | Model and effort | Snapshot at 06:27:48 | Finish | Deliverable |
|---|---|---|---|---|
| /root/clock_a | GPT-6.2 Astra, high | running | 06:31:50 | `5264f5f`, session clock and focused API tests |
| /root/range_b | GPT-6.2 Sol, high | running | 06:34:41 | `bbc2495`, panel and sparse-range browser regression |
| /root/verify_c | GPT-6.2 Sol, high | running | 06:37:31 | `350ecd5` harness and `f77f9f5` receipt, integrated as `b7eac60` and `20f1555` |

Integration started at 06:32:28 with A’s tested change while B and C were still working. The combined code commit `4a231d45bb96bb7775dd888d2c211246d6301155` was created at 06:34:57. The full Windows checks began at 06:36:42 after the integrated focused checks. Subsequent commits contain evidence and documentation only. Workers used sibling worktrees, independent test storage and assigned API/UI ports 8631/4631, 8632/4632 and 8633/4633, respectively. The backend API suite used in-process clients.

## Reproduction

Run from the repository root using the pinned files under local storage. Browser phases use temporary databases and fresh processes.

```powershell
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_replay_hour.py research_backend/tests/test_workspace_tools.py research_backend/tests/test_indicator_coverage.py research_backend/tests/test_crt.py
.venv/Scripts/python.exe -m scripts.verify_t10_h1_clock --source-storage storage --output storage/h1-clock-integration/controlled-api --part api
$env:TRADEFORGE_E2E_API_PORT='8641'
$env:TRADEFORGE_E2E_UI_PORT='4641'
.venv/Scripts/python.exe -m scripts.verify_t10_h1_clock --source-storage storage --output storage/h1-clock-integration/controlled-browser --part browser
.venv/Scripts/python.exe -m scripts.verify_t10_h1 --source-storage storage
.venv/Scripts/python.exe -m scripts.verify_t10_chart --h1-replay --source-storage storage --output storage/h1-clock-integration/genuine-browser
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8640'
$env:TRADEFORGE_E2E_UI_PORT='4640'
npm --prefix frontend run e2e -- --retries=0
```

## Combined Windows results

| Check | Observed | Time |
|---|---|---:|
| Focused backend replay, calendar, workspace and CRT | 67 passed | 17.91 s |
| Controlled API | 7 of 7 probes matched | 3.24 s |
| Controlled Chromium | 2 initial + 2 restart tests passed, zero retries | 16.98 s + 11.82 s |
| Existing genuine API oracle | All four complete H1 candles and boundary probes matched | 13.35 s |
| Existing genuine Chromium | Both phases passed, all four H1 values and 48 source references matched | 21.39 s + 12.17 s |
| Full backend | 1,133 passed, one POSIX-only skip, one Starlette deprecation warning | 696.62 s |
| Frontend units and production build | 13 passed; build passed with dependency bundler warnings | 1.84 s / 15.17 s |
| Standard Chromium | 88 passed, zero retries, including 100 navigation cycles and recorded-review selection | 460.03 s |

The full suites each ran once on the combined code. The controlled regressions used private test databases. The provider files remained local.

## Rollback

Stop the local API and frontend. Run both from `f273342db68aa61a8694d61693d9e1c1afbb5ee0` in a separate checkout with the same workspace. This restores the earlier H1 cutoff and interval-selection behavior.
