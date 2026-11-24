import os
import tempfile
from pathlib import Path
import pytest

@pytest.fixture
def client(tmp_path,monkeypatch,request):
    # Config path must be overridden before importing backend app modules.
    from research_backend.app import db,config,domain,storage
    path=tmp_path/'test.sqlite3'
    monkeypatch.setattr(db,'DATABASE',path)
    monkeypatch.setattr(config,'DATABASE',path)
    for mod,attr,subfolder in [(domain,'EXPORT_DIR','exports'),(domain,'JOURNAL_DIR','journal'),(storage,'MARKET_DIR','market')]:
        target=tmp_path/subfolder
        target.mkdir(exist_ok=True)
        monkeypatch.setattr(mod,attr,target)
    monkeypatch.setattr(domain,'JOURNAL_FILE',tmp_path/'journal'/'Forex Intraday Trading - Master Learning Journal.md')
    from fastapi.testclient import TestClient
    from research_backend.app.main import app
    if "canonical_successors" in request.fixturenames:
        # These fixtures drive claim/publish themselves. Install the guard before
        # lifespan can submit its first real claim to the background executor.
        monkeypatch.setattr("research_backend.app.historical_strategy_worker.claim",lambda _:None)
    with TestClient(app) as c:
        yield c

@pytest.fixture
def plan():
    return {'symbol':'EURUSD','direction':'BUY','strategy':'CRT candidate','session':'LONDON','timeframe':'M5',
    'context':'Higher timeframe trend is bullish','observation':'Prior session low was swept',
    'evidence':'An M5 bar closed back in range','hypothesis':'Price may reverse into range',
    'setup':'Sweep and range reclaim conditions','trigger':'Wait for M5 close above range',
    'invalidation':'Below the swept session low','management':'Do not move stop prematurely',
    'entry':'1.08500','stop':'1.08300','target':'1.08900',
    'equity_usd':'10000','risk_percent':'0.5','pip_value_per_lot_usd':'10'}
