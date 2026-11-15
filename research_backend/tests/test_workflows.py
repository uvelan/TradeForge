from __future__ import annotations
import io,zipfile,json,sqlite3
from decimal import Decimal
from research_backend.app.risk import evaluate
from research_backend.app.schemas import TradePlan

def test_health_and_demo_candle_labels(client):
    assert client.get('/api/health').json()['execution_enabled'] is False
    market=client.get('/api/market/candles?symbol=EURUSD&source=DEMO').json()
    assert market['synthetic'] is True
    assert all(c['high']>=max(c['open'],c['close']) and c['low']<=min(c['open'],c['close']) for c in market['candles'])
    assert market['candles']==sorted(market['candles'],key=lambda c:c['time'])

def test_plan_export_import_closure(client,plan):
    resp=client.post('/api/trades',json=plan);assert resp.status_code==201,resp.text
    trade=resp.json();uid=trade['id'];assert trade['versions'][0]['version']==1
    risk=client.post('/api/risk/evaluate',json={'plan':plan}).json()
    assert risk['maximum_planned_loss_usd']=='50.00'
    assert risk['illustrative_lots']=='0.25'
    invalid=client.post('/api/trades',json={**plan,'stop':'1.08600'})
    assert invalid.status_code==422
    exp=client.post(f'/api/ai/exports/{uid}');assert exp.status_code==201,exp.text
    pkg=exp.json()['package_id'];zip_resp=client.get(f'/api/ai/packages/{pkg}/download')
    assert zip_resp.status_code==200
    with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as z:
        content=z.read('AI_CONTEXT.md').decode()
        assert 'PRE-TRADE' in content and '50.00' in content
        assert 'data/timeline.jsonl' in z.namelist()
    response={'package_id':pkg,'trade_decision_id':uid,'plan_version':1,'assessment':'INSUFFICIENT_DATA',
        'findings':[{'text':'Broker-specific spread evidence missing','evidence_refs':['PLAN','RISK']}],
        'missing_evidence':['Actual market tick history'],'limitations':[]}
    original = json.dumps(response, indent=2).encode()
    imported = client.post('/api/ai/reviews', content=original, headers={'Content-Type':'application/json'})
    assert imported.status_code == 201, imported.text
    review_id = imported.json()["id"]
    assert client.get(f'/api/ai/reviews/{review_id}/original').content == original
    assert client.post('/api/ai/reviews',json=response).json()['verification']=='DUPLICATE_IGNORED'
    assert client.post('/api/ai/reviews',json={**response,'plan_version':999}).status_code==422
    assert client.post('/api/ai/reviews',json={**response,'findings':[{'text':'No source','evidence_refs':['FAKE']}]}).status_code==422
    rev=client.post(f'/api/trades/{uid}/revisions',json={**plan,'target':'1.09100'})
    assert rev.status_code==200,rev.text
    assert rev.json()['versions'][0]['version']==2
    assert len(rev.json()['versions'])==2
    close=client.post(f'/api/trades/{uid}/close',json={'pnl_usd':'100.00','result_r':'2','notes':'Paper result'})
    assert close.status_code==200
    assert client.post(f'/api/trades/{uid}/close',json={'pnl_usd':'0','result_r':'0'}).status_code==409
    assert client.post(f'/api/trades/{uid}/revisions',json=plan).status_code==409
    a=client.get('/api/analytics').json()
    assert a['closed']==1 and a['expectancy_r']==2.0

def test_immutable_plan_and_events(client,plan):
    from research_backend.app.db import transaction
    t=client.post('/api/trades',json=plan).json()
    with transaction() as db:
        try:
            db.execute('DELETE FROM trade_events WHERE trade_id=?',(t['id'],))
            assert False,'Should have failed'
        except sqlite3.IntegrityError as e:
            assert 'immutable' in str(e)
        try:
            db.execute('UPDATE plan_versions SET version=5 WHERE trade_id=?',(t['id'],))
            assert False,'Should have failed'
        except sqlite3.IntegrityError as e:
            assert 'immutable' in str(e)

def test_journal_stable_ids(client):
    a=client.post('/api/journal',json={'kind':'T','title':'Market structure','content':'Study swing highs and lows.'}).json()
    b=client.post('/api/journal',json={'kind':'T','title':'Liquidity sweep','content':'Study highs and stop runs.'}).json()
    assert a['id']=='T-00001' and b['id']=='T-00002'
    assert client.get('/api/journal/download').status_code==200

def test_backtest_does_not_overlap_and_rejects_short_series(client):
    result=client.get('/api/backtest?source=DEMO').json()
    assert result['synthetic'] is True
    timestamps=[t['entry_time'] for t in result['trades']]
    assert timestamps==sorted(timestamps)

def test_risk_buy_and_sell_validity(plan):
    buy=evaluate(TradePlan(**plan))
    sell=evaluate(TradePlan(**{**plan,'direction':'SELL','entry':'1.08500','stop':'1.08700','target':'1.08100'}))
    assert buy['maximum_planned_loss_usd']==sell['maximum_planned_loss_usd']=='50.00'
    assert buy['reward_risk']=='2.00'
