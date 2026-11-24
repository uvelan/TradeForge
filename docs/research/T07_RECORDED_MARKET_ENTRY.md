# T07 recorded-quote MARKET entry

PR #34 merged at `4b19d9cf29e1a9938ac1e7d33abb79c616c9b125`. At that revision the public Python service rejected the new keyword with `TypeError: validate_market_entries() got an unexpected keyword argument 'recorded'`.

## Evidence and boundaries

The positive control publishes a controlled historical intent from the existing CRT/SMT candle fixture dated to November 13, 2025. Both executable prices come from the original Dukascopy EURUSD 13:00 UTC BI5 hour. The fixture establishes no naturally occurring strategy opportunity on this archive. A BI5 timestamp supplies observation time. Delivery uses an explicit offline zero-delay assumption. The service assumes zero slippage, and quote size, broker latency and order acceptance remain unverified.

| Controlled intent | Qualification UTC | First eligible observation and modeled delivery UTC | Ordinal | Executable side | Expected / observed |
| --- | --- | --- | ---: | --- | --- |
| LONG | 13:55:20.000 | 13:55:21.403 | 2971 | ASK | 1.16174 / 1.16174 |
| SHORT | 13:55:20.000 | 13:55:21.403 | 2971 | BID | 1.16170 / 1.16170 |

The quote contains 116170 bid and 116174 ask in units of 0.00001. Both directions qualified at epoch millisecond 1763042120000. The test verifies the returned fill time of 1763042121403, side, ordinal, price units and original published source. The source partition and both overlapping shard orders produced the same economic fill. Reversing the shard list and retrying each request reproduced the same payload and IDs. A different physical layout retains its own fingerprint and result ID.

Supply the following recorded window to the Python service using `validate_market_entries(run_id, recorded=window)`, or save it as JSON and use `python -m research_backend.app.offline_market_entry --run RUN_ID --recorded window.json` in a workspace with that completed run and import.

```json
{
  "source": {
    "id": "713b347197c528d5d92242630e3369924152e83e7e9728079ef22a39c32170e2",
    "sha256": "ca71608096bb6ec870c2a621d761910f0d9a45d7d9ce7fe9e99569ef983e6276"
  },
  "source_sha256": "2f47bcb14e0a2c4dcd6d65eb819b85f1b5168b656d1f5f67c3dcd2240569f014",
  "partitions": [{
    "id": "713b347197c528d5d92242630e3369924152e83e7e9728079ef22a39c32170e2",
    "sha256": "ca71608096bb6ec870c2a621d761910f0d9a45d7d9ce7fe9e99569ef983e6276"
  }],
  "start": 1763042060,
  "end": 1763042210
}
```

## Coverage and replay

One completed BI5 import anchors each half-open window. T06 verifies whole Parquet files and orders the ticks. A shard selection must reproduce the contributing prefix from that import, with the same source fingerprint. A missing quote or changed price fails verification. A conflict at the winning priority produces zero fills. Later conflicting prices cannot displace an earlier certain fill. Every retry rechecks its files before comparing the immutable stored result.

Recorded coverage requires observations on both sides of qualification, within 60 seconds of each other in one open forex session. The source interval or a lone quote is insufficient. MARKET V2 chooses the first executable quote strictly after qualification through inclusive expiry. A quiet interval bracketing expiry can establish expiry under this bounded-gap policy. With an absent bracket or excessive gap the result is unresolved. Controlled BI5 tests cover a conflicting winning priority, a 60.403-second gap before entry and a five-second expiry with the next quote at six seconds.

The recorded policy has its own immutable identity, `OFFLINE_RECORDED_MARKET_ENTRY` V1. Synthetic MARKET V1/V2 records keep their existing identities. Historical intents retain `AWAITING_EXECUTION_VALIDATION`. Recorded-quote exits, P&L, and live routing are outside this increment. Public admission remains at 160 cutoffs and production recorded trading is `BLOCKED`.

## Reproduction and rollback

```powershell
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_recorded_market_entry.py research_backend/tests/test_offline_market_entry.py research_backend/tests/test_t06_layout.py
$env:TRADEFORGE_T07_REAL_BI5='storage/t06-input/13h_ticks.bi5'
.venv/Scripts/python.exe -m pytest -q -s research_backend/tests/test_recorded_market_entry.py -k layouts
Remove-Item Env:TRADEFORGE_T07_REAL_BI5
.venv/Scripts/python.exe -m pytest -q
```

The ordinary suite generates BI5 controls in an isolated database. The real run checks the original archive hash before replay. Roll back by omitting the opt-in recorded argument or reverting the T07 commit. Preserve the immutable result table and archives. No migration is required.

## Re-review of the winning conflict

At reviewed head `4906828f29e800d58fe91fc54c1fd4c301b37ee5`, a conflict prevented the list of selected quotes from returning to the verifier. The corrected service compares each ordered observation with the pinned BI5 prefix as it is read. A conflict at the winning priority can become an execution result only after every strictly earlier reference row has matched.

Each row below uses a completed controlled historical run, the same conflicting winning prices, two genuine Parquet shards, both selection orders and two identical calls per order. Counts come from a fresh SQLite connection after each call.

| Earlier observations | Reviewed service / stored rows | Corrected service / stored rows |
| --- | --- | --- |
| Exact | Ambiguous, zero fills / 1 | Ambiguous, zero fills / 1 |
| Omit the prequalification observation | Ambiguous, zero fills / 1 | Rejected for missing observation / 0 |
| Insert a lower ordinal at the winning millisecond | Ambiguous, zero fills / 1 | Rejected for extra observation / 0 |
| Change the earlier ask volume only | Ambiguous, zero fills / 1 | Rejected for source row disagreement / 0 |

The historical run, attempts, decision proofs and intent payloads matched their original stored values in all four cases. The November 13 archive again gave ordinal 2971 at 1763042121403 ms for both directions, with identical observation and modeled delivery times. Identical requests reproduced each result and fill ID.

### Identifying previously written results

The accepted record format and policy identity are unchanged. On an isolated database copy, list candidate results written with the opt-in recorded policy and a noncanonical physical selection:

```sql
SELECT id, run_id, intent_id, json_extract(payload, '$.status') AS status,
       json_extract(payload, '$.recorded_evidence') AS evidence
FROM offline_market_entry_results
WHERE json_extract(payload, '$.identity.policy.id') = 'OFFLINE_RECORDED_MARKET_ENTRY'
  AND json_extract(payload, '$.identity.policy.version') = 1
  AND (json_array_length(payload, '$.recorded_evidence.partitions') > 1 OR
       json_extract(payload, '$.recorded_evidence.partitions[0].id') <>
       json_extract(payload, '$.recorded_evidence.source.id'));
```

The old writer did not record whether it took the conflict branch. The query conservatively includes all statuses because insufficient coverage could also mask a malformed prefix. From each row's evidence, pass exactly `source`, `source_sha256`, `partitions`, `start`, and `end` to `validate_market_entries(run_id, intent_id=intent_id, recorded=window)`. A prefix omission, addition or source disagreement identifies a result unsupported by its pins. A valid replay reuses its original ID. Missing or damaged archives leave verification unresolved. Preserve original rows for audit. A rollback to the reviewed revision reintroduces the defect. Omit the opt-in recorded argument to disable this path.
