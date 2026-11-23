# Uncertainty correction verification

From the project root with its virtual environment active:

```text
git fetch origin
git rev-parse HEAD origin/codex/historical-opportunity-accounting
python -m pytest -q -s --tb=short research_backend/tests/test_historical_strategy_runs.py::test_false_context_sibling_does_not_mask_opportunity_uncertainty
python -m pytest -q -s --tb=short research_backend/tests/test_historical_strategy_runs.py::test_false_context_sibling_does_not_mask_opportunity_uncertainty research_backend/tests/test_historical_strategy_runs.py::test_masked_overflow_recovery_preserves_counts_and_established_family
python -m pytest -q --tb=short research_backend/tests/test_bounded_crt_smt_successors.py research_backend/tests/test_historical_successor_oracle.py research_backend/tests/test_historical_strategy_runs.py research_backend/tests/test_historical_publication_v2.py research_backend/tests/test_strategy_evaluator.py research_backend/tests/test_negated_current.py
git diff --check
```

The first pytest command was run before touching production code. The committed baseline log repeats it with pinned fixture identities and the reviewed production engine and route modules loaded from Git. The temporary Python harness used the following commands before running that first pytest invocation through `pytest.main`:

```python
import subprocess
import sys
from research_backend.app import historical_strategy_engine, historical_strategy_routes
import pytest

base = "6d57bbec049e3c257467bc8821033c9a3628ac7b"
for module in (historical_strategy_engine, historical_strategy_routes):
    path = module.__name__.replace(".", "/") + ".py"
    source = subprocess.check_output(["git", "show", f"{base}:{path}"])
    exec(compile(source, path, "exec"), module.__dict__)
raise SystemExit(pytest.main(sys.argv[1:]))
```

The command exited 1 with the expected unresolved-count assertion. Comparing the first two decision IDs in that log with the corrected run confirmed exact equality.

From `frontend`:

```text
npm run build
npm test
```
