# TF-I08B timing plan

Base 42bd8c7539a9a4349d73a1b79be77466b0ca40e9, the checked merge of PR #10. Keep default LEGACY_RESEARCH policy version 1 with unchanged outputs. STRICT_CAUSAL policy version 2 uses separate identity namespaces and request-local execution. Compare observation UTC intervals with the exact required dependency and project a separate fail-closed strategy verdict. A stored checksum proves integrity. Recorded availability and feed provenance are separate evidence requirements.

| Engine | Present behavior and defect | Strict transition |
| --- | --- | --- |
| Structure | Arrival order gates close breaks and wick candidates equally | Compare close against the nominal close boundary and wick against bar open. A rejected observation cannot consume the pivot or switch structural state. |
| Liquidity | Arrival later than original activation can trigger a crossing even after a boundary revision | Use the most recent pivot revision as the barrier. Retire uncertain crossing state separately from a sweep or break. A new pool can form from later confirmed pivots. |
| Order block | Arrival later than visibility gates touch and mitigation, and candidate selection uses confirming arrival | Require causal structure and whole-candle displacement when configured. Require candidate evidence by observation start and use the block visibility barrier for lifecycle touches. |
| FVG | Arrival later than formation gates penetration even in close mode | Both modes use overlap, so require the bar-open barrier. Retire an ambiguous crossing without claiming mitigation. |
| CRT | Bar-open floor for reference and reclaim interaction with nominal-close timestamps | Preserve existing numerical formulas. Use the shared temporal decision primitives and preserve separate ambiguous states and causal barriers. |
| Replay and continuity | Engines sort by availability then opening time, and skip older arrivals | Preserve observable chronological prefix, make gaps and late evidence explicit, keep accepted observation count and distinguish scheduled closures. Availability reaching a replay cutoff does not move historical price order. |

Continuity invalidation precedes expiry and price transitions. Scheduled closure does not fabricate bars. Expiry uses accepted observation counts where the engine already tracks them. Unknown coverage and absent recorded arrival time fail closed for consumption. Freeze identity binds reference evidence and timing version. Delivery limits remain projection-only. Shared context requires a strategy-eligible dependency in strict mode. No persisted evidence is rewritten.
