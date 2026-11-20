"""Opt-in historical producer benchmark. Run via pytest -s from the repository root."""
from scripts.verify_real_strategy_evaluation import measure

from test_historical_strategy_runs import prepared
from research_backend.app.historical_strategy_engine import publication_prefix, run_chunks
from research_backend.app.historical_strategy_inputs import verified_evidence
from research_backend.app.strategy_evaluation_sources import collect


def test_dense_historical_prefix_measurements(prepared):
    import json

    _,run,model,baseline = prepared(extra_bars=104)
    start,end = run["identity"]["evaluation_start"],run["identity"]["cutoff"]
    cutoffs = list(range(end-99*300,end+1,300))

    def independent():
        return [publication_prefix(collect(model,run["selection"],cutoff,start),cutoff)
                for cutoff in cutoffs]

    def reused():
        evidence = verified_evidence(model,run)
        return [publication_prefix(evidence,cutoff) for cutoff in cutoffs]

    assert independent()==reused()
    sizes = []
    run_chunks(run,baseline,model,lambda snapshot,*_:sizes.append(len(snapshot[0].encode())))
    report = {"cutoffs":len(cutoffs),"workload":"continuous M5, FVG lifecycle prefix using 123 canonical candles",
              "independent":measure(independent,30),"bounded_prefix":measure(reused,30),
              "max_checkpoint_bytes":max(sizes),"canonical_metrics":baseline.metrics}
    print("HISTORICAL_BENCHMARK "+json.dumps(report,sort_keys=True))
