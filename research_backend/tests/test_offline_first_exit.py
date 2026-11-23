"""Exercise fixed first exits using persisted fills from published historical runs."""
import json
from itertools import count
from pathlib import Path
from sqlite3 import IntegrityError
from uuid import UUID

import pytest

from research_backend.app.analysis_evidence import canonical
from research_backend.app.db import transaction
from research_backend.app.offline_first_exit import validate_first_exit
from research_backend.app.strategy_evidence import fingerprint
from test_bounded_crt_smt_successors import canonical_successors
from test_offline_market_entry import EXPIRY, Q, publish, quote, source_records, tape, validate

CUTOFF = EXPIRY+5
BASELINE = (Path(__file__).resolve().parents[2]/"docs"/"implementation"/"evidence"
            /"TF-I10A2-first-exit"/"merged-entry-baseline.json")


@pytest.fixture
def position(publish):
    def open_position(direction="bullish", **kwargs):
        return publish(kind="STOP",direction=direction,entry_price=1.10310,**kwargs)
    return open_position


def calculate(run, data, entry, cutoff=CUTOFF):
    return validate_first_exit(run,entry_result_id=entry["id"],tape=data,
                               tape_sha256=fingerprint(data),cutoff_ms=cutoff)


def snapshots(run, entry):
    with transaction() as db:
        row, = db.execute("SELECT CAST(payload AS BLOB) FROM offline_market_entry_results WHERE id=?",
                          (entry["id"],)).fetchone()
    return source_records(run),row


def check(published, case, data, status, *, winner=None, cutoff=CUTOFF, levels=True):
    run,intent = published
    entry, = validate(run,data,intent["id"])["results"]
    assert entry["status"]=="SIMULATED_FILLED"
    assert entry["fill"]["price"]==("1.10314" if intent["direction"]=="LONG" else "1.10306")
    original = snapshots(run,entry)
    result = calculate(run,data,entry,cutoff)
    row, = result["results"]
    assert row["status"]==status
    assert (result["simulated_orders"],result["simulated_fills"],result["simulated_exits"])==(1,1,int(winner is not None))
    assert row["identity"]["entry_fill_id"]==entry["fill"]["id"]
    assert row["identity"]["management_policy_fingerprint"]==intent["management_policy_fingerprint"]
    assert row["identity"]["cutoff_ms"]==cutoff
    assert row["identity"]["execution_data_fingerprint"]==fingerprint(data)
    if levels:
        stop,target = (110214,110514) if intent["direction"]=="LONG" else (110406,110106)
        assert (row["levels"]["stop_units"],row["levels"]["target_units"])==(stop,target)
        assert (row["levels"]["stop_price"],row["levels"]["target_price"])==(
            ("1.10214","1.10514") if intent["direction"]=="LONG" else ("1.10406","1.10106"))
    else:
        assert row["levels"] is None
    if winner is None:
        assert row["exit"] is None
    else:
        chosen,reason,threshold,price = winner
        close = row["exit"]
        side = "BID" if intent["direction"]=="LONG" else "ASK"
        assert (close["reason"],close["threshold_price"],close["price"],close["side"],close["time_ms"])==(
            reason,threshold,price,side,chosen["available_at_ms"])
        assert close["quote"]==chosen | {"scale":100000}

    assert calculate(run,data,entry,cutoff)==result
    assert snapshots(run,entry)==original
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM offline_first_exit_results WHERE id=?",(row["id"],)).fetchone()[0]==1
        with pytest.raises(IntegrityError,match="immutable record"):
            db.execute("DELETE FROM offline_first_exit_results WHERE id=?",(row["id"],))
    print(json.dumps({"case":case,"expected":status,"observed":row["status"],"intent_id":intent["id"],
                      "entry_fill":entry["fill"],"entry_result_id":entry["id"],
                      "tape_sha256":fingerprint(data),"result_id":row["id"],"cutoff_ms":cutoff,
                      "levels":row["levels"],"exit":row["exit"]},sort_keys=True))
    return row


ENTRY_LONG = quote(Q+2,Q+2,5,110311,110314)
ENTRY_SHORT = quote(Q+2,Q+2,5,110306,110309)
LONG_STOP = quote(Q+4,Q+4,7,110210,110220)
LONG_TARGET = quote(Q+4,Q+4,7,110514,110520)
SHORT_STOP = quote(Q+4,Q+4,7,110400,110406)
SHORT_TARGET = quote(Q+4,Q+4,7,110103,110106)


