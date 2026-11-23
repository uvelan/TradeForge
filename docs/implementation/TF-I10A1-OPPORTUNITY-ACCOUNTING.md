# Historical successor opportunity accounting

PR #18 merged at `54b8a372515687dfc9142295fdce3ae416a67669`. The follow-up branches from the verified `origin/main` at that commit.

## Expected CRT/SMT acceptance records

Offsets are seconds after 2020-01-02 10:00 UTC. These expectations come from the canonical fixture timeline before the accounting comparison.

| Root | First qualifying availability | Expected disposition | Attempt outcome | Intents |
| --- | ---: | --- | --- | ---: |
| A | +3600 | Bound at +3600 | Expired at +7200 | 0 |
| B | +7200 | Suppressed at the terminal boundary at +7200 | No binding | 0 |
| C | +10800 | Bound at +10800 | Qualified at +14120 | 1 |
| D | +14400 | Bound at +14400 | Invalidated at +15000 | 0 |

A's +7500 reclaim fails the designated ACTIVE predicate. C's +14119 SMT candidate cannot create a CRT context opportunity or an intent. The complete output should contain four families, three bound attempts, one boundary suppression and one qualified intent.

## Observation contract

An opportunity is a canonical family observed through the designated mandatory positive context rule in a run. The identity hashes the run ID, rule ID and canonical family ID. The first recorded row retains the exact as-of reference and inspection cutoff. The reference includes the modeled availability timestamp. Initial inspections use the rule's normal warmup and age window, so evidence available during warmup can qualify at the first evaluation inspection. No inspections or counts occur before evaluation starts. After a terminal attempt, the next inspection admits evidence available strictly after that terminal cutoff. The terminal inspection itself records qualified opportunities whose availability equals the excluded boundary.

The observation probe reads the same verified prefix at each existing scheduler cutoff. It applies the shared selector's predicate, projection, age, dependency, evidence attestation and candidate-limit checks. A predicate-failing revision never creates a row. A previously bound family retains one opportunity row if its evidence is seen again. The internal FVG spent-membership control still proves that an otherwise eligible later revision cannot rearm a spent family. That test-only predicate is outside public rearm admission.

The decision evaluator remains authoritative. If the decision binds, accounting uses that winner and records its exact binding proof. Other eligible families at that selection cutoff are suppressed under selection policy. The selector keeps its configured earliest/latest ordering and tie-breakers. Other positive observations can be suppressed by the terminal boundary, an attempt already in progress, or a known unsatisfied context. Where no disposition can be established, accounting records a qualifying family as unresolved or records an unresolved inspection when coverage or evidence does not establish a family. Such an inspection is not a family count. First observation is retained when an unresolved family is later classified or a family later binds. The first established non-binding reason is retained unless the family subsequently binds.

## Publication and compatibility

The run detail response exposes `opportunity_accounting` with status, total, disposition counts, reason counts and unresolved inspection count. Strategy Lab displays this summary. `GET /api/historical-strategy-runs/v1/{run_id}/opportunities?after=0&limit=30` returns rows in stable first-observation order. Pass the last row's `cursor` as `after` for the next page. Each row identifies the family, rule, reason, inspection cutoff and exact evidence reference. It also preserves the first observation, and provides the attempt ID and binding reference for bound rows.

Accounting events are append-only. The checkpoint carries the family cursor and disposition state needed for resume, plus the digest of the batch bound to that checkpoint. The fenced worker commits decisions, intents, opportunity events and the checkpoint together. Failed or interrupted runs expose neither accounting rows nor accounting totals. Completed legacy records return `NOT_COLLECTED`; completed instrumented runs with zero rows return `COLLECTED` with zero counts. Previously queued or interrupted legacy runs keep their original publication behavior. Re-enqueueing an identical legacy run keeps its existing immutable identity and does not backfill accounting.

The producer gate, evaluator version and 160-cutoff schedule are unchanged. Accounting shares the existing worker, checkpoint and ledger resource ceilings. The continuation does not cover full-session optimization, TF-I10A.2 or execution validation.

## Observed acceptance and recovery

| Root | Observed availability and disposition | Observed attempt result |
| --- | --- | --- |
| A | +3600, `BOUND / ATTEMPT_BOUND` | Expired at +7200, zero intents |
| B | +7200, `SUPPRESSED / TERMINAL_BOUNDARY` | No attempt or intent |
| C | +10800, `BOUND / ATTEMPT_BOUND` | One intent at +14120 |
| D | +14400, `BOUND / ATTEMPT_BOUND` | Invalidated at +15000, zero intents |

