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
from pydantic import BaseModel,Field, model_validator
import httpx
from . import binance
from .db import initialize, rows, one, transaction, utc_now
from .market import mt5_connection, mt5_rates, mt5_recent_deals,mt5_quote,mt5_symbols,demo_candles,BASE,FRAME_MIN
from .storage import write_partition, decode_bi5, aggregate_ticks,read_partition
from .domain import create_trade,get_trade,revise,decline_trade,close_trade,list_trades,analytics,export_ai,import_ai,journal_add,journal_list,journal_materialize
from .risk import evaluate
from .backtest import breakout_backtest
from .tick_replay import replay_breakout, validate_bid_extremes
from .schemas import TradePlan,Outcome,ReviewInput,ReviewV1,JournalInput,RiskInput,SignalReference
from . import domain
from .drawings import router as drawing_router
from .workspace_routes import router as workspace_router
from .indicator_routes import router as indicator_router, run_smt_monitor, signal_candles, signal_revision
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
app.include_router(drawing_router)
app.include_router(workspace_router)
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
def update_plan(uid:str,plan:TradePlan,expected_version:int|None=Query(None,ge=1)):
    return revise(uid,plan,expected_version)


class NoTrade(BaseModel):
    reason:str = Field(min_length=5,max_length=5000)


@app.post('/api/trades/{uid}/decline')
def no_trade(uid:str,decision:NoTrade):
    if len(decision.reason.strip())<5:
        raise HTTPException(422,"Provide the reason for passing on this trade")
    return decline_trade(uid,decision.reason.strip())


@app.post('/api/trades/{uid}/close')
def record_outcome(uid:str,outcome:Outcome):return close_trade(uid,outcome)


@app.post('/api/risk/evaluate')
def risk(inp:RiskInput):return evaluate(inp.plan)


@app.get('/api/analytics')
def stats():return analytics()


@app.post('/api/ai/exports/{uid}',status_code=201)
def create_export(uid:str, plan_version:int|None=Query(None,ge=1)):return export_ai(uid,plan_version)


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


@app.get('/api/datasets/{dataset_id}/coverage')
def dataset_coverage(dataset_id:str):
    dataset = one("SELECT * FROM datasets WHERE id=? AND kind='candles'", (dataset_id,))
    if not dataset:
        raise HTTPException(404, "Candle partition unavailable")
    try:
        step = FRAME_MIN[dataset["timeframe"]] * 60
        values = read_partition(dataset)
        first = int(datetime.fromisoformat(dataset["first_time"]).timestamp())
        end = int(datetime.fromisoformat(dataset["last_time"]).timestamp()) + step
        if not values:
            raise ValueError("Candle partition is empty")
        gaps, duplicates = [], 0
        truncated = dataset["rows_count"] > len(values)
        cursor = values[0]["time"] if truncated else first
        previous = None
        for candle in values:
            timestamp = candle["time"]
            if timestamp % step or not first <= timestamp < end or timestamp < cursor - step:
                raise ValueError("Candle partition timestamps do not match its registered interval")
            if timestamp == cursor - step:
                if previous != candle:
                    raise ValueError("Conflicting duplicate candles")
                duplicates += 1
                continue
            if timestamp > cursor:
                gaps.append({"start": cursor, "end": timestamp})
            cursor = timestamp + step
            previous = candle
        if cursor < end:
            gaps.append({"start": cursor, "end": end})
        return {"source":dataset["provider"]+"_SAVED","synthetic":False,"symbol":dataset["symbol"],
                "timeframe":dataset["timeframe"],"feed_id":dataset["feed_id"],"price_basis":dataset["price_basis"],
                "candles":values[-1:], "partitions":[{"id":dataset["id"],"sha256":dataset["sha256"]}],
                "coverage":{"start":first,"end":end,"gaps":gaps,"duplicate_bars":duplicates,
                            "truncated":truncated}}
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        raise HTTPException(409, f"UNTRUSTED: {error}") from error


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
    return calculate_backtest(BacktestRequest(
        symbol=symbol,timeframe=timeframe,source=source,target_r=target_r,start=start,end=end,
        tick_dataset_id=tick_dataset_id,slippage_bps=slippage_bps,commission_bps=commission_bps,
        max_quote_gap_seconds=max_quote_gap_seconds,
    ))["result"]


