"""Versioned strategy-facing projections of existing canonical producer contracts."""
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, Field, StrictInt, ValidationError, create_model, FiniteFloat, model_validator

from .arrival_policy import MODE_VERSIONS, RECORDED_AUTHORITY, RECORDED_MODE, SIMULATION_AUTHORITY, SIMULATION_MODE
from .crt import CRTRules
from .fvg import FVGRules
from .liquidity import LiquidityRules
from .market_structure import StructureRules
from .order_blocks import OrderBlockRules



SCHEMA_VERSION = 1
DEFAULT_POLICY = "STRATEGY_DEFAULTS_V1"
INTEGRITY_DEFAULT_POLICY = "STRATEGY_DEFAULTS_V2"
# These numbers are contract pins. Engine changes require a new catalog version.
REFERENCE_VERSION = 1
TIMING_VERSION = 3
LIFECYCLE_VERSION = 2
BINDING_POLICY_VERSION = 2
FRAMES = ("M1", "M5")
AUTHORITIES = {SIMULATION_MODE: SIMULATION_AUTHORITY, RECORDED_MODE: RECORDED_AUTHORITY}


class SmtSettings(BaseModel, extra="forbid"):
    relationship: Literal["POSITIVE"] = "POSITIVE"
    period: StrictInt = Field(default=3, ge=2, le=20)
    alignment_bars: StrictInt = Field(default=2, ge=0, le=10)
    tolerance_bps: FiniteFloat = Field(default=0.5, ge=0, le=100)
    confirmation_bars: StrictInt = Field(default=1, ge=0, le=20)
    expiry_bars: StrictInt = Field(default=64, ge=1, le=128)

    @model_validator(mode="after")
    def ordered(self):
        if self.expiry_bars < self.confirmation_bars:
            raise ValueError("Expiry must cover confirmation bars")
        return self


MODELS = {
    "CRT": CRTRules, "SMT": SmtSettings, "STRUCTURE": StructureRules, "LIQUIDITY": LiquidityRules,
    "FVG": FVGRules, "ORDER_BLOCK": OrderBlockRules,
}
VERSIONS = {
    "CRT": (1, 1), "SMT": (2, 2), "STRUCTURE": (2, 2),
    "LIQUIDITY": (1, 1), "FVG": (1, 2), "ORDER_BLOCK": (1, 1),
}
REQUIRES = {"LIQUIDITY": ("STRUCTURE",), "ORDER_BLOCK": ("STRUCTURE",)}
INTERNAL = {"SMT": ("smt_pivot:primary", "smt_pivot:comparison", "smt_match", "smt_lifecycle"),
            "LIQUIDITY": ("pool_lifecycle",), "CRT": ("closed_reference_constituents",)}
# Literal values pin schema 1 defaults independently of future producer defaults.
DEFAULTS = {'CRT': {'reference_frame': 'H1',
         'reference_mode': 'aggregated',
         'daily_timezone': 'UTC',
         'daily_boundary': '00:00',
         'history_bars': 2000,
         'max_ranges': 2,
         'max_age': 96,
         'candidate_age': 3,
         'min_penetration': 0.0,
         'min_reclaim': 0.0,
         'invalidate': 'sweep_extreme',
         'include_context': False},
 'SMT': {'relationship': 'POSITIVE',
         'period': 3,
         'alignment_bars': 2,
         'tolerance_bps': 0.5,
         'confirmation_bars': 1,
         'expiry_bars': 64},
 'STRUCTURE': {'algorithm_version': 2,
               'left': 3,
               'right': 3,
               'price_source': 'wick',
               'ties': 'strict',
               'tolerance_bps': 0.0,
               'break_bps': 0.0,
               'wick_candidates': False,
               'history_bars': 2000},
 'LIQUIDITY': {'equality_bps': 5.0,
               'penetration_bps': 0.0,
               'reclaim_bps': 0.0,
               'min_touches': 2,
               'max_touches': 16,
               'min_separation_bars': 2,
               'max_age_bars': 250},
 'FVG': {'min_price': 0.0,
         'min_bps': 0.0,
         'min_ticks': 0,
         'tick_size': None,
         'tick_size_reference': None,
         'atr_multiple': 0.0,
         'atr_period': 14,
         'displacement': 'none',
         'min_body_bps': 0.0,
         'mitigation': 'wick',
         'max_age_bars': 250,
         'history_bars': 2000},
 'ORDER_BLOCK': {'lookback': 20,
                 'candidate_age': 20,
                 'min_body': 0.0,
                 'min_break': 0.0,
                 'min_displacement': 0.0,
                 'geometry': 'wick',
                 'allow_choch': False,
                 'include_context': False,
                 'mitigation': 'wick',
                 'min_penetration_pct': 0.0,
                 'max_age_bars': 250}}
BOUND_PARAMS = ("market_schedule", "history_start", "reference_start")


