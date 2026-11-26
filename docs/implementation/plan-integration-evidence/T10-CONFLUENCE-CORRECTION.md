# Historical confluence coverage and saved receipt discovery

## Operator result

In Market Terminal, reopen the saved paired EURUSD/GBPUSD M5 replay, set its UTC clock and expand **Replay CRT and SMT confluence**. Select **Current** to inspect the selected cutoff. When a delayed candle determines first availability, expand **Coverage recovery witnesses** to see its instrument, observed open, nominal close, availability basis and time, source ID and checksum-pinned partitions. The CRT and SMT cards retain the revision and publication evidence for each condition.

Choose **Save exact receipt**. On any local browser profile connected to this workspace, reopen the same replay and expand the inspector. Its saved choices now come from SQLite, limited to the replay session, profile and chart cutoff. The index orders by cutoff and then by ID for ties. **Load older saved receipts** retrieves the next page. Choosing a saved ID opens its original payload and verifies the frozen archive bytes. Browsing the list does not decode market data. [Measured API behavior and source hashes](../evidence/T10-CONFLUENCE/correction-api.md)

## Coverage at the quiet candle

These delayed arrivals are controlled inputs on checksum-pinned M5 BID Parquet. Delivery in the genuine Dukascopy comparison uses modeled nominal close. The same producer revisions were selected on both sides of the controlled interruption.

| 13 November 2026 UTC | Original writer | Corrected current receipt |
|---|---|---|
| 14:09:59 | ALIGNED | ALIGNED; no delayed witness |
| 14:10:00 through 14:10:16 | INCOMPLETE | INCOMPLETE; the EURUSD open interval [14:05, 14:10) is missing |
| 14:10:17 | ALIGNED; join time advances without evidence for the quiet EURUSD candle | ALIGNED; exact EURUSD recovery witness appears |
| 14:10:18 | ALIGNED | Same source and first availability at 14:10:17 |
| Both lanes delivered at 14:10:17 | ALIGNED | Two witnesses, one from each instrument at the same instant |

The EURUSD source ID is `6197eb048229efd0cc153dd8c0e0e14eda15ae3c5feef354356bf9fd1b49af14:1794578700`, and the partition SHA-256 is `85c512b48e2b33f02a5942ab6150d10e9127feed6d0a79fc6d6ff2b5fabe725b`. The recovered join was bearish, with unchanged CRT revision `612f13ad15f3a492aa0a665ddbdadc0eece1c0a0b1e885d28325eb6c04142b20` and SMT revision `eefac556b6cafb1191e43d22b3a05f3e4ed1faa013d9cf3cb9b3aaf23fdee0d3`. [Before/after API receipts and raw payload hashes](../evidence/T10-CONFLUENCE-CORRECTION/api-before-after.json)

## Historical records and continuity

The selection profile remains `CRT_SMT_M5_V1`. Newly evaluated receipts use rule version 2 and include the list of delayed observations that establish the join clock. The old writer saved two receipts in each of three isolated databases. After upgrade, all six V1 records reopened with their original IDs and byte-identical SQLite payloads. An identical historical save by ID returned the existing original. A V2 preview at the same cutoff has a distinct content hash. The inspector explicitly identifies the absent recovery evidence in V1.

The independent market-data reference passed all 516 cutoff comparisons, 1,032 rewind reads and 52 restart reads. Compared with the prior committed oracle, all 85 material transitions retained the same timestamps, verdicts, reasons and producer identities. All 85 new preview receipt IDs changed with the new receipt representation. The genuine bearish interval still runs from 13:25 inclusive to 13:30 exclusive, with two aligned probes in 97 inspections. The disjoint control recorded zero alignments in 45 inspections. Eight historical standalone component fingerprints also matched. [Per-case reconciliation](../evidence/T10-CONFLUENCE-CORRECTION/oracle-comparison.json)

## Rollback

