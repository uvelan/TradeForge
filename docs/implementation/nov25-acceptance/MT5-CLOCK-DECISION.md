# Can the retained November MT5 ticks support EURUSD replay?

**Decision on 27 November 2026: REJECTED before monthly replay.** The capture identifies the server, but the available evidence cannot establish both its November 2025 tick clock and its effective EURUSD quoting and trading sessions. The original capture and timestamps remain intact. No correction or economic replay was applied.

## Evidence examined

- The 30 dated arrays and their manifest were captured with UTC-aware query bounds and original millisecond timestamps. The server fingerprint in the manifest matches the first 24 hex digits of SHA-256 of the local terminal history directory name `MetaQuotes-Demo`. The folder contains tick history; those prices do not independently date the server clock. There were no November 2025 daily terminal logs in the inspected profile log directories. Searches of the project evidence, storage filenames and Downloads filenames found no dated server-clock record or EURUSD holiday notice. [Capture writer](../../../scripts/capture_nov25_mt5.py) · [Daily checksums](archive/mt5-capture.json)
- MetaQuotes documents UTC for the Python range query and tick data. The MQL session interfaces return weekday schedules. They take no historical effective date. The general API documentation supplies neither a dated server configuration nor a November 2025 EURUSD exception calendar. [Python range query](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py) · [Quote and trade schedules](https://www.mql5.com/en/book/automation/symbols/symbols_sessions)
- On 10 November **2026**, a forum moderator described the then-current MetaQuotes-Demo winter/summer offsets and European changeover. The answer does not establish their effective dates for the retained 2025 data or give dated EURUSD sessions. A separate published audit examines bars from August 1, 2025 through October 7, 2026, spanning all four disputed November Fridays. It infers winter UTC+2 from H1 closing bars against an assumed New York Friday close. This is a lead, but its method uses the very close that needs independent verification. The Exness gold holiday observation cannot establish an EURUSD exception. [Moderator response #6](https://www.mql5.com/en/forum/516958) · [Broker Clock Auditor, method and symbol results](https://www.mql5.com/en/code/78123)
- Targeted searches of MetaQuotes and MQL5 publications for the named server, EURUSD, November 2025, Thanksgiving and the November 28 close yielded no operator-issued notice covering the disputed hours. This describes the sources examined; a private or unindexed operator record may exist.

## Measured baseline on the merge

The independent verifier and product audit agreed on every one of the 720 raw-hour classifications and the open-session gap endpoints. The command exited 0 for agreement. Both retained the market rejection. Under the existing New York Sunday 17:00 open / Friday 17:00 close model, 472 open hours contained observations, ten open hours were empty, 230 closed hours were empty, and eight closed hours contained 12,071 quotes. There were 45 open-session gaps under the existing 60-second policy. The capture contains 1,121,792 observations. These are results for the existing model; the model is not a broker session attestation.

| Friday in November 2025 | Rows at raw hour 22 | Rows at raw hour 23 |
|---|---:|---:|
| 7 | 1,647 | 1,270 |
| 14 | 1,540 | 1,496 |
| 21 | 2,023 | 1,565 |
| 28 | 1,278 | 1,252 |

The raw hours above are rendered using UTC with no shift. The ten missing hours were 22 and 23 on November 2, 9, 16, 23 and 30 under that same interpretation. A symmetric pattern cannot certify its own timezone. Delivery times were not recorded. [Raw-hour witnesses, gaps and daily pins](independent-windows-replay.json)

## What would change the decision

Obtain a dated server-operator specification or independently timestamped historical records that bind the stored tick fields to UTC, including effective intervals and clock changes. Also obtain the EURUSD quote and trade sessions in the same clock domain, including the status of each disputed interval and any November 28 holiday exception. If conversion changes the month boundaries, verify the adjacent source coverage needed for the UTC interval. Recompute all 720 UTC hours and the exact open-session gaps against that evidence. Any remaining unexplained absence keeps monthly replay ineligible. A present-day session query, another feed's calendar or matching Friday prices cannot fill these evidence gaps.

## Reproduction

Run in a clean tracked checkout of merge `9d327f3c8f1eacd9c3d4870302ffa817db5a6820` with the retained local capture. The command verifies the manifest and the daily files; only its report is written.

```powershell
.venv/Scripts/python.exe scripts/verify_nov25_windows.py --mt5-capture storage/nov25/mt5/capture.json --mt5-sha256 5431d15fa8a24b63b5f5b1f62241caa52110049ab8c87c8d1f7f5783d84d606c --mt5-preflight . --output storage/nov25-clock/verification.json
```

The saved product verdict was `REJECTED` with `RECORDED_CLOCK_CONTRADICTS_REPLAY_CALENDAR` and `OPEN_SESSION_COVERAGE_INCOMPLETE`. Its hourly receipt digest was `31943d30975d5218d2be7364f4b3f4b77042827e1acb3574196d9c047d4b238f`. This evidence-only change leaves the public cap at 160 cutoffs, historical intents at `AWAITING_EXECUTION_VALIDATION`, and production recorded trading `BLOCKED`.

Independent review used the ponytail-review skill to check the source coverage, rejection and reproduction instructions. Three focused MT5 audit tests passed. The capture manifest and all thirty daily hashes verified again after the audit.
