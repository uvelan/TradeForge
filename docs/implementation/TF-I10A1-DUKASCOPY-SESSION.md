# November 13 Dukascopy offline successor validation

The branch starts from verified main after PR #20 merged at `1aaa195b066efda67c4b16055e41a9693e23d78d`.

## Configuration pinned before outcome inspection

Use `DUKASCOPY_BID_2026_11_13_V1`, the original EURUSD and GBPUSD M5 BID partitions and feed, and their `forex_weekly` calendar. Warmup begins November 13, 2026 at 00:00 UTC, evaluation begins at 02:00 UTC, and the cutoff is 22:00 UTC. Reuse the reviewed SHORT CRT/SMT producer and rule settings, with modeled 0s primary and 20s comparison delays. The calendar is the only strategy configuration change from the reviewed synthetic fixture. The separate offline run method and identity are pinned in the preflight artifact.

The original eight real controls retain their original configurations. The real research run does not tune strategy rules based on outcomes. The provider catalog and files are read-only, and all evaluator writes occur in an isolated workspace.

## Coverage and provenance

Catalog fingerprint `cdd3678ceaf2d950af43dd462da6aaf6270702fb68f16966e9a45eb859814524`. The BID feed is `dukascopy-web-m1-v1` for both saved M5 lanes.

| Lane | Partition ID | SHA-256 |
| --- | --- | --- |
| EURUSD M5 | `ca01e9ac45346d7c9fdfdf4263085831a07735bc16158901c9a14187f77aaf4e` | `fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d` |
| GBPUSD M5 | `1cc6acc1b358b3b043438badc69f72413827a8cd5dad379de8e41dd9aaaecfd0` | `aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02` |

All 264 scheduled slots per lane were verified. The approved Parquet schema uses the loader's default closed status. Warmup had 24 eligible primary and 23 comparison candles. At 22:00, 264 primary and 263 comparison candles were eligible. EURUSD's last eligible candle opened at 21:55 and arrived at 22:00. GBPUSD's last eligible candle opened at 21:50 and arrived at 21:55:20. Its 21:55 candle arrived at 22:00:20 and was excluded. The coverage intervals end at the 17:00 New York Friday close. Checksums of the original provider files were verified again after each isolated case.

The pinned research identity is `HR-a02d7f7b896d82d8e874e402f398bec9e8e5b0e128966fe8690e8e276704756f` under `CRT_SMT_NOV13_OFFLINE_V1`. [Complete pre-outcome pin](evidence/TF-I10A1-dukascopy-nov13/pin.json)

## Full-session comparison and recovery

The reference derives arrivals from the pinned calendar and each lane's delay. It combines them with canonical publications, uncertainty edges and age limits, plus setup and expiry timers. Terminal cutoff and spent families carry across all 20 evaluation hours. The cache and reference shared the same immutable run identity.

The ordered cutoff lists matched at all 556 positions. Full trace fingerprints, decisions, attempt IDs, intents, clock times, proof references, and accounting records matched exactly. The comparison records no first divergence. There were 21 opportunities, 14 bound attempts and seven terminal-boundary suppressions. The separate inspection uncertainty count was 17, comprising 13 `CRT_REVISION_UNCERTAIN` and four `EVIDENCE_WINDOW_STALE`. No opportunity family had an unresolved disposition.

The first attempt bound the 01:00–02:00 UTC EURUSD CRT range. The independent Parquet reconstruction matched all 12 constituents and aggregate OHLC `[1.10531, 1.10636, 1.10519, 1.10615]`. Its availability and binding were both 02:00 UTC. Of the 14 attempts, only two reached setup. Both ended without satisfying SMT, with terminal SMT reason `SMT_ABSENCE_NOT_ESTABLISHED`. There was no qualifying confirmation to verify and no intent. Terminal reasons were seven setup expiries, six terminal CRT references, and one setup not established at the session cutoff.

Recovery interrupted at the first terminal attempt, 03:00 UTC, and submitted the identical committed batch again. Resume changed chunk size from 1 to 23. All 556 traces and published decisions, proofs, and opportunity records matched. The incomplete decisions, opportunities, intents and checkpoint stayed private. Public validation and enqueue both returned HTTP 422 and created no run. The real case fails the public calendar gate, and an independent call to the existing cutoff guard also rejected the frozen 556-cutoff schedule under the 160 limit.

## Worker-budget measurements

The paths ran in separate Python processes on the same Windows workspace. Cold and warm are each one invocation. The OS file cache was not flushed. Peak is process working-set high water, and the warm figure includes memory already touched by the cold run. Elapsed times include horizon verification and trace hashing. The cold invocations also record field digests for mismatch diagnosis.

| Path | State | Elapsed | Peak worker memory | Verified decodes | CRT / SMT calls | Cutoffs | Attempts |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fresh | Cold | 35.18 s | 159.06 MiB | 1,116 | 558 / 558 | 556 | 14 |
| Fresh | Warm | 35.46 s | 159.98 MiB | 1,116 | 558 / 558 | 556 | 14 |
| Cached | Cold | 32.68 s | 131.47 MiB | 6 | 558 / 558 | 556 | 14 |
| Cached | Warm | 32.40 s | 134.46 MiB | 6 | 558 / 558 | 556 | 14 |

The cached measurements completed below 150 seconds and 768 MiB. The counts include four horizon decodes and two cache initialization decodes. The isolated research ceiling is 800 cutoffs.

## Verification and rollback

- **Archive coverage** passed for the pinned two M5 partitions and the specified window.
- **Full-session equivalence** passed, including recovery and exact proofs.
- **Worker-budget fit** passed on both measured cached runs.

All eight original real single-attempt controls completed as expired with zero intents. The synthetic four-path cache-causality regression retained 77 equal pre-arrival inspections and its first expected divergence at +14120. All 57 focused tests passed. [Exact commands](evidence/TF-I10A1-dukascopy-nov13/commands.md) · [Comparison, hashes and exact references](evidence/TF-I10A1-dukascopy-nov13/comparison.json) · [Original eight controls](evidence/TF-I10A1-dukascopy-nov13/controls.json)

Rollback by reverting implementation commit `de4548473753a5bbcf8eae7339d7f5f85e536d55`. No migration is required. Public admission remains capped at 160 cutoffs. TF-I10A.2 remains pending, historical intents retain `AWAITING_EXECUTION_VALIDATION`, and production recorded trading remains `BLOCKED`.