@pytest.mark.parametrize("direction,entry,miss,stop,target,threshold_stop,threshold_target,gap_price",[
    ("bullish",ENTRY_LONG,quote(Q+3,Q+3,6,110513,110514),LONG_STOP,LONG_TARGET,"1.10214","1.10514","1.10210"),
    ("bearish",ENTRY_SHORT,quote(Q+3,Q+3,6,110106,110107),SHORT_STOP,SHORT_TARGET,"1.10406","1.10106","1.10406"),
])
def test_both_directions_stop_and_target_first(position,direction,entry,miss,stop,target,
                                                threshold_stop,threshold_target,gap_price):
    published = position(direction)
    sealed = [(Q+1,CUTOFF)]
    later = {"time_ms":Q+5,"available_at_ms":Q+5,"source_order":8}
    check(published,f"{direction} stop first after opposite-side-only target touch",
          tape([entry,miss,stop,target | later],sealed),"SIMULATED_EXIT",
          winner=(stop,"PROTECTIVE_STOP",threshold_stop,gap_price))
    check(published,f"{direction} exact target before stop",
          tape([entry,miss,target,stop | later],sealed),"SIMULATED_EXIT",
          winner=(target,"REWARD_TARGET",threshold_target,threshold_target))


def test_exit_activation_cutoff_and_continuous_coverage(position):
    published = position()
    wide = quote(Q+2,Q+2,5,110210,110314)
    sealed = [(Q+1,CUTOFF)]
    check(published,"entry quote wide-spread stop",tape([wide,LONG_TARGET],sealed),"SIMULATED_EXIT",
          cutoff=Q+2,winner=(wide,"PROTECTIVE_STOP","1.10214","1.10210"))
    delayed = ENTRY_LONG | {"time_ms":Q+1}
    stale_stop = quote(Q+1,Q+3,7,110210,110220)
    same_ms_stop = quote(Q+2,Q+2,6,110210,110220)
    check(published,"later sequence in the entry millisecond",tape([same_ms_stop,delayed],sealed),
          "SIMULATED_EXIT",cutoff=Q+2,winner=(same_ms_stop,"PROTECTIVE_STOP","1.10214","1.10210"))
    check(published,"stale observation delivered after entry",tape([delayed,stale_stop,LONG_TARGET],sealed),
          "SIMULATED_EXIT",winner=(LONG_TARGET,"REWARD_TARGET","1.10514","1.10514"))
    check(published,"one missing millisecond before prospective stop",
          tape([ENTRY_LONG,LONG_STOP],[(Q+1,Q+2),(Q+4,CUTOFF)]),"INSUFFICIENT_QUOTE_COVERAGE")
    check(published,"sealed horizon without touch after entry expiry",tape([ENTRY_LONG],sealed),"OPEN_AT_CUTOFF")
    check(published,"uncovered final millisecond",tape([ENTRY_LONG],[(Q+1,CUTOFF-1)]),
          "INSUFFICIENT_QUOTE_COVERAGE")

    late_target = LONG_TARGET | {"time_ms":CUTOFF,"available_at_ms":CUTOFF}
    data = tape([ENTRY_LONG,late_target],sealed)
    open_row = check(published,"one millisecond before late target",data,"OPEN_AT_CUTOFF",cutoff=CUTOFF-1)
    closed = check(published,"target at cutoff after entry expiry",data,"SIMULATED_EXIT",
                   winner=(late_target,"REWARD_TARGET","1.10514","1.10514"))
    assert open_row["id"]!=closed["id"]
    assert open_row["identity"]["entry_fill_id"]==closed["identity"]["entry_fill_id"]

    data = tape([ENTRY_LONG,LONG_STOP,LONG_TARGET | {"time_ms":Q+5,"available_at_ms":Q+5,"source_order":8}],
                [(Q+1,Q+4)])
    winner = (LONG_STOP,"PROTECTIVE_STOP","1.10214","1.10210")
    first = check(published,"stop recorded at first eligible cutoff",data,"SIMULATED_EXIT",cutoff=Q+4,winner=winner)
    later = check(published,"extend cutoff after recorded stop",data,"SIMULATED_EXIT",winner=winner)
    assert first["id"]!=later["id"]
    assert first["exit"]==later["exit"]