class BacktestRequest(BaseModel):
    symbol: str = Field(default="EURUSD", max_length=64)
    timeframe: str = Field(default="M5", max_length=4)
    source: str = Field(default="DEMO", max_length=30)
    target_r: float = Field(default=2, ge=1, le=5)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    tick_dataset_id: str | None = Field(default=None,max_length=100)
    slippage_bps: Decimal = Field(default=Decimal(0),ge=0,le=100)
    commission_bps: Decimal = Field(default=Decimal(0),ge=0,le=100)
    max_quote_gap_seconds: int = Field(default=60,ge=1,le=60)


def calculate_backtest(request: BacktestRequest):
    symbol,timeframe,source = request.symbol,request.timeframe,request.source
    start,end,target_r = request.start,request.end,request.target_r
    tick_dataset_id = request.tick_dataset_id
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
            ticks = read_partition(dataset, max_rows=500_000)
            step = FRAME_MIN[timeframe] * 60
            validate_bid_extremes(m["candles"], ticks, step)
            result = replay_breakout(m["candles"], ticks, step, target_r,
                                     slip_bps=request.slippage_bps, commission_bps=request.commission_bps,
                                     max_quote_gap_seconds=request.max_quote_gap_seconds)
            if coverage and coverage["gaps"]:
                result["candle_gaps"] = coverage["gaps"]
                # With no verified calendar, gaps may hide signals even if no open trade spans them.
                result.update(expectancy_r=None, expectancy_bounds_r=None, total_r=None, win_rate=None)
            result["lineage"] = {"candle_partitions":m["partitions"],
                                 "tick_partition":{"id":dataset["id"],"sha256":dataset["sha256"]}}
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        raise HTTPException(422, str(error)) from error
    result.update({'source':source,'synthetic':m['synthetic'],'symbol':symbol,'timeframe':timeframe})
    return {"schema_version":1,"engine_version":1,"parameters":request.model_dump(mode="json"),
            "market":m,"result":result}


def run_summary(row: dict) -> dict:
    return {"id": row["id"],"created_at":row["created_at"],**json.loads(row["summary"])}


@app.post('/api/backtest/runs',status_code=201)
def save_backtest(request:BacktestRequest):
    snapshot = calculate_backtest(request)
    encoded = json.dumps(snapshot,sort_keys=True,separators=(",",":"),allow_nan=False)
    run_id = sha256(encoded.encode()).hexdigest()
    result = snapshot["result"]
    summary = {key:result[key] for key in ("count","expectancy_r","source","timeframe","symbol","synthetic")}
    summary["model"] = "TICK" if request.tick_dataset_id else "OHLC"
    with transaction() as db:
        db.execute("INSERT INTO backtest_runs VALUES (?,?,?,?) ON CONFLICT(id) DO NOTHING",
                   (run_id,json.dumps(summary),encoded,utc_now()))
    return load_backtest(run_id)


@app.get('/api/backtest/runs')
def backtest_runs(limit:int=Query(40,ge=1,le=100)):
    return [run_summary(row) for row in rows("SELECT id,summary,created_at FROM backtest_runs ORDER BY created_at DESC,id LIMIT ?",(limit,))]


@app.get('/api/backtest/runs/{run_id}')
def load_backtest(run_id:str):
    row = one("SELECT * FROM backtest_runs WHERE id=?", (run_id,))
    if row is None:
        raise HTTPException(404,"Backtest run unavailable")
    if sha256(row["payload"].encode()).hexdigest() != run_id:
        raise HTTPException(409,"Saved run checksum mismatch")
    return {"id":run_id,"created_at":row["created_at"],**json.loads(row["payload"])}