def observation(label, indicator, category, field, values=(), *, current=False, current_only=False):
    return {"label":label,"indicator":indicator,"category":category,"field":field,
            "values":list(values),"value_type":"enum" if values else "number",
            "operators":["EQ"] if values else ["EQ","LT","LTE","GT","GTE"],
            "state_requirements":(["CURRENT"] if current_only else
                                  ["HISTORICAL","CURRENT"] if current else ["HISTORICAL"])}


OBSERVATIONS = {
    "CRT_REFERENCE": observation("CRT reference active","CRT","crt","state",("ACTIVE",),current=True),
    "CRT_RECLAIM": observation("CRT confirmed sweep and reclaim","CRT","crt","state",
                               ("HIGH_SWEPT_RECLAIMED","LOW_SWEPT_RECLAIMED")),
    "CRT_STATE": observation("CRT lifecycle observation","CRT","crt","state",
                             ("EXPANSION_OBSERVED","OPPOSITE_BOUNDARY_REACHED")),
    "CRT_MIDPOINT": observation("CRT active reference midpoint","CRT","crt","midpoint",current_only=True),
    "SMT_CONFIRMED": observation("Confirmed positive-pair SMT","SMT","smt","direction",("bullish","bearish"),current=True),
    "STRUCTURE_PIVOT": observation("Confirmed structure pivot","STRUCTURE","structure","side",("HIGH","LOW")),
    "STRUCTURE_BREAK": observation("Confirmed structure break","STRUCTURE","structure","type",
                                   ("BOS","CHOCH","COUNTERTREND_BREAK")),
    "BOS_DIRECTION": observation("Confirmed BOS direction","STRUCTURE","structure","direction",("BULLISH","BEARISH")),
    "CHOCH_DIRECTION": observation("Confirmed CHOCH direction","STRUCTURE","structure","direction",("BULLISH","BEARISH")),
    "STRUCTURE_BREAK_PRICE": observation("Confirmed break price","STRUCTURE","structure","break_price"),
    "LIQUIDITY_POOL": observation("Liquidity pool state","LIQUIDITY","pool","state",("ACTIVE","SWEPT","BROKEN")),
    "LIQUIDITY_ACTIVE_POOL": observation("Currently active liquidity pool","LIQUIDITY","pool","state",("ACTIVE",),current_only=True),
    "LIQUIDITY_SWEEP": observation("Confirmed liquidity sweep or break","LIQUIDITY","liquidity","state",("SWEPT","BROKEN")),
    "FVG_ACTIVE": observation("Currently active fair value gap","FVG","fvg","state",
                             ("ACTIVE","PARTIALLY_MITIGATED"),current_only=True),
    "FVG_STATE": observation("Fair value gap lifecycle observation","FVG","fvg","state",
                             ("ACTIVE","PARTIALLY_MITIGATED","FULLY_MITIGATED")),
    "ORDER_BLOCK_ACTIVE": observation("Currently active order block","ORDER_BLOCK","order_block","state",
                                     ("ACTIVE","TOUCHED","PARTIALLY_MITIGATED"),current_only=True),
    "ORDER_BLOCK_STATE": observation("Order block lifecycle observation","ORDER_BLOCK","order_block","state",
                                     ("ACTIVE","TOUCHED","PARTIALLY_MITIGATED","FULLY_MITIGATED")),
}
# These selectors narrow a canonical event family before comparing a published field.
for name, kind in (("BOS_DIRECTION","BOS"),("CHOCH_DIRECTION","CHOCH")):
    OBSERVATIONS[name]["where"] = {"type":kind}
OBSERVATIONS["STRUCTURE_PIVOT"]["where"] = {"type":"PIVOT"}
OBSERVATIONS["STRUCTURE_BREAK_PRICE"]["where"] = {"type":["BOS","CHOCH","COUNTERTREND_BREAK"]}
OBSERVATIONS["CRT_MIDPOINT"]["where"] = {"state":"ACTIVE"}

ROLE_OBSERVATIONS = {
    "SMT_LEADER": observation("Confirmed SMT leader", "SMT", "smt", "leader",
                              ("primary", "comparison"), current=True),
    "SMT_LAGGARD": observation("Confirmed SMT laggard", "SMT", "smt", "leader",
                               ("primary", "comparison"), current=True),
}
for spec in ROLE_OBSERVATIONS.values():
    spec["where"] = {"state": "SMT_CONFIRMED"}
    spec["requires_bound_observation"] = "SMT_CONFIRMED"
ROLE_OBSERVATIONS["SMT_LAGGARD"]["projection"] = "OPPOSITE_PAIR_ROLE"

FAMILY_CATEGORIES = frozenset({"crt", "smt", "pool", "fvg", "order_block"})

