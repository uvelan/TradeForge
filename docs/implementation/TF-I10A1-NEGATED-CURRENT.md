# TF-I10A.1 negated CURRENT correction

The reviewed starting SHA was `47ee2ae3e389ee9f007340a02b42b9c95ab61771`. The fetched head of PR #17 matched. This correction stays on `feat/TF-I10A1-incremental-historical-evaluation`.

## Canonical reproduction

The literal CURRENT FVG_STATE request was refused by the reviewed API because FVG_STATE is historical-only. The API-supported selector for CURRENT PARTIALLY_MITIGATED is FVG_ACTIVE. The admitted NOT uses the required PROPAGATE policy. The baseline was replayed through the normal strategy-definition and evaluation API in a clean export of the reviewed SHA, with the canonical archive fixture installed in isolated storage.

At BASE+3000, the formation rule matched ACTIVE and the prohibited CURRENT state was absent. At BASE+3300, the same family's PARTIALLY_MITIGATED revision was eligible and also satisfied the historical trigger. The v5 latch retained NOT_SATISFIED for the CURRENT leaf, even though the revision now matched. V6 retains the activation-time result separately and queries current evidence again.

| Latched phase | Order | V5 at BASE+3300 | V6 at BASE+3300 |
| --- | --- | --- | --- |
| Context | Formation then NOT | TRIGGER_QUALIFIED, one intent | INVALIDATED, zero intents |
| Context | NOT then formation | TRIGGER_QUALIFIED, one intent | INVALIDATED, zero intents |
| Setup | Formation then NOT | TRIGGER_QUALIFIED, one intent | INVALIDATED, zero intents |
| Setup | NOT then formation | TRIGGER_QUALIFIED, one intent | INVALIDATED, zero intents |

[Literal API refusal](evidence/TF-I10A1-negated-current/literal-request.txt) · [Reviewed baseline trace](evidence/TF-I10A1-negated-current/baseline.txt) · [Corrected trace](evidence/TF-I10A1-negated-current/corrected.txt)

## Decision and history behavior

- Latched CURRENT leaves under effective negation are queried at each decision cutoff against their declared dependency, source, family binding and original phase lower bound. Their latest result feeds the enclosing three-valued group. A matching revision is attached to the result. Insufficient coverage or eligibility remains UNKNOWN. Positive CURRENT predicates retain exact-family maintenance.
- Activation outcomes are immutable and visible alongside the latest check in Strategy Builder and Strategy Lab. Historical leaves stay latched. Continuous-context evaluation and cross-phase activation anchors retain their prior behavior.
- Tests establish invalidation when the prohibited state appears, qualification on a separate formation while it remains absent under complete coverage, and no intent with either a straddling producer observation or a missing due candle. The existing valid ANY alternative still passes.
- Normal evaluation, historical single-attempt evaluation, and a resumed FVG successor run all agree at BASE+3300 on the state, zero intents, exact rule and supporting evidence, and original activation outcome. The successor tests interrupt at BASE+3000, check that the incomplete run publishes no decisions, then resume and compare against uninterrupted processing.
- Evaluator v6 gives corrected runs new identities. Saved v1 through v5 results remain readable. Earlier incomplete evaluator runs require recalculation and cannot resume through v6. The historical run and checkpoint formats remain v2.

## Supported producer and rearm scope

| Policy | Scope |
| --- | --- |
| SINGLE_ATTEMPT | Existing admitted producer combinations, including the approved CRT/SMT archive. |
| NEW_CONTEXT_FAMILY | One primary EURUSD M5 FVG producer, an explicit mandatory positive ACTIVE context-family alias, at most 160 as-of cutoffs, and the existing worker and storage budgets. |
| CRT/SMT successors | Rejected at validation and enqueue. The independent three-opportunity acceptance fixture remains pending. |

No performance claim was measured for this correction. Production recorded-trading readiness remains BLOCKED. Historical qualified intents keep AWAITING_EXECUTION_VALIDATION. TF-I10A.2 remains pending.

## Verification

- The clean v5 API run recorded four expected regression failures. The same four canonical cases passed on v6. [Paired trace comparison](evidence/TF-I10A1-negated-current/trace-comparison.json)
- Focused pytest passed 102 tests. Full pytest passed 711 tests. SHA-256 inventories matched for all 44 workspace archive files. The explicit legacy test compared all 22 approved numerical fingerprints. Python compilation passed. The baseline and corrected pytest logs record the existing Starlette TestClient deprecation warning. [Focused](evidence/TF-I10A1-negated-current/focused.txt) · [Backend and archive isolation](evidence/TF-I10A1-negated-current/backend.txt) · [Legacy](evidence/TF-I10A1-negated-current/legacy.txt)
- Vitest passed eight tests and the production build passed. The first production build failed on invalid UTF-8 in the new UI text. The browser run started with that source was interrupted. After fixing the source encoding, the clean Chromium suite passed 71 tests, the checkpoint series passed 20 consecutive repetitions, and both the delayed-response stress test and the cold Data Library check passed. All completed browser runs used zero retries. [First build](evidence/TF-I10A1-negated-current/build-first.txt) · [Interrupted browser](evidence/TF-I10A1-negated-current/browser-interrupted.txt) · [Build](evidence/TF-I10A1-negated-current/build.txt) · [Vitest](evidence/TF-I10A1-negated-current/vitest.txt) · [Chromium](evidence/TF-I10A1-negated-current/browser.txt) · [Checkpoint](evidence/TF-I10A1-negated-current/checkpoint.txt) · [Stress](evidence/TF-I10A1-negated-current/stress.txt) · [Cold](evidence/TF-I10A1-negated-current/cold.txt)
- All eight approved Dukascopy single-attempt cases reproduced EXPIRED with zero intents. CRT/SMT successors remained gated for both configured intervals. The run used checksum-verified disposable copies of the approved archive. [Real session](evidence/TF-I10A1-negated-current/real-session.json)

Run the backend and the workspace integrity check with `python scripts/verify_strategy_test_isolation.py`. The real run uses `python scripts/verify_real_strategy_evaluation.py --historical`. Frontend verification used `npm run test`, `npm run build`, and `npm run e2e -- --retries=0 --reporter=line`. The checkpoint case used `--repeat-each=20` and the dedicated stress and Data Library cases used fresh isolated servers.

GitHub's push and pull-request workflows track the exact corrected head in PR #17.