Stop the local API and frontend and run both from the starting revision, `7cdacaf4c10bdd1073128c0a4d10b2e34be7c7f6`. Retain the SQLite databases, archive files and browser profiles. The added expression index and immutable V2 rows can stay in place. The earlier UI uses its browser-local receipt menu and cannot inspect V2 recovery witnesses. Restart the corrected version to discover both receipt generations again. Public admission remains 160 cutoffs, historical intents retain `AWAITING_EXECUTION_VALIDATION`, and production recorded trading remains `BLOCKED`.

## Saved menu under concurrent work

On the starting writer an actual save succeeded, but the new browser showed zero saved choices for that session. The SQLite discovery route returned HTTP 404. [Original Chromium assertion](../evidence/T10-CONFLUENCE-CORRECTION/fresh-profile-baseline.txt) On the corrected application, the two-tab tests released A then B in one empty workspace, and B then A in another. Each release was verified from a fresh SQLite connection. Counts went 0 → 1 → 2 in both orders. The tests stopped the API, frontend and browser, started new processes with an empty browser profile, and reopened both IDs, component revisions and archive pins. Retrying both POSTs kept exactly two rows. Reversing the pair created a distinct replay session whose list was empty and whose exact GET and POST rejected both foreign IDs with HTTP 409. [Completion order, process IDs, selected verdicts and stored hashes](../evidence/T10-CONFLUENCE-CORRECTION/two-tab-restart.json)

A separate one-tab control delayed the save across collapse/reopen and held a stale list response. The receipt appeared once after publication and remained visible after the obsolete response was released. Rewinding before a saved cutoff hid its receipt. Advancing restored it. Indexed keyset pagination is limited to 24 entries by default and 48 per request. Deep pagination can still scan newer records while applying its cursor. The benchmarks below used two saved receipts.

| Windows public API | Samples | Starting median / p95 ms | Corrected median / p95 ms | Corrected SQL SELECTs | Verified bytes / decoded rows |
|---|---:|---:|---:|---:|---:|
| List, warm | 30 | No endpoint | 9.49 / 57.38 | 3 | 0 / 0 |
| List, fresh process | 4 | No endpoint | 52.38 / 59.42 | 3 | 0 / 0 |
| Exact reopen, warm | 30 | 19.28 / 21.44 | 21.10 / 26.10 | 5 | 20,818 / 528 |
| Exact reopen, fresh process | 4 | 355.01 / 409.40 | 342.37 / 348.18 | 5 | 20,818 / 528 |

Cold samples started fresh API processes. The OS cache was uncontrolled, and other development workloads overlapped. Warm reopen was slower and fresh-process reopen faster in this run. Lasting performance change is inconclusive.

[Recovery witness shown in Chromium](../evidence/T10-CONFLUENCE-CORRECTION/quiet-coverage-recovery.png) · [Quiet-candle receipt reopened in new API and browser processes](../evidence/T10-CONFLUENCE-CORRECTION/quiet-reopened.json) · [Delayed-save and rewind result](../evidence/T10-CONFLUENCE-CORRECTION/discovery-delayed-save.json) · [Older-page selection](../evidence/T10-CONFLUENCE-CORRECTION/discovery-pagination.json)

## Reproduce on Windows

Use a separate checkout of the original writer for upgrade tests. The headed application need not be running. These commands use disposable test storage. The source archives stay local.

```powershell
git worktree add --detach ../TradeForge-confluence-original 7cdacaf4c10bdd1073128c0a4d10b2e34be7c7f6
$env:TRADEFORGE_T10_SOURCE_STORAGE=(Resolve-Path storage).Path
.venv/Scripts/python.exe -m scripts.verify_t10_confluence_correction --baseline ../TradeForge-confluence-original --candidate . --source-storage storage --output storage/confluence-upgrade.json --warm-samples 30 --cold-samples 4
.venv/Scripts/python.exe -m scripts.verify_t10_confluence_browser_upgrade --baseline ../TradeForge-confluence-original --output storage/quiet-witness-browser
.venv/Scripts/python.exe -m scripts.verify_t10_confluence_browser --case controlled --two-tab-completion-orders --output storage/confluence-two-tabs --api-port 8772 --ui-port 4772 --samples 1
.venv/Scripts/python.exe -m scripts.verify_t10_confluence_browser --case controlled --phase discovery --output storage/confluence-discovery --api-port 8772 --ui-port 4772 --samples 1
.venv/Scripts/python.exe -m scripts.verify_t10_confluence --source-storage storage --output storage/confluence-oracle.json
```

