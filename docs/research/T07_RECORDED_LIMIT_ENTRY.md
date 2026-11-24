# T07 recorded fixed-price LIMIT entry

PR #35 merged at `517f8122b633f9143324f99dd7b075c7c689df3f`, containing approved head `f3f5f35762047fc101808fa2784e01ce49e508d9`. On that merge the public service rejected both published LIMIT requests with `Recorded execution supports MARKET with seconds expiry only`. The opt-in recorded policy now supports fixed absolute LIMIT entries with seconds expiry. The same entry point and pin contract documented in [recorded MARKET entry](T07_RECORDED_MARKET_ENTRY.md) apply.

## Pinned November 13, 2025 EURUSD example

These are controlled historical strategy intents paired with independently recorded executable prices. BI5 provides the observation clock. Delivery uses a modeled delay of zero milliseconds. The qualification for both directions was 14:55:20.000 UTC, with 90 seconds to expiry. The pin covers [14:54:20, 14:56:51) UTC. The first eligible quote, ordinal 4918 at 14:55:20.086, showed 1.16271 BID and 1.16275 ASK and missed both limits.

| Direction | Fixed limit | First touch UTC | Original ordinal | Expected / observed executable price | Improvement |
| --- | ---: | --- | ---: | --- | ---: |
| LONG | 1.16274 | 14:55:37.210 | 4932 | 1.16274 / 1.16274 ASK | 0 points |
| SHORT | 1.16272 | 14:55:21.924 | 4920 | 1.16273 / 1.16273 BID | 1 point (0.00001) |

- Original 14h BI5 SHA-256: `9d40aa3147d93e6daa2218933877adb9471dc02ff08b19fc0d272f1eb781688f`.
- Tick partition ID: `8c201493b65ef8c53da08c0ba53cbb54b6d435d0ff33a38365e13fdbd42f5a02`.
- Parquet SHA-256: `6944703b23da14f4727177baab237abc04c36cb502a2f95db97e4493dd11e5c8`.

The test compares the complete order and economic fill (including the winning quote) across the source partition, overlapping shards and reversed shard selection. Each identical request returns the complete original response. Each isolated direction finishes with two result rows, one for each distinct physical layout. Reversing a selection adds zero rows.

## Decision evidence

The reader compares the selected prefix with the completed BI5 import, including the eligible misses. It stops comparing prices after a certain fill. In the absence of a touch, a later observation can bracket the deadline. The 60-second maximum gap applies between consecutive observations, from the prequalification anchor through the touch or expiry. Coverage ends at the first gap. Tied shard prices can establish ambiguity only when the original observation is present and its preceding sequence has matched. The LIMIT simulator applies its existing executable-side rule to all variants at that priority.

| Generated BI5 service control | Observed outcome | Rows after both orders and identical retries |
| --- | --- | ---: |
| Earlier miss omitted, inserted or altered | Rejected | 0 per case |
| Earlier miss altered and winning quote conflicted | Rejected | 0 |
| Tied observation changes source metadata | Rejected | 0 |
| Touch vs miss at first possible fill, with either touch or miss in the original source | AMBIGUOUS_QUOTE_ORDER, zero fills | 1 per case |
| Conflict after certain fill | Original fill | 1 |
| Touch exactly at expiry | Fill at expiry | 1 |
| First touch one millisecond after a covered deadline | VERIFIED_EXPIRED, zero fills | 1 |
| Gap after a miss and before the prospective fill | INSUFFICIENT_QUOTE_COVERAGE, zero fills | 1 |

The recorded LIMIT policy has its own identity. Two regression controls compare exact stored payload hashes, result IDs and fill IDs with values collected using the merged writers for recorded MARKET and synthetic LIMIT. Tests also compare the historical source ledger before and after execution.

## Reproduce

```powershell
.venv/Scripts/python.exe -m pytest -q research_backend/tests/test_recorded_limit_entry.py research_backend/tests/test_recorded_market_entry.py research_backend/tests/test_offline_limit_entry.py research_backend/tests/test_offline_market_entry.py
$env:TRADEFORGE_T07_LIMIT_BI5='storage/t06-input/14h_ticks.bi5'
.venv/Scripts/python.exe -m pytest -q -s research_backend/tests/test_recorded_limit_entry.py -k first_touch
Remove-Item Env:TRADEFORGE_T07_LIMIT_BI5
.venv/Scripts/python.exe -m pytest -q
```

Rollback by reverting the recorded LIMIT commit or discontinuing recorded LIMIT requests. Retain immutable results for audit. No migration is required. Historical intents retain `AWAITING_EXECUTION_VALIDATION`. Public admission stays at 160 cutoffs and production recorded trading stays `BLOCKED`. Quote prices alone cannot establish broker queue position, executable size or actual latency.
