"""TradeForge — localhost-only API, no execution/trading authority."""
from __future__ import annotations
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
from hashlib import sha256
import asyncio
import json
import uuid
from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel,Field
import httpx
from . import binance
from .db import initialize, rows, one, transaction, utc_now
from .market import mt5_connection, mt5_rates, mt5_recent_deals,mt5_quote,mt5_symbols,demo_candles,BASE,FRAME_MIN
from .storage import write_partition, decode_bi5, aggregate_ticks,read_partition
from .domain import create_trade,get_trade,revise,close_trade,list_trades,analytics,export_ai,import_ai,journal_add,journal_list,journal_materialize
from .risk import evaluate
from .backtest import breakout_backtest
from .tick_replay import replay_breakout
from .schemas import TradePlan,Outcome,ReviewInput,ReviewV1,JournalInput,RiskInput
from . import domain
from .indicator_routes import router as indicator_router, run_smt_monitor
from .saved_history import recent_archive_range, saved_range

@asynccontextmanager
async def lifespan(app:FastAPI):
    initialize()
    journal_materialize()
    app.state.smt_monitor = {}
    stop = asyncio.Event()
    monitor = asyncio.create_task(run_smt_monitor(stop, app.state.smt_monitor))
    try:
        yield
    finally:
        stop.set()
        await monitor

app=FastAPI(title='TradeForge Local API',version='0.1.0',lifespan=lifespan)
app.include_router(indicator_router)
app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5173','http://localhost:5173','http://127.0.0.1:4180','http://localhost:4180'],allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['Content-Type'])

@app.get('/api/health')
def health():return {'status':'ok','mode':'LOCAL_SINGLE_USER','execution_enabled':False,'time_utc':utc_now()}

@app.get('/api/overview')
def overview():
    return {'analytics':analytics(), 'mt5':mt5_connection(),
      'datasets':rows('SELECT * FROM datasets ORDER BY created_at DESC LIMIT 5'),
      'recent_trades':list_trades()[:5], 'recent_reviews':rows('SELECT * FROM ai_reviews ORDER BY created_at DESC LIMIT 5'),
      'journal_count':one('SELECT count(*) AS n FROM journal_entries')['n']}

@app.get('/api/market/status')
def market_status():return {'mt5':mt5_connection(),'dukascopy':{'available':'MANUAL_IMPORT','notice':'Official S3 data can incur requester-pays charges.'},'demo':True}

@app.get('/api/market/candles')
def market_candles(symbol:str='EURUSD',timeframe:str='M5',source:str='DEMO',limit:int=Query(180,ge=10,le=1000),
                   include_latest:bool=False,start:int|None=None,end:int|None=None):
    if (start is None) != (end is None) or (start is not None and source not in ("MT5_SAVED", "DUKASCOPY_SAVED")):
        raise HTTPException(422, "Provide both start and end UTC epoch seconds for a saved source")
    if source == "BINANCE_SPOT":
        if symbol != "BTCUSDT" or timeframe not in binance.INTERVALS:
            raise HTTPException(422, "Binance Spot supports BTCUSDT and the standard chart timeframes")
        try:
            candles = binance.historical_candles(timeframe, limit)
        except (httpx.HTTPError, ValueError) as error:
            raise HTTPException(503, "Binance Spot candle history unavailable") from error
        return {"source": source, "synthetic": False, "price_basis": "SPOT_TRADE_OHLC", "candles": candles}
    if timeframe not in FRAME_MIN or (source != 'MT5' and symbol not in BASE):
        raise HTTPException(422,'Unsupported instrument or timeframe')
    if source=='DEMO': return {'source':'DEMO','synthetic':True,'price_basis':'SIMULATED','feed_id':'demo-v1','candles':demo_candles(symbol,timeframe,limit)}
    if source=='MT5':
        provenance = {}
        try: candles=mt5_rates(symbol,timeframe,limit,include_latest=include_latest,provenance=provenance)
        except (RuntimeError,ValueError) as ex:raise HTTPException(503,str(ex)) from ex
        return {'source':'MT5','synthetic':False,'price_basis':'BROKER_BARS_UNSPECIFIED','candles':candles,
                'latest_unconfirmed':include_latest and bool(candles), **provenance}
    if source in ('MT5_SAVED','DUKASCOPY_SAVED'):
        provider=source.split('_')[0]
        try:
            step = FRAME_MIN[timeframe] * 60
            if start is None or end is None:
                start, end = recent_archive_range(provider, symbol, timeframe, step)
            result = saved_range(provider, symbol, timeframe, start, end, step, limit)
        except FileNotFoundError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, RuntimeError, OSError) as error:
            raise HTTPException(409, str(error)) from error
        return {"source":source, "synthetic":False, **result}
    raise HTTPException(422,'Unsupported source')

