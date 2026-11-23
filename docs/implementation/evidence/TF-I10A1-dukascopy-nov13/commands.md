# November 13 reproduction commands

Use PowerShell from the repository root with the project virtual environment active. Point the normal `TRADEFORGE_STORAGE` or `TRADEFORGE_DATA_DIR` at the workspace containing the approved archives if they differ from the default. The pytest isolation hook preserves those paths so the benchmark can copy the approved files read-only. The isolated database and relocated market directory receive all test writes.

```powershell
git fetch origin
git fetch origin main
git rev-parse origin/main
$env:TRADEFORGE_BENCH_OUTPUT = (Resolve-Path 'docs/implementation/evidence/TF-I10A1-dukascopy-nov13').Path
python -m pytest -q -s --tb=short research_backend/tests/benchmark_dukascopy_nov13.py::test_pin_nov13
$env:TRADEFORGE_BENCH_PATH = 'fresh'
python -m pytest -q -s --tb=short research_backend/tests/benchmark_dukascopy_nov13.py::test_measure_nov13
$env:TRADEFORGE_BENCH_PATH = 'optimized'
python -m pytest -q -s --tb=short research_backend/tests/benchmark_dukascopy_nov13.py::test_measure_nov13
python -m pytest -q -s --tb=short research_backend/tests/benchmark_dukascopy_nov13.py::test_nov13_equivalence_and_recovery
$env:TRADEFORGE_BENCH_OUTPUT = Join-Path $env:TEMP ('tradeforge-real-nov13-synthetic-'+[guid]::NewGuid())
New-Item -ItemType Directory -Path $env:TRADEFORGE_BENCH_OUTPUT
python -m pytest -q -s --tb=short research_backend/tests/benchmark_offline_crt_smt.py::test_in_window_confirmation_revision
$env:PYTHONPATH = (Get-Location).Path
python scripts/verify_real_strategy_evaluation.py --historical
python -m pytest -q --tb=short research_backend/tests/test_bounded_crt_smt_successors.py research_backend/tests/test_historical_strategy_runs.py research_backend/tests/test_historical_successor_oracle.py research_backend/tests/test_historical_publication_v2.py research_backend/tests/test_strategy_evaluator.py
git diff --check
```

Fresh and cached measurements ran in separate processes. Cold is the first replay in its process, and warm is the second replay with a new per-replay cache. OS caches were not flushed. The complete scratch ledgers are inputs to the comparison and recovery test. The committed comparison retains their ordered trace hashes, ledger hashes, exact decision and opportunity references, and resource measurements. The existing eight control scripts use their original recipes.
