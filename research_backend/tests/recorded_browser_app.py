"""Serve production candle and import routes with live-terminal access disabled."""
import sys

sys.modules["MetaTrader5"] = None

from tradeforge.api import app  # noqa: E402,F401