UNSUPPORTED = {
    "SESSION_MEMBERSHIP":"Calendar is a mandatory scope gate. Session charts do not publish a canonical session condition.",
    "PREVIOUS_PERIOD_LEVEL":"Chart projections are not canonical strategy evidence.",
    "STRUCTURE_TREND":"The latest structure-state projection is not a registry-attested event.",
    "ATR_FVG":"The ATR dependency cannot be pinned by the strategy evidence registry.",
    "INVERSE_SMT":"Only the positive EURUSD/GBPUSD pair is supported.",
    "DATA_READINESS":"Mandatory strategy gate, not an optional condition.",
    "EVIDENCE_ELIGIBILITY":"Mandatory registry gate, not an optional condition.",
    "SMT_CANDIDATE_OR_ABSENCE":"Unfinished or incomplete matching cannot establish a negative condition.",
    "CRT_NATIVE_OR_OTHER_HTF":"Schema 1 admits aggregated H1 from M5 with complete coverage.",
    "ARBITRARY_EXPRESSION":"The schema accepts only typed operators and canonical fields.",
}


def effective_settings(indicator, supplied, calendar):
    model = MODELS[indicator]
    allowed = set(DEFAULTS[indicator])
    extras = set(supplied) - allowed
    if extras:
        raise ValueError("Unknown or evaluation-bound settings: " + ", ".join(sorted(extras)))
    normalized = {**DEFAULTS[indicator],**supplied}
    if "market_schedule" in model.model_fields:
        normalized["market_schedule"] = calendar
    result = model.model_validate(normalized,strict=True).model_dump(mode="json")
    if indicator == "CRT" and (result["reference_frame"] != "H1" or result["reference_mode"] != "aggregated"
                               or result["include_context"]):
        raise ValueError("Use aggregated H1 CRT with independent strategy context conditions")
    if indicator == "STRUCTURE" and result["algorithm_version"] != 2:
        raise ValueError("Structure requires algorithm version 2")
    if indicator == "FVG" and result["atr_multiple"] != 0:
        raise ValueError("ATR-derived FVG cannot satisfy an evidence condition")
    if indicator == "ORDER_BLOCK" and result["include_context"]:
        raise ValueError("Declare context as independent strategy conditions")
    if "history_bars" in result and result["history_bars"] != 2000:
        raise ValueError("Extended archive windows are bound by the future evaluator")
    effective = {key:value for key,value in result.items() if key not in BOUND_PARAMS}
    if set(effective) != allowed:
        raise ValueError("Producer parameter shape has changed since this default policy was pinned")
    return effective


def capability_catalog(version=1):
    observations = {**OBSERVATIONS, **ROLE_OBSERVATIONS} if version == 2 else OBSERVATIONS
    indicators = {}
    for key, model in MODELS.items():
        settings = model.model_json_schema()["properties"]
        indicators[key] = {
            "algorithm_version":VERSIONS[key][0],"identity_policy_version":VERSIONS[key][1],
            "timing_policy_version":TIMING_VERSION,"evidence_reference_version":REFERENCE_VERSION,
            "timeframes":["M5"] if key=="CRT" else list(FRAMES),
            "reference_timeframes":["H1"] if key=="CRT" else [],
            "identity_policy": {"STRUCTURE":"STRUCTURE_V2_STRICT_PIVOT_AND_BREAK",
                                "SMT":"SMT_V2_MATCH_AND_LIFECYCLE",
                                "LIQUIDITY":"LIQUIDITY_V1_POOL_LIFECYCLE"}.get(key,key+"_CANONICAL"),
            "roles":["PAIR"] if key=="SMT" else ["PRIMARY","COMPARISON"],
            "parameters":{name:spec for name,spec in settings.items() if name in DEFAULTS[key]},
            "defaults":{name:value for name,value in DEFAULTS[key].items() if name not in BOUND_PARAMS},
            "required_producers":list(REQUIRES.get(key,())),
            "required_canonical_producers":list(INTERNAL.get(key,())),
            "lifecycle_version":LIFECYCLE_VERSION if key=="SMT" else None,
            "observations":[name for name,spec in observations.items() if spec["indicator"]==key],
            "limitations": [UNSUPPORTED[name] for name in {
                "CRT":("CRT_NATIVE_OR_OTHER_HTF",),"SMT":("INVERSE_SMT","SMT_CANDIDATE_OR_ABSENCE"),
                "STRUCTURE":("STRUCTURE_TREND",),"FVG":("ATR_FVG",),
            }.get(key,())],
        }
    return {**({"binding_policy_version": BINDING_POLICY_VERSION} if version == 2 else {}),
            "schema_version":version,"default_policy":INTEGRITY_DEFAULT_POLICY if version == 2 else DEFAULT_POLICY,
            "indicators":indicators,"observations":deepcopy(observations),"unsupported":UNSUPPORTED.copy(),
            "modes":{mode:{"authority":authority,"version":MODE_VERSIONS[mode],
                           "operationally_enabled":mode!=RECORDED_MODE} for mode,authority in AUTHORITIES.items()},
            "limits":{"max_nodes":96,"max_depth":6,"max_evidence_age_seconds":86400,
                      "max_temporal_bars":500,"max_dependencies":24,"max_temporal_links":96}}
