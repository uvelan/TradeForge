"""Read-only MT5 adapter and visibly synthetic demo series."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from math import sin, cos
from tradeforge.market import MT5_LOCK as _LOCK
from .arrival_policy import UNKNOWN_TIMESTAMP_DOMAIN
from .schemas import Symbol

BASE={'EURUSD':1.0862,'GBPUSD':1.2728,'USDJPY':151.35,'XAUUSD':2651.0}
STEP={'EURUSD':0.000055,'GBPUSD':0.000065,'USDJPY':0.009,'XAUUSD':0.32}
FRAME_MIN={'M1':1,'M3':3,'M5':5,'M15':15,'M30':30,'H1':60,'H4':240,'D1':1440}


def demo_candles(symbol:Symbol, timeframe:str='M5', limit:int=180):
    if timeframe not in FRAME_MIN: raise ValueError('Unsupported timeframe')
    if symbol not in BASE: raise ValueError('Unsupported symbol')
    limit=min(max(limit,10),800)
    minutes=FRAME_MIN[timeframe]
    today=datetime.now(timezone.utc)
    # Fixed historical anchor, never a simulated live quote.
    end=datetime(2026,11,13,20,0,tzinfo=timezone.utc)
    bars=[]; last=BASE[symbol]
    for i in range(limit):
        t=end-timedelta(minutes=(limit-i)*minutes)
        change=(sin(i*0.36+len(symbol))*1.3+cos(i*.11)*.9+sin(i*.057)*1.8)*STEP[symbol]
        start=last; close=round(start+change, 3 if symbol=='XAUUSD' else 5 if symbol!='USDJPY' else 3)
        wick=(.7+(i%5)*.2)*STEP[symbol]
        high=max(start,close)+wick; low=min(start,close)-wick
        bars.append({'time':int(t.timestamp()),'open':round(start,5),'high':round(high,5),'low':round(low,5),'close':close,'volume':75+i%35})
        last=close
    return bars


def demo_reference_candles(symbol, timeframe, start, end):
    # Keep the expanded chart's published 600-bar path and extend it backwards
    # using the same deterministic model. Existing chart responses stay fixed.
    recent = demo_candles(symbol, timeframe, 600)
    step = FRAME_MIN[timeframe] * 60
    first = recent[0]["time"]
    if start < first - 20_160 * step:
        return []
    decimal_places = 3 if symbol in {"USDJPY", "XAUUSD"} else 5
    close = recent[0]["open"]
    earlier = []
    for stamp in range(first - step, start - 1, -step):
        index = (stamp - first) // step
        change = (sin(index * .36 + len(symbol)) * 1.3 + cos(index * .11) * .9
                  + sin(index * .057) * 1.8) * STEP[symbol]
        opening = round(close - change, decimal_places)
        wick = (.7 + index % 5 * .2) * STEP[symbol]
        if stamp < end:
            earlier.append({"time": stamp, "open": opening, "close": close,
                            "high": round(max(opening, close) + wick, 5),
                            "low": round(min(opening, close) - wick, 5)})
        close = opening
    return list(reversed(earlier)) + [bar for bar in recent if start <= bar["time"] < end]


def mt5_connection():
    try:
        import MetaTrader5 as mt5
    except ImportError:
        return {'connected':False,'reason':'MetaTrader5 Python package not installed (Windows only)'}
    with _LOCK:
        if not mt5.initialize():
            return {'connected':False,'reason':f'MT5 terminal not connected: {mt5.last_error()}'}
        account=mt5.account_info()
        if account is None:
            return {'connected':False,'reason':'MT5 connected but account unavailable'}
        return {'connected':True,'broker':str(account.server),'currency':str(account.currency),
                'mode':'READ_ONLY','account_redacted':True}


def mt5_symbols():
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MT5 Python package is unavailable") from exc
    with _LOCK:
        if not mt5.initialize(timeout=2500):
            raise RuntimeError("MT5 terminal unavailable")
        instruments = mt5.symbols_get()
        if instruments is None:
            raise RuntimeError("MT5 symbol discovery failed")
        forex_modes = {mt5.SYMBOL_CALC_MODE_FOREX, mt5.SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE}
        selected_markets = {"USTEC", "USTECH100M", "XAUUSD", "XAGUSD"}
        return sorted(
            ({"name": item.name, "description": item.description, "digits": item.digits}
             for item in instruments
             if item.name.upper() in selected_markets
             or (item.trade_calc_mode in forex_modes and item.currency_base and item.currency_profit
                 and item.currency_base != item.currency_profit)),
            key=lambda item: item["name"].casefold(),
        )


def mt5_rates(symbol:str,timeframe:str,limit:int=300,*,include_latest:bool=False,provenance:dict | None=None, window:tuple[int,int] | None=None):
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError('MT5 Python package is unavailable on this system') from exc
    if not symbol.strip() or len(symbol) > 64 or timeframe not in FRAME_MIN:
        raise ValueError('Unsupported symbol/timeframe')
    with _LOCK:
        if not mt5.initialize(): raise RuntimeError(f'MT5 initialization failed: {mt5.last_error()}')
        if not mt5.symbol_select(symbol, True): raise RuntimeError('Symbol unavailable in MT5 terminal')
        interval = getattr(mt5, 'TIMEFRAME_' + timeframe)
        if window is None:
            rates = mt5.copy_rates_from_pos(symbol, interval, 0, limit if include_latest else limit + 1)
        else:
            start, end = window
            rates = mt5.copy_rates_range(symbol, interval, datetime.fromtimestamp(start, timezone.utc),
                                        datetime.fromtimestamp(end - 1, timezone.utc))
        if rates is None: raise RuntimeError(f'MT5 bar request failed: {mt5.last_error()}')
        if provenance is not None:
            account = mt5.account_info()
            instrument = mt5.symbol_info(symbol)
            if account and account.server and instrument and instrument.chart_mode in (0, 1):
                provenance["timestamp_policy"] = UNKNOWN_TIMESTAMP_DOMAIN
                provenance["feed_id"] = f"mt5:{account.server}"
                provenance["price_basis"] = "BID" if instrument.chart_mode == 0 else "LAST_TRADE"
        # Exclude the newest bar, which may be incomplete.
        candles=[{'time':int(r['time']),'open':float(r['open']),'high':float(r['high']),
                  'low':float(r['low']),'close':float(r['close']),'volume':int(r['tick_volume'])} for r in rates]
        return candles if include_latest or window is not None else sorted(candles,key=lambda bar:bar["time"])[:-1]


def mt5_reference_candles(symbol, timeframe, start, end, provenance):
    return mt5_rates(symbol, timeframe, provenance=provenance, window=(start, end))


def mt5_recent_deals(days:int=30):
    try: import MetaTrader5 as mt5
    except ImportError as exc: raise RuntimeError('MT5 unavailable') from exc
    with _LOCK:
        if not mt5.initialize(): raise RuntimeError(f'MT5 terminal disconnected: {mt5.last_error()}')
        end=datetime.now(timezone.utc)
        deals=mt5.history_deals_get(end-timedelta(days=days),end)
        if deals is None: raise RuntimeError(f'MT5 history unavailable: {mt5.last_error()}')
        acct=mt5.account_info()
        broker_key=sha256(f'{acct.login}:{acct.server}'.encode()).hexdigest()[:24]
        values=[]
        for d in deals:
            v=d._asdict()
            values.append({'broker_key':broker_key,'deal_id':str(v['ticket']),'symbol':str(v.get('symbol') or 'UNKNOWN'),
                'payload':{k:(str(value) if not isinstance(value,(int,float,str,bool,type(None))) else value) for k,value in v.items()}})
        return values


def mt5_quote(symbol: str) -> dict:
    """Read-only, serialized access to a live MT5 tick. Never synthesize fallback quotes."""
    if not symbol.strip() or len(symbol) > 64:
        raise ValueError('Unsupported instrument')
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError('MT5 Python package is unavailable on this system') from exc
    with _LOCK:
        if not mt5.initialize():
            raise RuntimeError(f'MT5 initialization failed: {mt5.last_error()}')
        if not mt5.symbol_select(symbol, True):
            raise RuntimeError('Symbol unavailable in MT5 terminal')
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError('MT5 quote unavailable')
        bid, ask = float(tick.bid), float(tick.ask)
        if bid <= 0 or ask < bid:
            raise RuntimeError('MT5 returned invalid bid/ask prices')
        as_of = datetime.fromtimestamp(tick.time, timezone.utc)
        age = (datetime.now(timezone.utc) - as_of).total_seconds()
        return {'source':'MT5','synthetic':False,'symbol':symbol,'bid':bid,'ask':ask,
            'as_of':as_of.isoformat(),'stale':age > 30 or age < -5}
