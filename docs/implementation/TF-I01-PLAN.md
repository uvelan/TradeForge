# TF-I01 plan

Base is the merged TF-D02 commit 9ec151e401a74352c78e77d441ede1dc0ff56f0d. The terminal already calculates EMA, RSI, ATR, and other traditional indicators in Python and displays one result. The session panel uses separate presentation logic. Replay has a frozen candle prefix and an availability cutoff. Simulations and AI exports preserve their own evidence contracts.

Keep the existing calculation endpoint and registry formulas compatible. Add a bounded batch evaluator with typed definitions, per-point status and availability, a deterministic hash of the eligible candle prefix, and caller-declared feed identity. Caller-supplied candles remain unverified. Use the same evaluator for historical series and chart output. Add UTC day/week levels and IANA session windows computed from closed candles only. Reject duplicate or overlapping bars. Warn on gaps and withhold range confirmation when coverage is incomplete. EMA seeds from the SMA. RSI and ATR use Wilder smoothing. ATR measures gaps from the previous observed close.

Adapt the existing terminal controls and renderer for a persisted list of active indicators, keeping the existing primary selector and evidence deep links. Keep chart instances and pointer controllers independent of indicator rendering. Supply a cutoff for replay and use availability timestamps when supplied by the history loader. Leave simulations, AI exports, SMT, and the existing session panel independent of the new chart evidence.

Test independent formula values, gap and duplicate handling, closed-period boundaries, DST, replay cutoffs, UI persistence, panes, and drawing interaction. Keep the evaluator bounded to eight definitions and 2,000 candles per request. Test against the existing backend, frontend, and Chromium suites.

Plan review. Do not imply archival verification from a fingerprint or a caller-supplied feed ID. Require a whole candle inside a session and handle absent bars conservatively. Treat exchange calendars and execution evidence as separate concerns. Do not approximate a missing period with the last observed period.