@app.get('/api/market/quote')
def quote(symbol:str='EURUSD',source:str='DEMO'):
    if source != 'MT5' and symbol not in BASE:raise HTTPException(422,'Unsupported symbol')
    if source=='DEMO':
        b=demo_candles(symbol,'M5',30)[-1]
        return {'source':'DEMO','synthetic':True,'symbol':symbol,'bid':b['close'],
          'ask':round(b['close']+(0.0001 if symbol in ('EURUSD','GBPUSD') else 0.01 if symbol=='USDJPY' else .2),5),
          'as_of':datetime.fromtimestamp(b['time'],timezone.utc).isoformat(), 'stale':True}
    if source!='MT5':raise HTTPException(422,'Unsupported source')
    try:return mt5_quote(symbol)
    except (RuntimeError,ValueError) as ex:raise HTTPException(503,str(ex)) from ex


@app.get('/api/market/mt5/symbols')
def broker_symbols():
    try:
        return mt5_symbols()
    except RuntimeError as error:
        raise HTTPException(503, str(error)) from error

@app.get('/api/trades')
def trades():return list_trades()

@app.post('/api/trades',status_code=201)
def create(plan:TradePlan):return create_trade(plan)

@app.get('/api/trades/{uid}')
def detail(uid:str):return get_trade(uid)

@app.post('/api/trades/{uid}/revisions')
def update_plan(uid:str,plan:TradePlan):return revise(uid,plan)

@app.post('/api/trades/{uid}/close')
def record_outcome(uid:str,outcome:Outcome):return close_trade(uid,outcome)

@app.post('/api/risk/evaluate')
def risk(inp:RiskInput):return evaluate(inp.plan)

@app.get('/api/analytics')
def stats():return analytics()

@app.post('/api/ai/exports/{uid}',status_code=201)
def create_export(uid:str):return export_ai(uid)

@app.get('/api/ai/packages')
def list_packages():return rows('SELECT id,trade_id,plan_version,knowledge_cutoff,sha256,created_at FROM ai_packages ORDER BY created_at DESC')

@app.get('/api/ai/packages/{pkg}/download')
def download_ai(pkg:str):
    p=one('SELECT * FROM ai_packages WHERE id=?',(pkg,))
    if not p:raise HTTPException(404,'Package not found')
    path=Path(p['location']).resolve()
    if not path.is_relative_to(domain.EXPORT_DIR.resolve()) or not path.is_file():raise HTTPException(404,'Package file missing')
    if sha256(path.read_bytes()).hexdigest()!=p['sha256']:raise HTTPException(409,'Export checksum mismatch')
    return FileResponse(path,media_type='application/zip',filename=f'{pkg}.zip')

@app.post('/api/ai/reviews',status_code=201)
def import_review(review:ReviewV1 | ReviewInput):return import_ai(review)

@app.get('/api/ai/reviews')
def reviews():return rows('SELECT id,package_id,assessment,verification,created_at FROM ai_reviews ORDER BY created_at DESC')

@app.get('/api/journal')
def journal():return journal_list()

@app.post('/api/journal',status_code=201)
def new_journal(entry:JournalInput):return journal_add(entry)

@app.get('/api/journal/download')
def journal_download():
    return FileResponse(domain.JOURNAL_FILE,media_type='text/markdown',filename='Forex Intraday Trading - Master Learning Journal.md')

@app.get('/api/datasets')
def datasets():return rows('SELECT * FROM datasets ORDER BY created_at DESC LIMIT 500')

class MT5Import(BaseModel):
    symbol:str='EURUSD'
    timeframe:str='M5'
    limit:int=Field(300,ge=10,le=2000)

@app.post('/api/market/import/mt5')
def import_mt5(inp:MT5Import):
    try:
        provenance = {}
        rates=mt5_rates(inp.symbol,inp.timeframe,inp.limit,provenance=provenance)
        if not rates: raise RuntimeError('No completed MT5 bars were returned')
        if not provenance.get("feed_id"): raise RuntimeError("MT5 feed and price basis are unavailable")
        feed='mt5-'+sha256(provenance["feed_id"].encode()).hexdigest()[:12]
        return write_partition('MT5',feed,inp.symbol,'candles',inp.timeframe,provenance["price_basis"],rates)
    except (RuntimeError,ValueError,ImportError) as ex:raise HTTPException(503,str(ex)) from ex

