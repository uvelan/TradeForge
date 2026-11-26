"""Local acceptance using reviewed archived candles and provider CSV references."""

import os
from pathlib import Path

import pytest

from scripts.t10_numerical_acceptance import SYMBOLS, verify_symbol


@pytest.fixture
def source_storage():
    path = os.getenv("TRADEFORGE_T10_SOURCE_STORAGE")
    if not path:
        pytest.skip("Set TRADEFORGE_T10_SOURCE_STORAGE to the original pinned archive storage root")
    return Path(path)


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_recorded_numerical_chart_prefixes(source_storage, client, symbol):
    verify_symbol(client, source_storage, symbol)