def test_stop_target_conflict_before_and_after_a_certain_exit(position):
    published = position()
    sealed = [(Q+1,CUTOFF)]
    certain = quote(Q+3,Q+3,6,110514,110520)
    late = {"time_ms":Q+5,"available_at_ms":Q+5,"source_order":8}
    certain_rows = []
    for label, arrange in (("forward",list),("reverse",lambda rows:list(reversed(rows)))):
        check(published,f"stop/target conflict first {label}",tape(arrange([ENTRY_LONG,LONG_STOP,LONG_TARGET,certain | late]),sealed),
              "AMBIGUOUS_QUOTE_ORDER")
        row = check(published,f"stop/target conflict after certain exit {label}",
              tape(arrange([ENTRY_LONG,certain,LONG_STOP | late,LONG_TARGET | late]),sealed),
              "SIMULATED_EXIT",winner=(certain,"REWARD_TARGET","1.10514","1.10514"))
        certain_rows.append(row)
    first,reverse = certain_rows
    assert first["identity"]["execution_data_fingerprint"]!=reverse["identity"]["execution_data_fingerprint"]
    for key in ("quote","reason","side","price_units","time_ms"):
        assert reverse["exit"][key]==first["exit"][key]


@pytest.mark.parametrize("rule,value",[
    ("break_even_at_r",1),("trailing_pips",8),("partial_exit",{"at_r":1.5,"percent":25}),
    ("time_exit",{"unit":"SECONDS","count":120}),
])
def test_management_extensions_are_explicitly_unsupported(position,rule,value):
    management = {rule:value}
    if rule=="partial_exit":
        management["target"] = {"kind":"REWARD_MULTIPLE","multiple":3}
    published = position(management=management)
    row = check(published,f"unsupported {rule}",tape([ENTRY_LONG,LONG_TARGET],[(Q+1,CUTOFF)]),
                "UNSUPPORTED_EXIT_MANAGEMENT",levels=False)
    assert row["unsupported_rules"]==[rule]


@pytest.mark.parametrize("management",[
    {"initial_stop":{"kind":"FIXED_PIPS","distance":10.01}},
    {"target":{"kind":"REWARD_MULTIPLE","multiple":2.00001}},
])
def test_fractional_level_rejected_without_rounding(position,management):
    published = position(management=management)
    check(published,"unrepresentable protective level",tape([ENTRY_LONG,LONG_STOP],[(Q+1,CUTOFF)]),
          "UNREPRESENTABLE_EXIT_LEVELS",levels=False)


def test_nonfilled_entry_cannot_create_any_exit(position,publish):
    published = position()
    run,intent = published
    off_grid_run,off_grid_intent = publish(kind="STOP",direction="bullish",entry_price=1.103101)
    for identifier,source,data,status in (
        (run,intent,tape([]),"VERIFIED_EXPIRED"),
        (run,intent,tape([],[(Q+1,Q+2)]),"INSUFFICIENT_QUOTE_COVERAGE"),
        (run,intent,tape([ENTRY_LONG,ENTRY_LONG | {"ask_units":110315}]),"AMBIGUOUS_QUOTE_ORDER"),
        (off_grid_run,off_grid_intent,tape([ENTRY_LONG]),"UNSUPPORTED_STOP_PRICE_GRID"),
    ):
        entry, = validate(identifier,data,source["id"])["results"]
        assert entry["status"]==status
        before = snapshots(identifier,entry)
        result = calculate(identifier,data,entry)
        assert (result["simulated_fills"],result["simulated_exits"])==(0,0)
        assert result["results"]==[{"status":"NO_ENTRY_FILL","entry_result_id":entry["id"],
                                    "entry_status":status,"exit":None}]
        assert snapshots(identifier,entry)==before
        print(json.dumps({"case":"no exit without fill","entry_status":status,
                          "tape_sha256":fingerprint(data),"exits":0},sort_keys=True))
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM offline_first_exit_results").fetchone()[0]==0


