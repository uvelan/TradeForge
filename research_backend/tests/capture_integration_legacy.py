"""Run explicitly with the reviewed Git revision available in the local clone."""
import subprocess
import sys
from types import ModuleType

import pytest

from research_backend.app.db import transaction
from test_bounded_crt_smt_successors import canonical_successors
from test_offline_combined_management import closing
from test_offline_first_exit import ENTRY_SHORT, calculate, position
from test_offline_market_entry import Q, publish, tape, validate

REVIEWED = "3fcd2ec9c00101d0bc09ebac52ebe7581fb662d0"


def load_reviewed(name):
    path = f"research_backend/app/{name}.py"
    code = subprocess.check_output(["git","show",f"{REVIEWED}:{path}"])
    module = ModuleType("research_backend.app."+name)
    module.__package__ = "research_backend.app"
    exec(compile(code,path,"exec"),module.__dict__)
    return module


@pytest.mark.parametrize("management",[
    {"break_even_at_r":1},{"trailing_pips":5},{"break_even_at_r":1,"trailing_pips":5},
])
def test_two_service_versions_one_database(position,monkeypatch,management):
    run,intent = position("bearish",management=management)
    data = tape([ENTRY_SHORT,closing(False,4,4,7,110206),closing(False,3,5,8,110311)])
    entry, = validate(run,data,intent["id"])["results"]
    with monkeypatch.context() as legacy:
        legacy.setitem(sys.modules,"research_backend.app.offline_partial_exit",load_reviewed("offline_partial_exit"))
        old = load_reviewed("offline_first_exit")
        prior, = old.validate_first_exit(run,entry_result_id=entry["id"],tape=data,
                                        tape_sha256=entry["identity"]["execution_data_fingerprint"],
                                        cutoff_ms=Q+5)["results"]
    def payloads():
        with transaction() as db:
            return dict(db.execute("SELECT id,CAST(payload AS BLOB) FROM offline_first_exit_results"))
    stored = payloads()
    assert prior["status"]==("UNSUPPORTED_EXIT_MANAGEMENT" if len(management)>1 else "SIMULATED_EXIT")
    after, = calculate(run,data,entry,Q+5)["results"]
    assert after["status"]=="OPEN_AT_CUTOFF" and after["id"]!=prior["id"]
    assert calculate(run,data,entry,Q+5)["results"]==[after]
    rows = payloads()
    assert set(rows)=={prior["id"],after["id"]} and rows[prior["id"]]==stored[prior["id"]]


@pytest.mark.parametrize("direction",["bullish","bearish"])
@pytest.mark.parametrize("case",["off_grid","open","partial","terminal"])
def test_partial_only_v1_replays_original_bytes(position,monkeypatch,direction,case):
    from research_backend.app.analysis_evidence import canonical
    from test_offline_first_exit import ENTRY_LONG

    long = direction=="bullish"
    settings = {"initial_stop":{"kind":"FIXED_PIPS","distance":10},
                "target":{"kind":"REWARD_MULTIPLE","multiple":3},
                "partial_exit":{"at_r":1.333 if case=="off_grid" else 1.5,"percent":25}}
    run,intent = position(direction,management=settings)
    data = tape([ENTRY_LONG if long else ENTRY_SHORT,
                 closing(long,3,3,6,110464 if long else 110156),
                 closing(long,4,4,7,110210 if long else 110410)])
    entry, = validate(run,data,intent["id"])["results"]
    cutoff = Q+{"off_grid":4,"open":2,"partial":3,"terminal":4}[case]
    with monkeypatch.context() as legacy:
        legacy.setitem(sys.modules,"research_backend.app.offline_partial_exit",load_reviewed("offline_partial_exit"))
        old = load_reviewed("offline_first_exit")
        response = old.validate_first_exit(run,entry_result_id=entry["id"],tape=data,
                                           tape_sha256=entry["identity"]["execution_data_fingerprint"],cutoff_ms=cutoff)
    prior, = response["results"]
    assert prior["identity"]["policy"]["id"]=="OFFLINE_PARTIAL_EXIT"
    assert prior["identity"]["policy"]["version"]==1
    assert prior["status"]=={"off_grid":"UNREPRESENTABLE_EXIT_LEVELS","open":"OPEN_AT_CUTOFF",
                             "partial":"OPEN_AT_CUTOFF","terminal":"SIMULATED_EXIT"}[case]
    assert response["simulated_partial_exits"]==int(case in ("partial","terminal"))
    assert response["simulated_exits"]==int(case=="terminal")

    def persisted():
        with transaction() as db:
            return [tuple(row) for row in db.execute("SELECT id,CAST(payload AS BLOB) FROM offline_first_exit_results")]
    original = persisted()
    assert original==[(prior["id"],canonical(prior).encode())]
    for _ in range(2):
        result = calculate(run,data,entry,cutoff)
        assert result==response
        assert canonical(result["results"][0]).encode()==original[0][1]
        assert persisted()==original
