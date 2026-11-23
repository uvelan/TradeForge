# TF-I10A.2 offline fixed-price STOP entry validation

PR #23 merged at `78911aaa6d8817830a27249834ad1d6e30cac21f` from reviewed head `3d49decaa4f4db2297ff58b32b011552d9a2e8d1`.

## Policy pinned before implementation

`OFFLINE_FIXED_PRICE_STOP_ENTRY` version 1 models a synthetic stop-market entry. It uses the published `FIXED_ABSOLUTE` stop, its declared basis, the instrument point scale and checksum-pinned synthetic executable bid/ask. Convert the serialized decimal threshold exactly to integer points. Reject an off-grid threshold as `UNSUPPORTED_STOP_PRICE_GRID` before creating a simulated order. The declared basis is retained separately from the trigger side. LONG triggers on ask ≥ stop. SHORT triggers on bid ≤ stop. The triggering quote supplies both the simulated executable fill price and delivery time. A gap through the stop fills at that quote, including adverse price movement.

Use the reviewed eligibility window: both observation and delivery are strictly after qualification, with delivery at expiry allowed. Select the first possible trigger by delivery, observation, then source sequence. Require continuous delivery coverage through that priority. Conflicting rows at the first triggering priority are ambiguous even when one misses the stop. Conflicts after a certain trigger cannot change its fill. If no eligible quote triggers, verified expiry requires coverage through the deadline.

## Fixture expectations pinned before replay

Qualification is `1577973320000` UTC milliseconds, and expiry is `1577973620000`. The EURUSD stop `1.10310` is 110310 points.

| Tape or scenario | Expected quote, price and delivery |
| --- | --- |
| LONG ask 1.10309, then ask 1.10310 | Sequence 5, ASK 1.10310 at qualification +2 ms |
| LONG ask 1.10309, then ask 1.10314 with bid 1.10308 | Sequence 6, ASK 1.10314 at +3 ms. Bid still below stop. |
| SHORT bid 1.10311, then bid 1.10310 | Sequence 5, BID 1.10310 at +2 ms |
| SHORT bid 1.10311, then bid 1.10306 with ask 1.10313 | Sequence 6, BID 1.10306 at +3 ms. Ask still above stop. |
| Late prequalification and equal-qualification observations | Excluded. Sequence 5, ASK 1.10310 at +2 ms. |
| Trigger delivered at exact expiry | Sequence 7, ASK 1.10311 at `1577973620000` ms |
| Sealed window without an eligible trigger | `VERIFIED_EXPIRED`, zero fills |
| One-millisecond gap before apparent trigger | `INSUFFICIENT_QUOTE_COVERAGE`, zero fills |
| Delivery priority wins despite earlier observation | Sequence 5, ASK 1.10310 at +2 ms |
| Equal delivery, earlier observation | Sequence 6, ASK 1.10311 at +2 ms |
| Equal delivery and observation, lower sequence | Sequence 5, ASK 1.10310 at +2 ms |
| Trigger/miss conflict before definite trigger, both row orders | `AMBIGUOUS_QUOTE_ORDER`, zero fills |
| Same conflict after definite trigger, both row orders | Sequence 5, ASK 1.10310 at +2 ms |
| Off-grid stop 1.103101 | `UNSUPPORTED_STOP_PRICE_GRID`, zero orders and fills |

The merged simulator is expected to reject the pinned LONG STOP with `UNSUPPORTED_ENTRY_TYPE`. The same completed intent and tape are used for the corrected replay. The pinned MARKET V2 and LIMIT V1 results must preserve the complete serialized payload and result and fill IDs.

## Observed service results

All fixture expectations above held in the 18 passing focused checks. The November 13 control reproduced 14 attempts, zero intents, zero orders and zero fills. Its approved source-ledger and archive fingerprints matched. The LONG and SHORT exact touches both filled at 1.10310. The adverse gaps filled at the triggering ask of 1.10314 and bid of 1.10306, respectively. The straddling spread makes use of the wrong quote side fail. The sealed miss produced verified expiry. The coverage gap produced insufficient coverage with zero fills. Moving the identical trigger/miss pair after the first certain trigger preserved its fill in both input orders.

The merged baseline captured the published STOP as `UNSUPPORTED_ENTRY_TYPE` with no order or fill. On tape SHA-256 `8edf56182da93496647b6be97be3d5e23e436ce3372992a0837a333e722f8537`, STOP V1 replayed that same intent and filled on sequence 5 at ask 1.10310. It preserved the old unsupported row byte-for-byte. An identical request returned the same new result and fill IDs and created no extra row. MARKET V2 and LIMIT V1 kept their prior result IDs, fill IDs and full serialized payloads. Source runs, attempts, decisions, intents and embedded proofs were byte-identical before and after replay.

[Merged baseline payloads and tape](evidence/TF-I10A2-stop-entry/merged-baseline.json) · [Observed results, tape fingerprints and triggering quote references](evidence/TF-I10A2-stop-entry/focused.txt)

## Reproduction

Capture the baseline on `78911aaa6d8817830a27249834ad1d6e30cac21f` with the updated `capture_offline_entry_baseline.py` fixture from this PR. The capture was run on that simulator before it was edited. The negative control needs the locally approved Dukascopy archive. The regular backend suite uses synthetic execution tapes.

```powershell
.venv/Scripts/python.exe -m pytest -q -s --tb=short research_backend/tests/capture_offline_entry_baseline.py::test_capture_stop_baseline
.venv/Scripts/python.exe -m pytest -q -s --tb=short research_backend/tests/test_offline_stop_entry.py research_backend/tests/test_offline_limit_entry.py research_backend/tests/test_offline_market_entry.py research_backend/tests/benchmark_dukascopy_nov13.py::test_nov13_market_entry_negative_control
.venv/Scripts/python.exe -m pytest -q
git diff --check
```

The prior LIMIT capture command and its pre-LIMIT revision are in the [LIMIT report](TF-I10A2-OFFLINE-FIXED-PRICE-LIMIT.md#reproduction). The isolated recapture used the corrected command and reproduced the original JSON bytes.

## Scope and rollback

Revert this PR as a unit to restore the prior unsupported STOP outcome. Retain the additive SQLite outcome table and its immutable rows. The change covers offline entry price and delivery time with synthetic quotes. Public admission stays at 160 cutoffs. Historical intents retain `AWAITING_EXECUTION_VALIDATION`. Production recorded trading remains `BLOCKED`.