@app.get('/api/backtest/runs/{run_id}/trades/{trade_index}/quotes')
def run_quotes(run_id:str,trade_index:int,offset:int=Query(0,ge=0,le=500_000),limit:int=Query(250,ge=1,le=2000)):
    snapshot = load_backtest(run_id)
    result = snapshot["result"]
    if not 0<=trade_index<len(result["trades"]):
        raise HTTPException(404,"Trade not found in run")
    if "execution_model" not in result:
        raise HTTPException(409,"This OHLC run has no recorded quote path")
    trade = result["trades"][trade_index]
    partition = result["lineage"]["tick_partition"]
    row = one("SELECT * FROM datasets WHERE id=? AND kind='ticks'", (partition["id"],))
    if row is None or row["sha256"] != partition["sha256"] or row["rows_count"] > 500_000:
        raise HTTPException(409,"Original quote partition unavailable")
    try:
        quotes = read_partition(row,max_rows=500_000)
    except (RuntimeError,OSError,ValueError) as error:
        raise HTTPException(409,f"Original quote partition failed verification: {error}") from error
    start = (trade["entry_time_ms"],trade["entry_source_order"])
    end = (trade["exit_time_ms"],trade["exit_source_order"])
    path = sorted((q for q in quotes if start <= (q["time_ms"],q["source_order"]) <= end),
                  key=lambda quote:(quote["time_ms"],quote["source_order"]))
    return {"items":path[offset:offset+limit],"total":len(path),
            "next_offset":offset+limit if offset+limit < len(path) else None}


class ReplayView(BaseModel):
    view: str = Field(pattern=r"^(comparison|[234])$")
    symbol: str = Field(min_length=1,max_length=64)
    timeframe: str = Field(min_length=2,max_length=4)


class ReplayRequest(BaseModel):
    symbol: str = Field(min_length=1,max_length=64)
    timeframe: str = Field(min_length=2,max_length=4)
    source: str = Field(max_length=30)
    start: int | None = Field(None,ge=0)
    end: int | None = Field(None,ge=0)
    views: list[ReplayView] = Field(default_factory=list,max_length=4)
    signal_reference: SignalReference | None = None
    @model_validator(mode="after")
    def paired_range(self):
        if (self.start is None) != (self.end is None) or self.start is not None and self.start>=self.end:
            raise ValueError("Replay range requires an ordered start and end")
        if len({view.view for view in self.views}) != len(self.views):
            raise ValueError("Duplicate replay view")
        return self


class ReplayCheckpoint(BaseModel):
    position:int = Field(ge=0,lt=2000)
    bookmarks:list[int] = Field(default_factory=list,max_length=50)