def test_exit_rejects_mismatched_source_tape_and_cutoff(position,publish):
    run,intent = position()
    data = tape([ENTRY_LONG],[(Q+1,CUTOFF)])
    entry, = validate(run,data,intent["id"])["results"]
    with pytest.raises(ValueError,match="cutoff precedes"):
        calculate(run,data,entry,Q+1)
    with pytest.raises(ValueError,match="pinned execution tape"):
        calculate(run,tape([ENTRY_LONG,LONG_TARGET]),entry)
    other_tape = tape([ENTRY_LONG,LONG_TARGET])
    forged_identity = entry["identity"] | {"execution_data_fingerprint":fingerprint(other_tape)}
    forged = entry | {"identity":forged_identity,"id":"ME-"+fingerprint(forged_identity)}
    # This alternate row has a self-consistent outer identity but a fill from a different tape.
    with transaction() as db:
        db.execute("INSERT INTO offline_market_entry_results VALUES (?,?,?,?)",
                   (forged["id"],run,intent["id"],canonical(forged)))
    with pytest.raises(ValueError,match="does not match verified replay"):
        calculate(run,other_tape,forged)
    other,_ = publish(direction="bullish")
    with pytest.raises(ValueError,match="does not belong"):
        calculate(other,data,entry)
    queued,_ = publish(direction="bullish",incomplete=True)
    with pytest.raises(ValueError,match="completed published"):
        calculate(queued,data,entry)


def test_market_limit_stop_entry_payloads_match_merged_main_after_exit(publish,monkeypatch):
    ids = count(1)
    monkeypatch.setattr("research_backend.app.strategy_routes.uuid4",lambda:UUID(int=next(ids)))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    data = baseline["tape"]
    assert fingerprint(data)==baseline["tape_sha256"]
    for kind,direction,price in (("MARKET","bearish",None),("LIMIT","bullish",1.10310),("STOP","bullish",1.10310)):
        run,intent = publish(kind=kind,direction=direction,entry_price=price)
        entry, = validate(run,data,intent["id"])["results"]
        expected = baseline["cases"][kind]
        assert (entry["id"],entry["fill"]["id"],intent["id"])==(
            expected["result_id"],expected["fill_id"],expected["intent_id"])
        before = snapshots(run,entry)
        assert before[1]==canonical(entry).encode()==expected["serialized_payload"].encode()
        result = calculate(run,data,entry,cutoff=Q+4)
        assert result["simulated_fills"]==1
        assert result["results"][0]["identity"]["entry_fill_id"]==entry["fill"]["id"]
        assert snapshots(run,entry)==before
        print(json.dumps({"case":f"{kind} merged compatibility","tape_sha256":fingerprint(data),
                          "result_id":entry["id"],"fill_id":entry["fill"]["id"],
                          "payload_bytes":len(before[1]),"byte_identical":True},sort_keys=True))


def test_market_v1_persisted_fill_keeps_its_original_identity(publish,monkeypatch):
    from research_backend.app import offline_market_entry

    run,intent = publish(direction="bullish")
    market_quote = quote(Q+1,Q+1,4,110311,110314)
    data = tape([market_quote,LONG_STOP],[(Q+1,CUTOFF)])
    with monkeypatch.context() as legacy:
        # Unique sequences give the same entry under V1, with its original IDs.
        legacy.setitem(offline_market_entry.POLICY,"version",1)
        old, = validate(run,data,intent["id"])["results"]
    assert old["identity"]["policy"]["version"]==1
    before = snapshots(run,old)
    result = calculate(run,data,old)
    assert result["simulated_exits"]==1
    exit_row, = result["results"]
    assert exit_row["identity"]["entry_fill_id"]==old["fill"]["id"]
    assert exit_row["exit"]["price"]=="1.10210" and exit_row["exit"]["side"]=="BID"
    assert snapshots(run,old)==before
    print(json.dumps({"case":"MARKET V1 replay","tape_sha256":fingerprint(data),
                      "entry_fill_id":old["fill"]["id"],"exit":exit_row["exit"]},sort_keys=True))

    conflicting = tape([market_quote,LONG_STOP,LONG_STOP | {"bid_units":110211}],[(Q+1,CUTOFF)])
    modern, = validate(run,conflicting,intent["id"])["results"]
    assert modern["status"]=="SIMULATED_FILLED"
    identity = modern["identity"] | {"policy":old["identity"]["policy"]}
    identifier = "ME-"+fingerprint(identity)
    fill = modern["fill"] | {"id":"MF-"+fingerprint([identifier,modern["fill"]["quote"]])}
    forged = modern | {"identity":identity,"id":identifier,"fill":fill}
    with transaction() as db:
        db.execute("INSERT INTO offline_market_entry_results VALUES (?,?,?,?)",
                   (identifier,run,intent["id"],canonical(forged)))
    with pytest.raises(ValueError,match="does not match verified replay"):
        calculate(run,conflicting,forged)

