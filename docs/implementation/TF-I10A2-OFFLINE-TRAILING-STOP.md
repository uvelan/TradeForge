# Offline fixed-distance trailing stop

This research-only policy starts with the verified fill's original fixed stop and reward target. The fill quote can close the position under that protection but never moves the stop. For each eligible later quote, test the already active stop and target first. If neither closes, tighten the stop only: long positions use bid minus the fixed distance, short positions use ask plus it. A tightened stop applies at the next strictly later priority, including a later sequence in the same millisecond. A gap through it fills at the closing quote, not the stop. The latest tightening quote and active stop remain visible even when a later priority is ambiguous or the evaluation window is incomplete.

The trail distance must be exactly representable in synthetic quote points. No rounding, assumed favorable intrabar path, or live prices are used. A distinct quote tied at a priority that could close or tighten is ambiguous. An earlier definite exit is final. Coverage must be continuous from entry delivery through each accepted transition and to the cutoff for an open outcome.

Result and event IDs use the new offline trailing policy. Fixed-only and break-even results keep their policy identities and payload contracts. Trailing combined with another management extension remains explicitly unsupported. Position size, cash P&L, account publication, and live broker routing are outside scope.

Run `python -m pytest -q research_backend/tests/test_offline_first_exit.py research_backend/tests/test_offline_break_even.py research_backend/tests/test_offline_trailing_stop.py`. To roll back, revert the implementation and retain immutable outcome records. No schema migration is needed.