def captured_replay(request:ReplayRequest,symbol:str,timeframe:str,cutoff:int):
    if timeframe not in FRAME_MIN:
        raise HTTPException(422,"Unsupported replay timeframe")
    step = FRAME_MIN[timeframe]*60
    start,end=request.start,request.end
    if start is not None:
        start = start//step*step
        end = (end+step-1)//step*step
        end = min(end, cutoff//step*step)
        if end <= start:
            raise HTTPException(422,"No completed window for this replay timeframe")
    market=market_candles(symbol,timeframe,request.source,limit=600,include_latest=(request.source=="MT5"),
                          start=start,end=end)
    bars=[{**bar,"available_at":bar.get("available_at",bar["time"]+step)} for bar in market["candles"]
          if bar.get("available_at",bar["time"]+step)<=cutoff]
    if any(right["time"]<=left["time"] or right["available_at"]<left["available_at"]
           for left,right in zip(bars,bars[1:])):
        raise HTTPException(409,"Replay candle timestamps are inconsistent")
    return {**market,"candles":bars,"latest_unconfirmed":False,"symbol":symbol,"timeframe":timeframe}


@app.post('/api/replay/sessions',status_code=201)
def create_replay(request:ReplayRequest):
    clock = int(datetime.now(timezone.utc).timestamp())
    if request.source not in {"DEMO","MT5","MT5_SAVED","DUKASCOPY_SAVED"}:
        raise HTTPException(422,"Replay requires a supported closed-candle source")
    panes = {}
    if request.signal_reference:
        reference=request.signal_reference
        event=signal_revision(reference.event_key,reference.status)
        provenance=event["provenance"]
        primary,comparison=provenance["primary"],provenance["comparison"]
        if (request.symbol,request.timeframe,request.source)!=(primary["symbol"],provenance["timeframe"],primary["source"]):
            raise HTTPException(422,"Replay context differs from linked signal")
        if request.views or request.start is not None:
            raise HTTPException(422,"Recorded signal replay uses its original capture boundaries")
        captures=signal_candles(reference.event_key,reference.status)
        def pane(side:dict, bars:list[dict]):
            return {**side,"candles":bars,"timeframe":request.timeframe,"synthetic":False,"latest_unconfirmed":False}
        market=pane(primary,captures["primary"])
        panes["comparison"]=pane(comparison,captures["comparison"])
    else:
        market = captured_replay(request,request.symbol,request.timeframe,clock)
        if market.get("coverage",{}).get("truncated") and request.start is not None:
            raise HTTPException(422,"Choose a replay range of at most 600 primary candles")
        for view in request.views:
            try:
                panes[view.view] = captured_replay(request,view.symbol,view.timeframe,clock)
            except HTTPException as error:
                if error.status_code not in (404,409,422,503):
                    raise
                panes[view.view] = {"symbol":view.symbol,"timeframe":view.timeframe,"error":str(error.detail)}
    if not market["candles"]:
        raise HTTPException(422,"No completed replay candles")
    snapshot = {"schema_version":1,"request":request.model_dump(),"market":market,"views":panes}
    encoded=json.dumps(snapshot,sort_keys=True,separators=(",",":"),allow_nan=False)
    session_id=sha256(encoded.encode()).hexdigest()
    with transaction() as db:
        db.execute("INSERT INTO replay_sessions VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                   (session_id,request.symbol,request.timeframe,request.source,encoded,utc_now()))
    return replay_session(session_id)


@app.get('/api/replay/sessions')
def list_replays(symbol:str=Query(max_length=64),timeframe:str=Query(max_length=4),source:str=Query(max_length=30)):
    return rows("""SELECT session.id,session.created_at,checkpoint.position,checkpoint.updated_at FROM replay_sessions session
                 JOIN replay_checkpoints checkpoint ON checkpoint.session_id=session.id
                 WHERE session.symbol=? AND session.timeframe=? AND session.source=?
                 ORDER BY checkpoint.updated_at DESC,session.id LIMIT 20""",(symbol,timeframe,source))


@app.get('/api/replay/sessions/{session_id}')
def replay_session(session_id:str):
    row=one("SELECT * FROM replay_sessions WHERE id=?",(session_id,))
    if not row:
        raise HTTPException(404,"Replay session unavailable")
    if sha256(row["payload"].encode()).hexdigest()!=session_id:
        raise HTTPException(409,"Replay session checksum mismatch")
    checkpoint=one("SELECT position,bookmarks FROM replay_checkpoints WHERE session_id=?",(session_id,))
    return {"id":session_id,**json.loads(row["payload"]),
            "checkpoint":{"position":checkpoint["position"],"bookmarks":json.loads(checkpoint["bookmarks"])}
            if checkpoint else None}


@app.post('/api/replay/sessions/{session_id}/checkpoint')
def checkpoint_replay(session_id:str,checkpoint:ReplayCheckpoint):
    session=replay_session(session_id)
    size=len(session["market"]["candles"])
    if checkpoint.position>=size or any(not 0<=index<size for index in checkpoint.bookmarks):
        raise HTTPException(422,"Replay position or bookmark is outside the frozen snapshot")
    with transaction() as db:
        db.execute("""INSERT INTO replay_checkpoints VALUES(?,?,?,?) ON CONFLICT(session_id) DO UPDATE
                      SET position=excluded.position,bookmarks=excluded.bookmarks,updated_at=excluded.updated_at""",
                   (session_id,checkpoint.position,json.dumps(sorted(set(checkpoint.bookmarks))),utc_now()))
    return {"position":checkpoint.position,"bookmarks":sorted(set(checkpoint.bookmarks))}