def test_no_fill_cutoff_filters_expiry_and_late_conflict(publish):
    run,intent = publish()
    for quotes,expected in (
        ([], "VERIFIED_EXPIRED"),
        ([quote(EXPIRY,EXPIRY,2),quote(EXPIRY,EXPIRY,2,110302,110315)], "AMBIGUOUS_QUOTE_ORDER"),
    ):
        data = tape(quotes)
        entry, = validate(run,data)["results"]
        before = snapshots(run,entry)
        assert entry["status"]==expected
        with pytest.raises(ValueError,match="precedes intent qualification"):
            calculate(run,data,entry,Q-1)
        for cutoff,status in ((Q,"PENDING_AT_CUTOFF"),(Q+1,"PENDING_AT_CUTOFF"),
                              (EXPIRY-1,"PENDING_AT_CUTOFF"),(EXPIRY,expected)):
            result = calculate(run,data,entry,cutoff)
            assert (result["simulated_orders"],result["simulated_fills"],result["simulated_exits"])==(1,0,0)
            assert result["results"][0]["entry_status"]==status
            assert calculate(run,data,entry,cutoff)==result
        assert snapshots(run,entry)==before
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM offline_first_exit_results").fetchone()[0]==0


@pytest.mark.parametrize("kind,version,scenario",[
    ("MARKET",1,"contradiction"),("MARKET",99,"unknown"),("MARKET",1,"duplicate"),
    ("LIMIT",1,"unsupported"),("LIMIT",2,"unsupported"),("STOP",1,"unsupported"),("STOP",2,"unsupported"),
])
def test_historical_no_fill_policy_verification(publish,kind,version,scenario):
    from research_backend.app.offline_market_entry import POLICY

    run,intent = publish(kind=kind,entry_price=None if kind=="MARKET" else 1.10310)
    quotes = [ENTRY_SHORT,SHORT_STOP]
    if scenario=="duplicate":
        quotes[1] = SHORT_STOP | {"source_order":ENTRY_SHORT["source_order"]}
    data = tape(quotes)
    current, = validate(run,data)["results"]
    identity = current["identity"] | {"policy":POLICY | {"version":version}}
    status = {"contradiction":"VERIFIED_EXPIRED","unknown":"VERIFIED_EXPIRED",
              "duplicate":"AMBIGUOUS_QUOTE_ORDER","unsupported":"UNSUPPORTED_ENTRY_TYPE"}[scenario]
    old = current | {"id":"ME-"+fingerprint(identity),"identity":identity,"status":status,"fill":None,
                     "order":None if scenario=="unsupported" else current["order"]}
    with transaction() as db:
        db.execute("INSERT INTO offline_market_entry_results VALUES (?,?,?,?)",
                   (old["id"],run,intent["id"],canonical(old)))
    before = snapshots(run,old)
    if scenario in ("contradiction","unknown"):
        message = "does not match verified replay" if scenario=="contradiction" else "Unverifiable historical entry policy"
        with pytest.raises(ValueError,match=message):
            calculate(run,data,old)
    else:
        result = calculate(run,data,old)
        assert result["results"][0]["entry_status"]==status
        assert result["simulated_orders"]==int(scenario=="duplicate")
        assert result["simulated_fills"]==result["simulated_exits"]==0
        if scenario=="duplicate":
            assert calculate(run,data,old,Q+3)["results"][0]["entry_status"]=="PENDING_POLICY_VERIFICATION"
    assert snapshots(run,old)==before
    with transaction() as db:
        assert db.execute("SELECT count(*) FROM offline_first_exit_results").fetchone()[0]==0
