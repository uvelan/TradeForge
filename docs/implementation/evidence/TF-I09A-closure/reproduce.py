"""Run with research_backend/tests on PYTHONPATH."""
import json
from copy import deepcopy

from test_strategy_definitions import ready_definition_v2
from research_backend.app.strategy_definitions import validate_definition

base = ready_definition_v2()
probe = {
    "type":"RULE", "id":"trigger_event", "dependency":"crt", "observation":"CRT_STATE",
    "operator":"EQ", "expected":"EXPANSION_OBSERVED", "selection":"LATEST_ELIGIBLE",
    "alias":None, "same_family_as":None,
    "evidence":{"state":"HISTORICAL", "purpose":"HISTORICAL_SIMULATION", "max_age_seconds":3600},
}
crt = deepcopy(base)
crt["trigger"]["children"] = [probe]
smt = deepcopy(base)
smt["trigger"]["children"][0].update(observation="SMT_LEADER",expected="primary")
phase = deepcopy(base)
phase["temporal"].append({"first":"trigger_event","then":"reference","relation":"AFTER"})
data = {}
for name, strategy in (("crt_trigger",crt),("smt_role",smt),("phase_inversion",phase)):
    result = validate_definition(strategy)
    data[name] = {
        "submitted":strategy["status"],"valid":result["valid"],"status":result["status"],
        "errors":result["errors"],"fingerprint":result.get("configuration_fingerprint"),
        "trigger":strategy["trigger"]["children"][0],"temporal":strategy["temporal"],
    }
print(json.dumps(data,indent=2))