The complete fixture reconciled four distinct families, three attempts, one boundary suppression, zero unresolved families, and one intent. No extra row appeared for A's reclaim, C's pending SMT candidate, or repeated inspection of B. The admitted FVG fixture also produced five rows, three bound and two boundary-suppressed. Its warmup competition test retained the evaluator's winner while recording other eligible families.

The chunk sizes 1, 7, 17 and 93 produced identical opportunity events, decisions, intents, and proof references. An uninstrumented control produced the same decisions and intents. Both resumed SQLite runs matched the uninterrupted result after interruptions at +7200 and +14100. Replaying the committed B batch left one B row. A batch deliberately rejected after an opportunity insertion rolled back the opportunity, decision and checkpoint together. Public reads remained empty until completion, after which both cursor pages reconstructed the complete output.

## Verification and rollback

[Commands and captured output](evidence/TF-I10A1-opportunities/) document the local checks. Hosted workflow results for the submitted head are linked in the pull request.

The local backend pass reported 722 tests and verified all 44 inventoried archive files were unchanged. That invocation started before the additional warmup competition regression was added. The final targeted suite included the warmup case and passed 67 tests. TypeScript compilation, production build and all eight Vitest cases passed. The initial concurrent Chromium check passed 70 cases and failed one simulator chart assertion while the page showed “Loading datasets…”. The isolated simulator check passed with zero retries. The full rerun passed all 71 Chromium tests with zero retries.

For rollback, drain the accounting-enabled queue or leave interrupted accounting-enabled runs paused. Revert implementation commit `3afbe66eb3f1c6bb4e8353d89e435c1765383c76` through GitHub and retain the additive accounting table and completed evidence for audit. Resume interrupted accounting-enabled runs with the accounting-capable version. Historical intents retain `AWAITING_EXECUTION_VALIDATION` and production recorded trading remains `BLOCKED`.

## Follow-up on uncertainty masked by a false sibling

The correction starts from the reviewed head `6d57bbec049e3c257467bc8821033c9a3628ac7b`. Both baseline cases were admitted as bounded FVG `NEW_CONTEXT_FAMILY` runs using `candidate_limit=1` and the same false mandatory sibling. Each run had exactly one scheduler inspection.

| Case | Designated ACTIVE rule | ALL context | Baseline unresolved inspections | Corrected summary |
| --- | --- | --- | ---: | --- |
| Two eligible ACTIVE families at +3300 | `UNKNOWN / CANDIDATE_LIMIT_EXCEEDED` | `NOT_SATISFIED` | 0 | 1, with `CANDIDATE_LIMIT_EXCEEDED: 1` |
| Complete coverage with zero ACTIVE families at +2700 | `NOT_SATISFIED / NO_MATCH_COMPLETE_COVERAGE` | `NOT_SATISFIED` | 0 | 0, with empty reason counts |

Both API responses published zero opportunity rows, suppressions, attempts and intents. The baseline overflow assertion failed on 0 versus 1. The corrected accounting consults the designated rule before falling back to the combined context outcome. An overflow contributes one inspection with its specific reason, irrespective of whether a sibling settles the ALL context. The selector yields no partial candidate list. Existing family rows and the decision path are preserved. Older collected checkpoints that lack reasons retain their counts under `NOT_RECORDED`. Completed legacy runs still report `NOT_COLLECTED`.

Recovery verified that the duplicate delivery of the +3300 checkpoint kept one unresolved inspection private. Resume through +3600 matched the uninterrupted two-inspection total and its reason breakdown. The previously established +3000 family retained its original proof. Completed responses distinguish true zero from overflow.

The baseline replay pinned strategy/version IDs. Both single-inspection decision IDs matched exactly before and after. These single-inspection cases emitted zero intents. The CRT and FVG instrumented/uninstrumented ledger controls preserved decisions, intent IDs and proofs. CRT/SMT still produced four families, three bound attempts, one terminal-boundary suppression and one intent. The existing FVG competition, chunk-size, pagination, and rollback cases passed.

Local verification passed three API baseline-correction/recovery cases, 70 focused regressions, TypeScript compilation and production build, and eight Vitest tests. [Baseline failure](evidence/TF-I10A1-opportunity-uncertainty/baseline.txt) · [Corrected API output](evidence/TF-I10A1-opportunity-uncertainty/corrected.txt) · [Exact commands](evidence/TF-I10A1-opportunity-uncertainty/commands.md)