@app.post('/api/market/import/bi5')
async def import_dukascopy_bi5(symbol:str,day:str,hour:int|None=Query(None,ge=0,le=23),file:UploadFile=File(...)):
    try:
        if symbol not in BASE:raise ValueError('Unsupported symbol')
        date=datetime.strptime(day,'%Y-%m-%d').replace(tzinfo=timezone.utc)
        start = date + timedelta(hours=hour or 0)
        end = start + (timedelta(hours=1) if hour is not None else timedelta(days=1))
        if end > datetime.now(timezone.utc):
            raise ValueError("Select a completed UTC hour or day")
        payload=await file.read(40_000_001)
        records=await asyncio.to_thread(decode_bi5,payload,symbol,date,hour)
        if not records:raise ValueError('No ticks present in supplied source file')
        tick_result=await asyncio.to_thread(write_partition,'DUKASCOPY','dukascopy-bi5',symbol,'ticks',None,'BID_ASK_UNITS',records)
        candles=await asyncio.to_thread(aggregate_ticks,records,5)
        if candles:
            await asyncio.to_thread(write_partition,'DUKASCOPY','dukascopy-bi5',symbol,'candles','M5','BID',candles)
        return {'tick_dataset':tick_result,'candle_rows':len(candles),
          'note':'Imported source file; no guarantee data is exhaustive.'}
    except (RuntimeError,ValueError,ImportError) as ex:raise HTTPException(422,str(ex)) from ex

@app.post('/api/market/import/mt5-deals')
def import_deals(days:int=Query(30,ge=1,le=365)):
    try:deals=mt5_recent_deals(days)
    except RuntimeError as ex:raise HTTPException(503,str(ex)) from ex
    imported=0
    with transaction() as db:
        for deal in deals:
            cur=db.execute('INSERT OR IGNORE INTO imported_fills(id,broker_key,deal_id,symbol,payload,imported_at) VALUES(?,?,?,?,?,?)',
              (str(uuid.uuid4()),deal['broker_key'],deal['deal_id'],deal['symbol'],json.dumps(deal['payload']),utc_now()))
            imported+=cur.rowcount
    return {'imported':imported,'seen':len(deals),'note':'Raw broker deals imported, not yet grouped or financially reconciled'}

@app.get('/api/market/imported-deals')
def imported_deals():return rows('SELECT id,symbol,imported_at FROM imported_fills ORDER BY imported_at DESC LIMIT 200')

@app.get('/api/backtest')
def backtest(symbol:str='EURUSD',timeframe:str='M5',source:str='DEMO',target_r:float=Query(2.0,ge=1,le=5),
             start:int|None=None,end:int|None=None,tick_dataset_id:str|None=None,
             slippage_bps:Decimal=Query(Decimal(0),ge=0,le=100),
             commission_bps:Decimal=Query(Decimal(0),ge=0,le=100),
             max_quote_gap_seconds:int=Query(60,ge=1,le=60)):
    m=market_candles(symbol,timeframe,source,limit=700,start=start,end=end)
    coverage = m.get("coverage")
    if coverage and ((tick_dataset_id is None and coverage["gaps"])
                     or (start is not None and coverage["truncated"])):
        raise HTTPException(422, "Select a complete archive range of at most 700 candles for this backtest")
    try:
        if tick_dataset_id is None:
            result=breakout_backtest(m['candles'],target_r)
        else:
            dataset = one("SELECT * FROM datasets WHERE id=?", (tick_dataset_id,))
            if (not dataset or dataset["kind"] != "ticks" or dataset["symbol"] != symbol
                    or dataset["provider"] + "_SAVED" != source or dataset["feed_id"] != m.get("feed_id")
                    or dataset["price_basis"] != "BID_ASK_UNITS" or m["price_basis"] != "BID"):
                raise ValueError("Choose saved bid candles and a bid/ask tick partition from the same feed and symbol")
            if dataset["rows_count"] > 500_000:
                raise ValueError("Tick partition exceeds the 500,000 quote replay limit")
            result = replay_breakout(m["candles"], read_partition(dataset, max_rows=500_000),
                                     FRAME_MIN[timeframe]*60, target_r,
                                     slip_bps=slippage_bps, commission_bps=commission_bps,
                                     max_quote_gap_seconds=max_quote_gap_seconds)
            if coverage and coverage["gaps"]:
                result["candle_gaps"] = coverage["gaps"]
                # With no verified calendar, gaps may hide signals even if no open trade spans them.
                result.update(expectancy_r=None, expectancy_bounds_r=None, total_r=None, win_rate=None)
            result["lineage"] = {"candle_partitions":m["partitions"],
                                 "tick_partition":{"id":dataset["id"],"sha256":dataset["sha256"]}}
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        raise HTTPException(422, str(error)) from error
    result.update({'source':source,'synthetic':m['synthetic'],'symbol':symbol,'timeframe':timeframe})
    return result
