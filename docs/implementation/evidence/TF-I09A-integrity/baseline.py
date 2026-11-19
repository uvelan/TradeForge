import json
from copy import deepcopy
from research_backend.tests.test_strategy_definitions import ready_definition
from research_backend.app.strategy_definitions import validate_definition
from research_backend.app.strategy_catalog import capability_catalog

base = ready_definition()
out = {}
def check(name, d):
    r = validate_definition(d)
    out[name] = {k:r[k] for k in ('valid','status','errors','unresolved')}
check('ready_without_direction_or_traded_role',base)
for intent in ('LIMIT','STOP'):
    d = deepcopy(base)
    d['execution']['entry_intent'] = intent
    check(intent.lower()+'_without_price',d)
d = deepcopy(base)
d['risk']['instrument_limits'][0]['max_risk_percent'] = 0.01
check('instrument_cap_below_intended_risk',d)
d = deepcopy(base)
d['context']['operator'] = 'ANY'
d['temporal'].append({'first':'reference','then':'reclaim','relation':'AFTER'})
check('link_into_optional_branch',d)
out['missing_semantics'] = {key:base.get(key) for key in ('trade_intent','selection','lifecycle')}
out['crt_context_and_setup'] = {k:base[k] for k in ('context','setup')}
out['smt_fields'] = [k for k in capability_catalog()['observations'] if k.startswith('SMT')]
from research_backend.tests.test_crt import calc, REF, CENTER, HIGH
report = calc([REF]*12+[CENTER]*3+[HIGH],rules={"market_schedule":{"kind":"continuous"}})
active, = report["crt_formations"]
reclaim, = report["crt_sweeps"]
out["actual_crt_transition"] = {
    "same_family":active["range_id"]==reclaim["range_id"],
    "different_event_ids":active["id"]!=reclaim["id"],
    "initial_state":active["state"],"later_state":reclaim["state"],
    "active_revision_remains_current":any(item["id"]==active["id"] for item in report["current_crt"]["items"])
}
print(json.dumps(out,indent=2))