The original writer’s fresh-profile test failed with no saved option after a successful POST. The corrected browser harness also uncovered a test setup assumption about the number of panes after launching a fresh browser. It now configures the two panes explicitly before verifying foreign-session isolation. The first quiet-case runner failed serializing the independent reference’s Decimal values; it now uses the existing reference writer. An integration check exposed an obsolete pagination cursor on rewind and return. The corrected hook starts from the first page and retains exact selected-ID access. The three workers ran in independent sibling worktrees, SQLite stores and browser ports.

| Lane | Model | Dispatched / active / finished UTC on 26 November | Worker commits |
|---|---|---|---|
| /root/coverage | GPT-6.2 Astra high | 12:26:44.251 / 12:26:45.764 / 12:35:03.346 | 2e45cb5 |
| /root/discovery | GPT-6.2 Sol high | 12:26:52.708 / 12:26:54.370 / 12:48:42.014 | 4a47af1, 5259b4d, cde122e, f9832a8 |
| /root/verify | GPT-6.2 Sol high | 12:27:06.153 / 12:27:07.992 / 12:38:46.626 | 28860b4, 90045b6 |

The owner started at 12:26:30.998 UTC. The 12:27:10 snapshot recorded all three workers running. All three overlapped for 475.354 seconds. The first worker commit landed on the integration branch at 12:33:11 UTC. The combined full-suite run began at 12:44:39.520 UTC. The quiet-runner serialization repair and verified rerun took 50.098 seconds from first failure output. The two-order browser setup correction and rerun spanned 213.550 seconds from observed failed output to verified completion. These intervals include execution and polling. Other review-driven refinements did not have separate engineering timers. The final delivery receipt on PR #37 records the verified push, churn and per-agent totals through its sampling time.

## Combined verification

Combined-tested code and tests: `e28a5d0a87e412d4509799dd6afa72d817a62dfb`. The final evidence commit updates documentation only.

| Check | Result | Windows elapsed s |
|---|---|---:|
| focused | 20 passed, 1 warning in 10.60s | 11.57 |
| compat | 8 standalone component responses verified | 2.84 |
| oracle | 516 cutoffs, 1,032 rewind reads, 52 restart reads passed | 164.58 |
| orders | 1 passed (13.0s); 1 passed (26.2s); 1 passed (34.0s); 1 passed (53.3s) | 151.75 |
| discovery | 3 passed (1.2m) | 85.50 |
| quiet | 1 passed (15.1s); 1 passed (13.6s) | 48.35 |
| prior-browser | 1 passed (36.4s); 1 passed (17.2s); 1 passed (37.3s); 1 passed (16.4s) | 128.29 |
| backend | 1188 passed, 1 skipped, 1 warning in 826.89s (0:13:46) | 828.83 |
| build | TypeScript and production build passed | 25.07 |
| unit | Test Files  4 passed (4); Tests  13 passed (13) | 3.51 |
| standard | 88 passed (9.3m) | 561.54 |

Full backend, unit, build and standard Chromium checks each ran once. Chromium had zero retries. One backend POSIX FIFO test is platform-specific. [Exact commands, runtimes and outputs](../evidence/T10-CONFLUENCE-CORRECTION/verification.json) · [Usage through the pre-push sample](../evidence/T10-CONFLUENCE-CORRECTION/usage-checkpoint.json).
