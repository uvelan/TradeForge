"""Opt-in historical benchmark. API routes and the public worker do not use this path."""
from functools import partial

from .analysis_evidence import ArchiveSession, ResourceLimit
from .arrival_policy import SIMULATION_MODE
from .historical_strategy_engine import _run_successor_chunks, make_machine
from .historical_strategy_inputs import (
    CRT_SMT_METHOD, bounded_crt_smt, crt_smt_configuration, historical_record, mandatory_alias,
)
from .strategy_evaluation_routes import load_definition
from .strategy_evaluation_sources import collect, freeze_selection
from .strategy_evaluator import RuleGraph
from .strategy_evidence import fingerprint

OFFLINE_CUTOFFS = 800
NOV13_METHOD = "CRT_SMT_NOV13_OFFLINE_V1"
NOV13_CATALOG = "DUKASCOPY_BID_2026_11_13_V1"
NOV13_FINGERPRINT = "cdd3678ceaf2d950af43dd462da6aaf6270702fb68f16966e9a45eb859814524"
NOV13_WINDOW = (1794528000,1794535200,1794607200)


def nov13_scope(identity,definition,selection,rule):
    return (
        identity["rearm"]=="NEW_CONTEXT_FAMILY"
        and identity["context_alias"]=="crt_reference"
        and identity["max_attempts"]==32
        and (identity["warmup_start"],identity["evaluation_start"],identity["cutoff"])==NOV13_WINDOW
        and (selection["id"],selection["catalog_fingerprint"])==(NOV13_CATALOG,NOV13_FINGERPRINT)
        and definition.instruments.calendar.kind=="forex_weekly"
        and rule is not None and crt_smt_configuration(definition,selection,rule)
    )


def prepare_nov13_offline(request):
    """Pin the reviewed archive without creating or queueing a public run."""
    if (request.partitions or
            (request.selection_id,request.selection_fingerprint)!=(NOV13_CATALOG,NOV13_FINGERPRINT)):
        raise ValueError("November 13 replay requires the exact approved archive")
    definition = load_definition(request)
    selection = freeze_selection(request,definition)
    rule = mandatory_alias(definition.context,request.context_alias)
    if not nov13_scope(request.model_dump(mode="json"),definition,selection,rule):
        raise ValueError("Unsupported November 13 offline configuration")
    return historical_record(request,definition,selection,rule,NOV13_METHOD,OFFLINE_CUTOFFS)


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
    method = identity["method"]
    selection = run["selection"]
    allowed = (
        method==CRT_SMT_METHOD and rule is not None and bounded_crt_smt(definition,selection,rule)
        or method==NOV13_METHOD and nov13_scope(identity,definition,selection,rule)
    )
    if (not allowed or run["run_id"]!="HR-"+fingerprint(identity)
            or identity["frozen_selection"]!=fingerprint(selection)
            or identity["frozen_definition"]!=fingerprint(definition.model_dump(mode="json"))):
        raise ValueError("Offline replay requires a pinned CRT/SMT configuration")
    schedule = make_machine(run,evidence,definition).boundaries(identity["cutoff"])
    if len(schedule)>OFFLINE_CUTOFFS:
        raise ResourceLimit("Offline replay exceeds its 800-cutoff ceiling")
    archive = archive if archive is not None else ReplayArchive()
    _run_successor_chunks(run,evidence,definition,publish,schedule,OFFLINE_CUTOFFS,
                          saved=saved,chunk_size=chunk_size,check=check,
                          collector=partial(collect,replay_archive=archive),observe=observe)
