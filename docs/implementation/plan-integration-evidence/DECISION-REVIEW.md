# Blind decision review on Windows

Start in **Learning Journal → Review a historical decision**. Select the symbol, decision ID and version. Read the original thesis, trigger, invalidation and risk alongside the pinned candles. Use the replay controls to revisit an earlier bar. Save an independent assessment and then choose **Reveal original outcome**. Compare the frozen plan with its receipt prices, sizes, costs and action times. Classify the process, enter one practice objective and select its UTC follow-up date. Reopen the decision from the list to practise and mark the drill completed.

## Local acceptance

Starting revision `217cc663cc683fd0e49928cdd901c74a77c3af98`. [The local receipt](decision-review-receipt.json) records the actual decision IDs, plan IDs, hashes, fills, source fingerprints, lane sizes and timings. Each invocation uses a disposable database, so newly issued trade IDs differ between invocations.

| Case | Before reveal | After reveal and restart |
| --- | --- | --- |
| 13 November 2025, recorded EURUSD bid/ask | Submitted plan v1 despite a later v2 edit. One M5 BID candle opened at 13:00 and was available at 13:05 UTC. No fill price, quantity, fee or realized result in the response. | Bought 1,000 EUR at 1.16211 ASK. Sold 400 at 1.16199 BID and 600 at 1.16194 BID. Cash moved from 10,000 to 9,999.85 USD. The 0.15 USD loss retained a **rule followed** assessment. |
| 13 November 2026, recorded EURUSD and GBPUSD M5 BID | The plan linked the 13:25 UTC confirmation. Both lanes had 14 bars; the last opened at 13:20. No pass reason or 13:30 invalidation appeared in the prompt. | The original no-trade reason and the 13:30 invalidation were available after reveal. The signal plan had zero orders. A **rule followed** objective remained due across restart and could be marked completed. |

The paper cash check is independent of the application ledger: `(1.16199 − 1.16211) × 400 + (1.16194 − 1.16211) × 600 = −0.15 USD`. The simulator assumed zero fees and zero delivery delay. The 2026 candle archives supplied signal evidence only.

The review page boots separately from the dashboard and journal feed. Before assessment, its detail response contains only `phase`, `prompt` and `prompt_hash`. A POST to reveal at that point returned 409. The saved assessment response added `assessment` and reported `ASSESSED`. It contained no outcome. Identical assessment submissions returned the same response. After restart an explicit reveal returned the original paper receipts or the no-trade event and signal history. Another restart reproduced both frozen prompts, assessments, outcomes and scheduled drills. The browser also checked its API requests throughout each review.

The review steps are append-only and unique per trade, revision and step. An immediate SQLite transaction gates each transition. The review captures the displayed prompt on assessment and captures the outcome on first reveal. Older entries without recoverable cutoff evidence show the evidence limitation. A local operator can separately open a ledger or journal outside the blind exercise.

## Source verification

Both Data Trust and paper entry use the same lookup of claims about the pinned candle partition. The paper action regression cloned the baseline database before submission. With an unrelated malformed claim the accepted receipt was byte-identical to baseline. Two competing claims returned HTTP 409 with `CONFLICTING_IMPORT_CLAIM` and left zero economic events. Existing immutable requests still replay through their original receipt.

## Reproduction

Run in PowerShell from the repository root with the pinned local archives available.

```powershell
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_decision_reviews.py research_backend/tests/test_import_claim_lookup.py research_backend/tests/test_plan_paper.py
.venv/Scripts/python.exe -m scripts.verify_plan_paper --reviews --output storage/decision-review-acceptance.json
.venv/Scripts/python.exe -m pytest -q
npm --prefix frontend test
npm --prefix frontend run build
$env:TRADEFORGE_E2E_API_PORT='8553'
$env:TRADEFORGE_E2E_UI_PORT='4553'
npm --prefix frontend run e2e -- --retries=0
```

The real-data harness completed eight fresh API/Chromium lifetimes. The three review phases took 11.84, 12.39 and 12.60 seconds, including process startup and shutdown. The focused API regressions passed 6/6. The frontend unit suite passed 13/13 and the production build succeeded.

Final implementation `a5f04f8cc9d31095c4549637031729d8619ef53d` passed 1,068 backend tests with one skip and one Starlette deprecation warning in 571.47 seconds. Standard Chromium passed 85/85 in 7.1 minutes with retries disabled. An earlier development run passed 84/85 while frontend modules were being edited. Its 100-cycle disposal counter measured 161 against a minimum greater than 200. The complete rerun on committed code recorded 686 creations, 685 disposals, one active chart, and no browser errors in the soak.

## Rollback

Stop the local API, revert the milestone commits and restart the previous checkout. Keep the SQLite file and archive roots together. Older code ignores the additive review table. Leave its immutable rows intact for a future upgrade. Original plan revisions, charts and paper receipts retain their existing formats. Month-scale replay, broker reconciliation and live execution require separate verification.
