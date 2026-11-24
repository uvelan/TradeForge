import json, statistics, time, tempfile
from pathlib import Path
from research_backend.app.offline_first_exit import simulate_first_exit
from research_backend.app.offline_market_entry import SyntheticTape
from research_backend.app.strategy_definitions import ManagementPolicy
from tradeforge.catalog import Catalog
from tradeforge.database import open_database
from tradeforge.market import Simulator,Timeframe

def report(times):
    return {'median_ms':round(statistics.median(times)*1000,3),'max_ms':round(max(times)*1000,3)}

# At the maximum documented candidate tape length, use deterministic flat quotes.
# Pydantic tape parsing, disk persistence and broker access are outside the timed region.
q=1_500_000_000_000
quotes=[dict(time_ms=q+i,available_at_ms=q+i,source_order=i,bid_units=100001,ask_units=100005) for i in range(10000)]
tape=SyntheticTape.model_validate(dict(kind='SYNTHETIC_EXECUTABLE_QUOTES_V1',source_id='synthetic-perf',symbol='EURUSD',scale=100000,
                 quotes=quotes,complete_delivery_intervals=[dict(start_ms=q,end_ms=q+9999)]))
entry={'id':'bench-entry','source':{'id':'bench-intent','direction':'LONG','symbol':'EURUSD'},'fill':{'id':'bench-fill','price_units':100005,'time_ms':q,'quote':quotes[0]}}
extensions={'fixed':{},'break_even':{'break_even_at_r':1},'trailing':{'trailing_pips':5},'elapsed':{'time_exit':{'unit':'SECONDS','count':8}},'partial':{'partial_exit':{'at_r':1,'percent':25}}}
pure={}
for name,extra in extensions.items():
    policy=ManagementPolicy.model_validate(dict(initial_stop={'kind':'FIXED_PIPS','distance':10},target={'kind':'REWARD_MULTIPLE','multiple':2},**extra))
    runs=[]
    for i in range(6):
        start=time.perf_counter(); result=simulate_first_exit(entry,policy,tape,'benchmark-digest',q+9999); runs.append(time.perf_counter()-start)
    pure[name]=report(runs[1:])|{'status':result['status']}

# Catalog re-publishes an exact synthetic M1 duplicate and verifies unchanged bytes.
# Warm cache, temp SQLite+Parquet, no external provider.
with tempfile.TemporaryDirectory(prefix='tf-bench-') as d:
    root=Path(d)
    engine=open_database(root)
    try:
        c=Catalog(root,engine)
        first=c.ingest(Simulator(),'EURUSD',Timeframe.M1,60,60*10001)
        item=c.read(first,cutoff=None)['dataset']
        runs=[]
        for i in range(6):
            t=time.perf_counter()
            assert c.ingest(Simulator(),'EURUSD',Timeframe.M1,60,60*10001)==first
            runs.append(time.perf_counter()-t)
        catalog=report(runs[1:])|{'rows':item['row_count'],'parquet_bytes':(root/item['relative_path']).stat().st_size}
    finally: engine.dispose()
print(json.dumps({'samples':5,'instrument':'EURUSD','quote_count':len(quotes),'simulate_first_exit':pure,'catalog_duplicate_ingest':catalog},indent=2))
