# Offline elapsed-time exit

An elapsed-seconds deadline is the verified entry delivery time plus the frozen duration. The initial fixed stop and reward target stay active throughout. Process each eligible quote in priority order: if the price triggers existing protection, accept that exit first; otherwise, the first quote observed at or after the deadline closes at the actual executable bid (long) or ask (short) when the quote becomes available. A quotation observed before the deadline does not qualify for the timer merely because it arrived later.

An inclusive cutoff before the deadline leaves a continuously covered position open. An inclusive cutoff at or after the deadline without an eligible quote returns `AWAITING_TIME_EXIT_QUOTE`; the timer cannot invent a fill. Missing coverage or conflicting rows at the execution priority fail closed. Time exits record the exact quote and deadline and have no nominal price threshold.

Only the frozen `SECONDS` unit without a bar timeframe is supported. Eligible-bar time exits require attested candle availability, not an inferred time conversion from quotes. Combining this extension with another management extension remains unsupported. No account, live-routing, or public-admission changes.

Run `python -m pytest -q research_backend/tests/test_offline_time_exit.py` in addition to fixed, break-even, and trailing checks. To roll back, revert the implementation and retain immutable outcome records. No schema migration is needed.
