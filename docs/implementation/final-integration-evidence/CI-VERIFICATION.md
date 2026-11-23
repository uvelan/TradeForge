# CI verification

Baseline `c3e4ae28fe152530e2b4fc3c4c0eec826737fbf0`: [Windows CI](https://github.com/uvelan/TradeForge/actions/runs/45214756925) passed. Candidate runs are not integration-head evidence: [#26](https://github.com/uvelan/TradeForge/actions/runs/45215570000), [#27](https://github.com/uvelan/TradeForge/actions/runs/45225307266), [#28](https://github.com/uvelan/TradeForge/actions/runs/45225776849).

For the aggregate head, use the CI runs linked in the integration PR and confirm they match that PR head (or its GitHub-generated test merge commit). Record run URLs and conclusions on the PR without changing the validated code tree. At publication, an unfinished or failing check is not a pass. The connected GitHub integration received 403 from the branch-protection endpoint; observed workflow results alone do not prove absence of additional required rules.
