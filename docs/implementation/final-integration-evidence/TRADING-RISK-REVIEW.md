# Trading risk and security review

Reviewed by a separate risk reviewer. The reviewer independently ran 23 break-even, trailing, and elapsed-time tests on candidate `d8a06c798c0223a0d27665f3a759f8cc6c1c3c1b` and did not count their own partial-exit implementation as independent review. A separate QA reviewer exercised partial reductions and terminal ties.

- Long closes use bid; short closes use ask. No fill is inferred between quotes or from an unobserved gap. Contradictory equal-priority rows and incomplete coverage fail closed.
- Break-even and trailing ratchets do not take effect at the priority that proves the transition. Elapsed deadlines derive from entry fill delivery and wait for an eligible executable closing quote. Partial reductions use exact normalized fractions and protect the remainder with the original stop/target.
- The synthetic tests neither obtain broker credentials nor submit orders. Recorded trading remains blocked; live orders, mixed policy expansion, venue sizing, fees, margin, FX, and broker reconciliation are not certified here.
- The backup rehearsal is not hostile-manifest authentication. Restore roots and fixture data are isolated. Workflow token permissions and broker connectivity are not broadened.

No high/critical issue was confirmed in the isolated candidate review. This is not a release verdict on an untested aggregate commit.
