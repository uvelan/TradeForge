# H1 candle evidence in saved M5 replay

This reader supports saved Dukascopy EURUSD and GBPUSD M5 archive ranges under the forex weekly calendar. The public strategy admission cap remains 160 cutoffs and production recorded trading remains BLOCKED.

The verification window is 13 November 2026, 13:00–15:00 UTC. Each lane uses Dukascopy BID candles. A child is identified by its UTC open and its immutable source partition. Delivery is modeled at nominal M5 close with zero delay for the recorded case.

## Independent reference

The reference reads the checksum-pinned provider CSV, checks the twelve expected UTC opens in each hour and calculates the first open, maximum high, minimum low and last close. Times in the table are UTC. Prices are quote currency per unit of base currency.

| Symbol | Open interval | Open | High | Low | Close |
|---|---|---:|---:|---:|---:|
| EURUSD | [13:00, 14:00) | 1.10503 | 1.10693 | 1.10486 | 1.10499 |
| GBPUSD | [13:00, 14:00) | 1.32256 | 1.32503 | 1.32238 | 1.32319 |
| EURUSD | [14:00, 15:00) | 1.10501 | 1.10598 | 1.10429 | 1.10568 |
| GBPUSD | [14:00, 15:00) | 1.32321 | 1.32508 | 1.32245 | 1.32456 |

| Symbol | Provider CSV SHA-256 | Saved M5 Parquet SHA-256 |
|---|---|---|
| EURUSD | `bc2e49a78edad37b73ac1bcdc006892f54aab60c3e769ec837998773cb5960f6` | `fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d` |
| GBPUSD | `a2b4947d5e79198920589263a9626e476012650a09d8ba52386e244ab78c1928` | `aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02` |

[Provider reference with dataset IDs and all 48 child rows](../evidence/T10-H1/provider-reference.json)

## Market Terminal workflow

1. Choose the Dukascopy saved M5 range in Market Terminal. Set EURUSD as the primary and GBPUSD as a linked pane and freeze the replay.
2. Seek to 13:55 UTC. Inspect the forming hour, then advance one M5 candle to 14:00. Open **12 source candles** in **H1 replay evidence** to inspect the UTC opens, modeled availability, OHLC, source IDs and checksums.
3. Select the other pane. Advance to 15:00, then rewind to 14:00 and return to 15:00. Save the replay position, restart the API and browser, and select that session under **Resume saved replay**.

The UTC boundary probes returned the following results on both recorded lanes. Each completed row exactly matched the independent OHLC table above and all twelve source references. At 14:59:59 the earlier completed hour remains inspectable.

| Cutoff UTC | Hour being inspected | Observed result |
|---|---|---|
| 13:59:59 | [13:00, 14:00) | FORMING, no completed OHLC or children |
| 14:00:00 | [13:00, 14:00) | COMPLETE, 12 children per symbol |
| 14:59:59 | [14:00, 15:00) | FORMING, no completed OHLC or children |
| 15:00:00 | [14:00, 15:00) | COMPLETE, 12 children per symbol |

The API supports second-resolution probes. The ordinary chart moves between closed M5 candles. In Chromium a real response for the 14:55 cutoff was held until the 15:00 result was displayed. Releasing it preserved the 15:00 OHLC and child IDs. After both processes restarted, the saved 15:00 position reopened on GBPUSD. Rewinding then recovered the twelve earlier child IDs. The session ID was `e5bcdbe2a81b0d9dbb3e806d2754888335d423d2e2109fed296b7335145b9c29`.

Controlled copies covered two different causes of missing evidence. Removing the EURUSD child opening at 13:25 left the first hour unresolved at both 14:00 and 15:00. Delaying the final GBPUSD child by 20 seconds left its hour unresolved through 14:00:19. It completed at 14:00:20. Appending a disjoint second partition preserved the earlier OHLC, availability and full child array across sessions. A separate checksum-corruption test returned HTTP 409.

[API results](../evidence/T10-H1/api-replay-verification.json) · [Observed browser values and source IDs](../evidence/T10-H1/browser/h1-chart-journey.json) · [Reopened evidence screenshot](../evidence/T10-H1/browser/reopened-h1-evidence.png)

## Parallel work and verification

Starting revision `616a67798e54e03d74df5e5a33591aef5526e34b`. Combined Windows-tested revision `2d9357681927011e9fb730162397414fc6c8a73b`. The final handoff commit changes documentation only.

| Worker ID | Requested model and effort | Post-dispatch state | Worker commits |
|---|---|---|---|
| /root/h1_a | GPT-6.2 Astra, high | running | f9c307e, f637399 |
| /root/h1_b | GPT-6.2 Sol, high | running | 1ce090a, 4376c3e, a445d58 |
| /root/h1_c | GPT-6.2 Sol, high | running | c01b139, 818ec3c |

On the PR branch, the calculation and oracle retain their original identities. The corresponding cherry-picked commits are `547b5a6` for A’s API tests, `f8095e1` and `8d17504` for B’s verifier and receipt, and `ea9b6c2` and `8f3baad` for C’s UI and receipt.

Workers used sibling worktrees and private test databases. The reference and API lanes used in-process clients. Port pairs 8611/4611, 8612/4612 and 8613/4613 were assigned to A, B and C respectively.

Run from the repository root in PowerShell with the original files in local storage:

```powershell
.venv/Scripts/python.exe -m scripts.t10_h1_reference --source-storage storage
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_replay_hour.py research_backend/tests/test_workspace_tools.py research_backend/tests/test_indicator_coverage.py research_backend/tests/test_crt.py
.venv/Scripts/python.exe -m scripts.verify_t10_h1 --source-storage storage
$env:TRADEFORGE_E2E_API_PORT='8621'
$env:TRADEFORGE_E2E_UI_PORT='4621'
.venv/Scripts/python.exe -m scripts.verify_t10_chart --h1-replay --source-storage storage --output storage/t10-h1-integration/browser
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8620'
$env:TRADEFORGE_E2E_UI_PORT='4620'
npm --prefix frontend run e2e -- --retries=0
```

### Combined Windows results

| Check | Result | Elapsed |
|---|---|---:|
| Focused replay, workspace, calendar coverage and CRT API | 64 passed | 14.93 s pytest |
| Independent API and source verifier | All eight genuine boundary probes and three controlled cases passed | 12.84 s |
| Genuine Chromium journey | Both the initial and restart phases passed, zero retries | 17.55 s + 9.17 s |
| Full backend | 1,130 passed, one Windows-inapplicable POSIX FIFO test skipped, one Starlette deprecation warning | 681.53 s pytest |
| Frontend unit suite | 13 passed | 1.79 s including npm |
| TypeScript and production build | Passed with dependency bundler warnings | 19.73 s including npm |
| Standard Chromium | 87 passed with zero retries, including the 100-cycle navigation test and the recorded-review package selection regression | 462.28 s including npm |

The genuine browser runner starts each phase separately, selecting its applicable test. The combined run reproduced the committed observed JSON (Git blob `f9c0696f682cc48cdc052417262f3bc03757a915`). The public API comparison matched the committed receipt on every field. All full suites ran once on the combined revision above.

## Rollback

Stop the local application and run the API and frontend from the prior revision `616a67798e54e03d74df5e5a33591aef5526e34b` in a separate checkout. Retain the local archive and replay-session database.
