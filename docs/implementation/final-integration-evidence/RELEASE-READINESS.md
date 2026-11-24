# Release readiness

**BLOCKED for release until the integration PR demonstrates exact-head validation and its required GitHub checks.** This research increment does not authorize activation of recorded or live trading.

Pending or excluded: combined management rules, candle-count time exits, execution-grade position sizing and cash P&L, production MT5 clock/timestamp attestation, a real clean-profile Windows recovery drill and broader catalog migration. Authoritative broker specs, redacted statement, calendars, and cutover approval are separate gates. See the feature matrix for scope and the PR for the current review verdict.

Rollback: do not merge a blocked draft. If deployed in a future authorized release, revert the integration merge using a normal reviewed PR; keep immutable SQLite outcome rows and data files for audit and restore from a verified snapshot if needed. No catalog reencoding or destructive migration is part of this increment. A rejected catalog artifact should be diagnosed and recovered from a trusted source, not replaced automatically.
