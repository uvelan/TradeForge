# TF-I10A.2 offline first-exit validation

PR #24 merged at `57fb880f4f91211f21ee8b29bf9c14c3a9872574` from reviewed head `a46e0e63f256a3f123142bcdb3062001fb00140f`.

## Policy pinned before implementation

The exit service takes a completed historical run, its persisted entry result ID, the same checksum-pinned synthetic quote tape, and an inclusive UTC millisecond exit cutoff. It reconciles the published intent and frozen management policy and verifies the immutable entry result against the pinned entry simulator. Exit simulation never creates an entry. An entry with no fill yields zero exits. The main historical ledger and the entry result are kept as audit records.

Only a fixed-pip initial stop and a reward-multiple target are supported. Break-even, trailing, partial and time exits return explicit unsupported outcomes. The stop distance in points is the pip distance times the instrument pip size times its point scale. Compute the reward distance from this same distance with exact rational arithmetic. Reject fractional or nonpositive price levels outside the synthetic quote grid. Anchor both thresholds to the actual fill and retain them independently of the executable exit price.

Protection activates on the entry fill quote. If its closing side already breaches the stop, exit immediately on that same quote at delivery time. After activation, accept later quote priorities, including later sequences in the same millisecond. A distinct quote observed before entry delivery is stale for exits, even if delivered later. The exact entry quote remains eligible when it was observed earlier and delivered at fill activation. LONG closes on bid and SHORT on ask. The first stop or target touch wins at its executable quote, including gaps. Conflicting rows at the first possible exit priority are ambiguous, even when one is a miss. A conflict after an established exit cannot replace it. Evaluate coverage from entry delivery through the prospective exit or cutoff, inclusive. A covered horizon without a touch is still open. An incomplete horizon is unresolved. Entry-order expiry only controls entry.

## Expectations pinned before fixtures

With ten pips of risk and a two-R target, a LONG STOP filled at ASK 1.10314 has stop 1.10214 and target 1.10514. A SHORT STOP filled at BID 1.10306 has stop 1.10406 and target 1.10106. Both request 1.10310. The entry is delivered at qualification +2 ms. In the basic cases, a spread-side miss at +3 ms is followed by the relevant executable touch at +4 ms. LONG stop gap BID 1.10210 exits at 1.10210. SHORT exact stop ASK 1.10406 exits at 1.10406. The target cases fill exactly on BID 1.10514 or ASK 1.10106. A late opposing touch cannot add a second exit.

The wide-spread LONG entry with ask 1.10314 and bid 1.10210 exits immediately at BID 1.10210. A later sequence within the same delivery and observation millisecond can trigger. A distinct quote observed before entry delivery cannot. At a shared priority where bid 1.10210 indicates stop and bid 1.10514 indicates target, an earlier conflict is ambiguous in both row orders. Moving the identical conflict after a certain exit preserves that exit. One missing millisecond before a prospective touch produces insufficient coverage. A sealed no-touch tape past entry-order expiry leaves the position open at cutoff.

## Observed first exits

| Fixture | Anchored stop / target | First executable outcome |
| --- | --- | --- |
| LONG stop first | 1.10214 / 1.10514 | Stop, BID 1.10210, sequence 7 at +4 ms |
| LONG target first | 1.10214 / 1.10514 | Target, BID 1.10514, sequence 7 at +4 ms |
| SHORT stop first | 1.10406 / 1.10106 | Stop, ASK 1.10406, sequence 7 at +4 ms |
| SHORT target first | 1.10406 / 1.10106 | Target, ASK 1.10106, sequence 7 at +4 ms |
| Wide spread at LONG entry | 1.10214 / 1.10514 | Stop on the entry quote, BID 1.10210, sequence 5 at +2 ms |
| Later sequence at the entry millisecond | 1.10214 / 1.10514 | Stop on sequence 6 at +2 ms |
| Observation older than entry delivery | 1.10214 / 1.10514 | Stale stop ignored. Target at +4 ms. |
| Stop/target conflict at the first exit priority | 1.10214 / 1.10514 | `AMBIGUOUS_QUOTE_ORDER`, zero exits in forward and reverse order |
| Same conflict after a certain exit | 1.10214 / 1.10514 | Earlier target on sequence 6 at +3 ms survives in both orders |
| One missing millisecond | 1.10214 / 1.10514 | `INSUFFICIENT_QUOTE_COVERAGE`, zero exits |
| Sealed horizon without a touch | 1.10214 / 1.10514 | `OPEN_AT_CUTOFF`, including beyond entry expiry |
| Exit delivered exactly at cutoff after entry expiry | 1.10214 / 1.10514 | Open one millisecond earlier. Target on the cutoff. |

The four unsupported management extensions and fractional stop or target levels produce explicit zero-exit outcomes. Expired, ambiguous, unsupported and uncovered entry results produce no exit records. Cross-run entry results, altered fill evidence, a different tape and a cutoff before entry were rejected.

Exit records use a separate immutable table. Their IDs bind the entry result and fill, frozen management policy, tape fingerprint, cutoff and exit policy version. An identical replay returns the same result and exit IDs and one stored row. A different cutoff can have a different evaluation result without changing the fill. Extending the cutoff after a known exit preserves the original exit event ID and quote. The regression verified the source and entry bytes around the exit call, including pinned MARKET V2, LIMIT V1 and STOP V1 payloads captured on merged main. The verifier also recognizes MARKET V1 fills with their original tape-wide sequence rule. Other unrecognized filled policy versions require a supported replay.

[Raw expected and observed results, fingerprints and winning quotes](evidence/TF-I10A2-first-exit/focused.txt) · [Pinned entry payloads from merged main](evidence/TF-I10A2-first-exit/merged-entry-baseline.json)

## Exact verification commands

The baseline capture used an isolated `git archive` of `57fb880f4f91211f21ee8b29bf9c14c3a9872574` with only the updated capture test copied into it. Run the first command on that isolated baseline using the project's Python environment. The focused suite passed 32 tests. The November 13 control used the approved local Dukascopy archive and returned 14 attempts, zero intents, zero orders, zero fills and zero exits. The historical ledger and original archive checksums matched.

```powershell
python -m pytest -q -s --tb=short research_backend/tests/capture_offline_entry_baseline.py::test_capture_first_exit_entry_baseline
.venv/Scripts/python.exe -m pytest -q -s --tb=short research_backend/tests/test_offline_first_exit.py research_backend/tests/test_offline_stop_entry.py research_backend/tests/test_offline_limit_entry.py research_backend/tests/test_offline_market_entry.py research_backend/tests/benchmark_dukascopy_nov13.py::test_nov13_market_entry_negative_control
.venv/Scripts/python.exe -m pytest -q
git diff --check
```

For a filled entry, pass its persisted result ID and the same pinned synthetic quote tape.

```powershell
.venv/Scripts/python.exe -m research_backend.app.offline_first_exit --run <run-id> --entry-result <entry-result-id> --cutoff-ms <utc-ms> --tape <tape.json> --sha256 <canonical-tape-sha256>
```

## Scope and rollback

This validates one simulated exit reason, price and time. Position sizing, account P&L, paper-account publication and broker routing are deferred. Public admission stays at 160 cutoffs. Historical intents retain `AWAITING_EXECUTION_VALIDATION`. Production recorded trading remains `BLOCKED`.

After merge, revert this PR as a unit to disable first-exit validation. Retain the additive immutable exit table and its rows for audit. Entry result and historical tables require no rollback migration.
