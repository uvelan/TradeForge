"""Restore the selected genuine partitions into the disposable comparison test workspace."""
from datetime import date
import os
from pathlib import Path
from tempfile import gettempdir

from scripts.u01_comparison_archive import reference

root = Path(os.environ["TRADEFORGE_STORAGE"]).resolve()
data = Path(os.environ["TRADEFORGE_DATA_DIR"]).resolve()
if root.parent != Path(gettempdir()).resolve() or not root.name.startswith("tradeforge-e2e-") or data != root / "research":
    raise RuntimeError("Expected an isolated Playwright workspace")

from research_backend.app.db import initialize
from .recorded_browser_app import app

initialize()
if os.environ.get("TRADEFORGE_PAIR_REOPEN") != "1":
    reference(date.fromisoformat(os.environ["TRADEFORGE_COMPARISON_DAY"]), data)
