# TF-I10A.2 offline MARKET entry validation

PR #21 merged at `acfa2e965370d69629776b9317c3852f00c5afb3`. This increment consumes the immutable historical ledger through an offline service and CLI.

## Execution contract pinned before fixture execution

| Decision | Pinned policy |
| --- | --- |
| Offer side | The existing executable bid/ask convention in the tick replay selects bid for SHORT and ask for LONG. The supplied executable spread is already in the quote. V1 requires the source strategy's pinned slippage to be zero. |
| Event and availability | UTC epoch milliseconds. The intent's `AFTER_CAUSAL_AVAILABILITY` rule requires both quote observation and delivery to be strictly after qualification. A quote observed before or at qualification stays ineligible even if delivered later. The result's fill clock is delivery time. |
| Order and ties | Select the first observable quote by delivery, observation time, then the unique source sequence of a single pinned tape. Duplicate source sequence makes order ambiguous. |
| Expiry | Add the immutable intent's seconds expiry to qualification. The upper boundary is inclusive, following the evaluator's expiry rule that expires only after the deadline. Both observation and delivery must be within the window. |
| Precision | Decode integer points using the existing symbol point scale. Store the fill as an exact decimal string with its units and scale, without float conversion. |
| Coverage | A verified synthetic tape explicitly attests complete delivery intervals. Before selecting a quote, require coverage from the first eligible millisecond through delivery. Without an eligible quote, require coverage through expiry to record verified expiry. Gaps and ambiguous sequences produce explicit unresolved reasons. |
| Scope | Accept a checksum-pinned `SYNTHETIC_EXECUTABLE_QUOTES_V1` tape. Existing archived ticks have observation order and bid/ask but no equivalent delivery receipt or complete-window attestation. The service rejects other entry types explicitly. |

### Expected fixture

The reviewed synthetic SHORT C intent qualifies at +14120 seconds. Three quotes have distinct prices at one millisecond before, exactly at, and one millisecond after qualification. The expected fill uses the last quote's bid of `1.10301` at qualification plus one millisecond. A LONG control with the same tape uses its ask of `1.10314`. A quote observed before qualification and delivered later must never win. The fixture will also distinguish inclusive expiry, sealed empty expiry, incomplete coverage, and ambiguous ties.

The completed November 13 control has 14 attempts and zero qualified intents, so it must return zero simulated orders and fills at the same service entry point.

## Expected versus observed

All timestamps in the table are epoch milliseconds or offsets from qualification. The synthetic intent and result IDs vary across separately created fixture strategy versions. Within each published run, repeated identical inputs return the same result and fill IDs and leave exactly one immutable row. The raw test output records the full simulated quote, intent ID, expiry, result ID and fill ID.

| Case | Expected from the input | Observed |
| --- | --- | --- |
| Reviewed synthetic C, SHORT | Qualification `1577973320000`. Use sequence 4, observed and delivered at qualification +1 ms. BID `1.10301`. | Same timestamp, side and price. The prequalification, equal-time and late-delivered prequalification quotes were excluded. |
| Mirrored synthetic C, LONG | Same qualification and sequence 4. ASK `1.10314` with a 13-point spread. | Same timestamp, side and ask price. Exact resubmission returned the original result and fill IDs. |
| Equal observations and delivery times | Sequence 8 wins the ordered tie. A repeated source sequence makes ordering ambiguous. | `SIMULATED_FILLED` for sequence 8 and `AMBIGUOUS_QUOTE_ORDER` for the repeated sequence. |
| Delivery order | The quote observed at qualification +2 ms and delivered at +3 ms beats the quote observed at +1 ms and delivered at +5 ms. | Filled at delivery +3 ms on the expected bid. |
| Inclusive deadline | At `1577973620000`, the exact-boundary bid is eligible. Delivery one millisecond afterward is out of bounds. | The exact-boundary bid filled. Late delivery with complete coverage returned `VERIFIED_EXPIRED`. |
| Empty or incomplete coverage | An empty tape covering through expiry expires. An open final millisecond or gap before the first possible fill stays unresolved. | `VERIFIED_EXPIRED` for the complete empty window and `INSUFFICIENT_QUOTE_COVERAGE` for missing coverage. |
| Published LIMIT intent | Explicit unsupported result with zero simulated orders and fills. | `UNSUPPORTED_ENTRY_TYPE`. |
| November 13 Dukascopy | 14 attempts, zero qualified intents, zero orders and zero fills. | 14 / 0 / 0 / 0. No offline result rows and no change to the original ledger or its approved SHA-256. |

Incomplete runs, intent IDs belonging to another run, changed embedded evidence references and incorrect tape digests are rejected. The run freeze, immutable strategy version, attempt, decision ID, exact supporting events and original intent are reconciled before execution. SQLite serializes same-input writes, and the result table prohibits updates and deletes.

### November 13 audit

The opt-in negative control uses the PR #21 pinned catalogs and provider partitions in an isolated database and market directory. It reconstructs and publishes the approved completed ledger before invoking the offline service twice. It compares the public source ledger before and after simulation and rehashes the original provider files after the run.

- Approved clone run ID `HR-a02d7f7b896d82d8e874e402f398bec9e8e5b0e128966fe8690e8e276704756f`.
- Historical ledger SHA-256 `eea705c1c9a4d307460801b1864c91343ebf18221b657e69ab86f53fe49d9a7f`.
- Catalog SHA-256 `cdd3678ceaf2d950af43dd462da6aaf6270702fb68f16966e9a45eb859814524`.
- EURUSD M5 BID SHA-256 `fa2af192aa86dc41bb20df51dffe95c29bc48cdf2fe38b643f699985c6b0e09d`.
- GBPUSD M5 BID SHA-256 `aed73f09d78bb3df29d3678e0ace1f00949a673a920ed22dcd6d23cb7a173d02`.

### Reproduction

Run from the repository root on PowerShell. The private-archive regression requires the original PR #21 provider storage. The default backend suite exercises the synthetic fixture without that storage. The captured focused evidence is [focused.txt](evidence/TF-I10A2-market-entry/focused.txt).

```powershell
.venv/Scripts/python.exe -m pytest -q -s --tb=short research_backend/tests/test_offline_market_entry.py research_backend/tests/benchmark_dukascopy_nov13.py::test_nov13_market_entry_negative_control
.venv/Scripts/python.exe -m pytest -q
Push-Location frontend
npm test
npm run build
npm run e2e -- --retries=0
Pop-Location
git diff --check
```

For a published run in the configured local database, pass its run ID and an optional intent ID to the CLI. A quote tape requires the SHA-256 of its validated normalized JSON encoded as UTF-8 with sorted keys and compact separators. With a published MARKET intent and no tape, the result reports insufficient quote coverage.

```powershell
.venv/Scripts/python.exe -m research_backend.app.offline_market_entry --run <run-id> --intent <intent-id> --tape <tape.json> --sha256 <canonical-tape-sha256>
```

### Scope and rollback

The simulator validates research entry only. It has no HTTP, chart, account or broker integration. Position management, exits and P&L are outside this increment. Historical intents retain `AWAITING_EXECUTION_VALIDATION`. Production recorded trading remains `BLOCKED`.

Before merge, close the PR to discard the change. After merge, revert this increment while retaining PR #21. The SQLite change is additive. Leave the offline result table intact for audit retention, and return to the previous code to stop writing it. Existing historical ledgers, exact proofs, strategies and provider archives are preserved.

Focused verification completed with 6 passes, including the opt-in real negative control. The local runtime was Windows, Python 3.13.14 and Node 24.14.0.
