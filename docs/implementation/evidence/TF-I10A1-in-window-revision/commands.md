# In-window archive revision verification

Use the project virtual environment and PowerShell from the repository root.

```powershell
git fetch origin
git rev-parse HEAD origin/codex/offline-crt-smt-session
$env:TRADEFORGE_BENCH_OUTPUT = (Resolve-Path 'docs/implementation/evidence/TF-I10A1-in-window-revision').Path
python -m pytest -q -s --tb=short research_backend/tests/benchmark_offline_crt_smt.py::test_in_window_confirmation_revision
python -m pytest -q -s --tb=short research_backend/tests/benchmark_offline_crt_smt.py::test_session_recovery_and_equivalence
python -m pytest -q --tb=short research_backend/tests/test_bounded_crt_smt_successors.py research_backend/tests/test_smt_causal.py research_backend/tests/test_historical_successor_oracle.py research_backend/tests/test_historical_publication_v2.py
git diff --check
```

The four-path test generates two scratch JSON artifacts for the existing recovery test. The four-path evidence JSON retains the material traces, cache assertions, archive coordinates, and per-inspection market and raw digests. The committed report states where cross-archive provenance changes and which comparisons use exact references.

The committed evidence collapses identical fresh/cached inspection vectors within each archive into one vector, retaining all hashes and each path's full material traces. Raw proof coordinates remain visible.
