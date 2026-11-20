"""Playwright-only app with an explicitly allowlisted canonical synthetic fixture."""
from functools import lru_cache
import os
import sys
from pathlib import Path
from tempfile import gettempdir

storage = Path(os.environ["TRADEFORGE_STORAGE"]).resolve()
data = Path(os.environ["TRADEFORGE_DATA_DIR"]).resolve()
if not storage.is_relative_to(Path(gettempdir()).resolve()) or not storage.name.startswith("tradeforge-e2e-") or data != storage/"research":
    raise RuntimeError("The fixture app requires an isolated Playwright archive")

# Keep the offline browser app isolated from an installed local terminal.
sys.modules["MetaTrader5"] = None

from tradeforge.api import app
from research_backend.app import historical_strategy_inputs, historical_strategy_routes
from research_backend.app import strategy_evaluation_routes as routes
from research_backend.app import strategy_evaluation_sources as sources


@lru_cache(maxsize=1)
def fixture_catalog():
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    try:
        from test_strategy_evaluator import seed_fixture
        return [seed_fixture()]
    finally:
        sys.path.pop(0)


sources.selection_catalog = fixture_catalog
routes.selection_catalog = fixture_catalog

historical_strategy_inputs.selection_catalog = fixture_catalog
historical_strategy_routes.selection_catalog = fixture_catalog
