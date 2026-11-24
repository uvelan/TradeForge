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
