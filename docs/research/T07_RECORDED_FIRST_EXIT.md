# T07 offline first fixed exit on recorded BI5 quotes

Opt in to recorded exit validation with the exact same bounded `RecordedWindow` originally used by the persisted entry. The service replays that entry and compares its complete immutable result, including policy, partition pins, fill and recorded evidence. A synthetic entry, no-fill entry, changed window/selection or unrelated run cannot authorize a recorded exit. MARKET, LIMIT and STOP recorded fills are supported. This increment covers a fixed-pip protective stop and a fixed risk-multiple target. Other recorded management (break-even, trailing, partial and time exits) is not enabled by this policy.

The verified BI5 original is used to prove post-fill observations independently of the entry shards, which may end at the entry decision. It must contain the exact fill observation. A held position keeps the actual closing side: BID for LONG, ASK for SHORT. Thresholds are anchored to the executable entry fill, and a stop or target is executed at the observed closing quote, not a fictional threshold fill. The fill quote itself and later ordinals at its millisecond are eligible; earlier ordinals and observations after the inclusive exit cutoff cannot execute an exit.

Coverage extends only to the last observation at or before the cutoff with no gap over 60 seconds or FX calendar closure since the verified fill. There is intentionally no inference from a quote after cutoff. Thus a cutoff between quotes can be unresolved even when a slightly later cutoff is provable. An already verified exit remains unchanged as the cutoff grows. The result uses its own `OFFLINE_RECORDED_FIXED_FIRST_EXIT` policy and pins the original source; synthetic result identities and payloads are unchanged.

Run the CLI on archived research data only:

```
python -m research_backend.app.offline_first_exit --run <run-id> --entry-result <recorded-id> --cutoff-ms <UTC-ms> --recorded <same-pins.json>
python -m pytest -q research_backend/tests/test_recorded_first_exit.py
```

These simulations model zero delay from BI5 observation. They do not prove live quote arrival, spreads outside the archive, broker fills, cash P&L, or live trading readiness. To roll back, revert the recorded exit commit and retain immutable historical outputs and archives; no migration or live service is involved.
