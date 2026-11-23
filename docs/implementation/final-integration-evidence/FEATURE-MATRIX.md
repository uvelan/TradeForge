# Integration feature matrix

Baseline: `c3e4ae28fe152530e2b4fc3c4c0eec826737fbf0`. The [machine-readable backlog](BACKLOG.json) records dependencies, commit/evidence sources, and exclusions.

| ID | Selected work | Source and acceptance |
| --- | --- | --- |
| TF-I10A2-FIXED | Add one missing regression without rewriting fixed-exit behavior | #22–25 already merged; both tape orders must reject an ambiguous stop/miss and retain an earlier definite exit. |
| TF-I10A2-BE | Break-even stop | #26, source `b0a7c47`; use verified executable price and next quote priority; preserve fixed policy payloads. |
| TF-I10A2-MGMT | Trailing, elapsed-seconds, and one partial exit | #28, source `d8a06c7`; versioned policies, no look-ahead, residual protection and byte-exact old/new coexistence. |
| T03-DUP | Legacy catalog fail-closed artifact integrity | [T03](../../research/05_REMEDIATION_PLAN.md); verify registered and orphan paths, preserve bytes/keys, reject unverifiable artifacts. |
| T11-RESTORE | Two-catalog recovery rehearsal | #27 restore files only; online backup, SHA-256 manifests, integrity/FK checks, application reads and corruption rejection. |

No live trading, public admission expansion, mixed exit extensions, bar-count exit timing, broker accounting, or full production data/recovery rollout is delivered.
