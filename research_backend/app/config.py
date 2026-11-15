"""Paths and fixed server settings. No remote exposure or secrets."""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORAGE_ROOT = Path(os.getenv('TRADEFORGE_STORAGE', str(ROOT / 'storage')))
DATA_DIR = Path(os.getenv('TRADEFORGE_DATA_DIR', str(STORAGE_ROOT / 'research'))).resolve()
DATABASE = DATA_DIR / 'tradeforge.sqlite3'
MARKET_DIR = DATA_DIR / 'market'
EXPORT_DIR = DATA_DIR / 'ai-exports'
JOURNAL_DIR = DATA_DIR / 'journal'
JOURNAL_FILE = JOURNAL_DIR / 'Forex Intraday Trading - Master Learning Journal.md'

for folder in [DATA_DIR, MARKET_DIR, EXPORT_DIR, JOURNAL_DIR]:
    folder.mkdir(parents=True, exist_ok=True)
