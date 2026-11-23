"""Opt-in historical benchmark. API routes and the public worker do not use this path."""
from functools import partial

from .analysis_evidence import ArchiveSession, ResourceLimit
from .arrival_policy import SIMULATION_MODE
from .historical_strategy_engine import _run_successor_chunks, make_machine
from .historical_strategy_inputs import CRT_SMT_METHOD, bounded_crt_smt
from .strategy_evaluation_sources import collect
from .strategy_evaluator import RuleGraph

OFFLINE_CUTOFFS = 800


class ReplayArchive:
    """One replay owns immutable verified rows and their normalized representations."""

    def __init__(self):
        self.archive = ArchiveSession()
        self.bars = {}

    def read(self, part, reader):
        return self.archive.read(part,reader)

    def simulated_bar(self, raw, step, delay, normalize):
        key = id(raw),step,delay
        if key not in self.bars:
            if len(self.bars)>=8000:
                raise ResourceLimit("Offline normalized archive budget exceeded")
            # Retain the raw row as well so its object identity cannot be recycled.
            self.bars[key] = raw,normalize(raw,step,SIMULATION_MODE,None,delay)
        return self.bars[key][1]


def run_offline(run,evidence,definition,publish,*,archive=None,saved=None,chunk_size=32,
                check=lambda:None,observe=None):
    identity = run["identity"]
    rule = RuleGraph(definition).rules.get(run["rearm_rule"])
    if identity["method"]!=CRT_SMT_METHOD or rule is None or not bounded_crt_smt(definition,run["selection"],rule):
        raise ValueError("Offline replay requires the pinned bounded CRT/SMT configuration")
    schedule = make_machine(run,evidence,definition).boundaries(identity["cutoff"])
    if len(schedule)>OFFLINE_CUTOFFS:
        raise ResourceLimit("Offline replay exceeds its 800-cutoff ceiling")
    archive = archive if archive is not None else ReplayArchive()
    _run_successor_chunks(run,evidence,definition,publish,schedule,OFFLINE_CUTOFFS,
                          saved=saved,chunk_size=chunk_size,check=check,
                          collector=partial(collect,replay_archive=archive),observe=observe)
