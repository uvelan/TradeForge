# Verification commands

Windows, Python 3.13.14, Node 24.14.0, npm 11.1.0. Run Python commands from the repository root with the project's virtual environment activated and the repository on `PYTHONPATH` for the standalone real-archive script.

```text
python -m compileall -q research_backend scripts
python -m pytest -q -s research_backend/tests/test_bounded_crt_smt_successors.py
python -m pytest -q research_backend/tests/test_historical_successor_oracle.py research_backend/tests/test_historical_strategy_runs.py research_backend/tests/test_historical_publication_v2.py research_backend/tests/test_negated_current.py research_backend/tests/test_strategy_evaluator.py
python -m pytest -q research_backend/tests/test_analysis_evidence.py::test_approved_golden_singles_and_combined_are_unchanged
python scripts/verify_strategy_test_isolation.py
python scripts/verify_real_strategy_evaluation.py --historical
```

The real-session JSON retains the selection, individual provenance comparisons, and historical outcomes from the command. The syntax log records the exit status. The build log retains the bundle results and an example bundler warning.

Run frontend commands from `frontend`. The Chromium commands used separate temporary output directories and local API/UI ports 8222 and 4222.

```text
npm run test
npm run build
npm run e2e -- --retries=0 --reporter=line
npm run e2e -- e2e/research.spec.ts -g "persists a trendline and resumes a frozen replay checkpoint" --retries=0 --repeat-each=20 --reporter=line
npm run e2e -- e2e/research.spec.ts -g "during delayed analytical and persistence responses" --retries=0 --reporter=line
npm run e2e -- e2e/arrival-capture.spec.ts --retries=0 --reporter=line
```

## PR #18 evidence correction

Starting remote head and local HEAD both resolved to `b816d3abe9c7fd19ed9d765349c6d48006e3bd57` after fetching `codex/crt-smt-successor-evidence`.

```text
git fetch origin refs/heads/codex/crt-smt-successor-evidence:refs/remotes/origin/codex/crt-smt-successor-evidence
git rev-parse HEAD origin/codex/crt-smt-successor-evidence
python -B -m pytest -q -s --tb=short research_backend/tests/test_bounded_crt_smt_successors.py research_backend/tests/test_historical_successor_oracle.py::test_rearm_rejects_same_family_and_equal_cutoff
python -B -m pytest -q research_backend/tests/test_historical_successor_oracle.py research_backend/tests/test_historical_strategy_runs.py research_backend/tests/test_historical_publication_v2.py
git diff --exit-code -- research_backend/app
git diff --check
```

For the isolated mutation, remove only the disjunct checking membership in `self.spent_families` in `EvaluationMachine.select`. Run the single shared-selector test with `python -B -m pytest -q -s` followed by its node ID above. Restore the original source before the normal run. The [mutation trace](spent-mutation.txt) records the expected failed assertion.
