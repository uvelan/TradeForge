"""Boundary tests: never misrepresent simulator output as a live broker quote."""
from research_backend.app import main
from research_backend.app.market import mt5_symbols
from types import SimpleNamespace
import sys


def test_mt5_picker_includes_forex_and_selected_markets(monkeypatch):
    def instrument(name, mode, base, quote):
        return SimpleNamespace(name=name, trade_calc_mode=mode, currency_base=base,
                               currency_profit=quote, description=name, digits=5)

    fake = SimpleNamespace(
        initialize=lambda **kwargs: True,
        SYMBOL_CALC_MODE_FOREX=0, SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE=5,
        symbols_get=lambda: [
            instrument("A", 32, "USD", "USD"),
            instrument("XAUUSD", 4, "XAU", "USD"),
            instrument("XAGUSD", 4, "XAG", "USD"),
            instrument("USTEC", 2, "", "USD"),
            instrument("EURUSD.a", 0, "EUR", "USD"),
            instrument("GBPJPY", 5, "GBP", "JPY"),
        ],
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake)
    assert [row["name"] for row in mt5_symbols()] == ["EURUSD.a", "GBPJPY", "USTEC", "XAGUSD", "XAUUSD"]


def test_mt5_symbol_discovery_and_latest_bar_opt_in(client, monkeypatch):
    item = {"name": "EURUSD.a", "description": "Euro", "digits": 5}
    monkeypatch.setattr(main, "mt5_symbols", lambda: [item])
    assert client.get("/api/market/mt5/symbols").json() == [item]
    completed = {"time": 100, "open": 1, "high": 1.2, "low": 1, "close": 1.1}
    newest = {**completed, "time": 200}

    def rates(symbol, timeframe, limit, *, include_latest=False, provenance=None):
        assert symbol == "EURUSD.a"
        return [completed, newest] if include_latest else [completed]

    monkeypatch.setattr(main, "mt5_rates", rates)
    url = "/api/market/candles?symbol=EURUSD.a&timeframe=D1&source=MT5"
    live = client.get(url + "&include_latest=true").json()
    assert live["latest_unconfirmed"] and live["candles"] == [completed, newest]
    history = client.get(url).json()
    assert not history["latest_unconfirmed"] and history["candles"] == [completed]


def test_synthetic_candles_are_visibly_identified(client):
    result = client.get('/api/market/candles?symbol=EURUSD&timeframe=H1&source=DEMO')
    assert result.status_code == 200
    data = result.json()
    assert data['source'] == 'DEMO'
    assert data['synthetic'] is True
    assert data['price_basis'] == 'SIMULATED'
    assert len(data['candles']) >= 20


def test_unknown_symbol_or_market_source_is_rejected(client):
    assert client.get('/api/market/candles?symbol=NOTREAL').status_code == 422
    assert client.get('/api/market/candles?source=UNKNOWN').status_code == 422
    assert client.get('/api/market/quote?source=UNKNOWN').status_code == 422


def test_mt5_failure_does_not_fallback_to_demo(client, monkeypatch):
    def disconnected(*args, **kwargs):
        raise RuntimeError('MT5 disconnected')
    monkeypatch.setattr(main, 'mt5_rates', disconnected)
    response = client.get('/api/market/candles?source=MT5')
    assert response.status_code == 503
    assert 'MT5 disconnected' in response.json()['detail']


def test_demo_quote_is_never_declared_live(client):
    q = client.get('/api/market/quote?source=DEMO').json()
    assert q['synthetic'] is True
    assert q['stale'] is True
    assert q['ask'] > q['bid']
