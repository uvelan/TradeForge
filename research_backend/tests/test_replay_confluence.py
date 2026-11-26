"""Public replay join exercised through saved, checksum-verified candle partitions."""
import pytest

from research_backend.app import db, replay_hour, storage
from research_backend.tests.test_replay_hour import START, URL, publish, session

STEP = 300
LEFT = [10, 11, 14, 11, 10, 11, 16, 11, 10, 11, 11, 11]
RIGHT = [22, 24, 29, 24, 22, 24, 27, 24, 22, 24, 24, 24]


def at(i):
    return START+i*STEP


def bar(i, price, high=None, low=None, arrival=None):
    return dict(time=at(i), open=price, close=price, high=price if high is None else high,
                low=price if low is None else low, available_at=arrival)


def candles(prices):
    return [bar(i, x, x+1, x-1) for i, x in enumerate(prices)]


def capture(client, left, right, reverse=False, **options):
    pins = [publish(left), publish(right, "GBPUSD")]
    sym, peer = ("GBPUSD", "EURUSD") if reverse else ("EURUSD", "GBPUSD")
    key = session(client, symbol=sym, end=at(max(len(left),len(right))),
                  views=[dict(view="comparison",symbol=peer,timeframe="M5")], **options)
    return key, pins


def request(client, key, cutoff, *, verb="get", **params):
    return getattr(client, verb)(f"{URL}/{key}/confluence", params=dict(cutoff=cutoff, **params))


def receipt(client, key, cutoff, **params):
    response = request(client, key, cutoff, **params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(("sweep","status","direction"), [
    ((18,15),"ALIGNED","BEARISH"), ((12,8),"CONTRADICTED",None),
])
def test_exact_overlap_direction_proofs_and_single_read_per_partition(client, monkeypatch, sweep, status, direction):
    high, low = sweep
    value = 16 if high == 18 else 10
    left = candles(LEFT)+[bar(12,value,high,low), bar(13,12)]
    right = candles(RIGHT)+[bar(12,24),bar(13,24)]
    key, pins = capture(client,left,right)
    counts = []
    reader = replay_hour.iter_complete_partition

    def count(part, **kw):
        counts.append(part["id"])
        return reader(part, **kw)

    monkeypatch.setattr(replay_hour, "iter_complete_partition", count)
    latest = receipt(client,key,at(13))
    assert sorted(counts) == sorted(p["id"] for p in pins)
    assert (latest["status"],latest["direction"],latest["first_available_at"]) == (status,direction,at(13))
    crt, smt = (latest["components"][name] for name in ("crt","smt"))
    assert crt["condition"] == smt["condition"] == "SATISFIED"
    assert smt["state"] == "SMT_CONFIRMED" and smt["candidate_at"] == at(9)
    assert crt["revision"] == smt["revision"] == 2
    assert len([c for c in crt["evidence"] if c["roles"][0]["role"] == "REFERENCE_CHILD"]) == 12
    expected = {("EURUSD",stamp):row for row in left for stamp in [row["time"]]}
    expected.update({("GBPUSD",row["time"]):row for row in right})
    deps = {r["id"]:r["available_at"] for r in smt["revisions"]}
    for c in smt["evidence"]:
        original = expected[c["symbol"],c["time"]]
        assert [c[k] for k in ("time","open","high","low","close")] == [
            original[k] for k in ("time","open","high","low","close")]
        assert c["partitions"][0]["id"] == pins[int(c["symbol"] == "GBPUSD")]["id"]
        assert c["availability_basis"] == "MODELED_NOMINAL_CLOSE" and c["available_at"] <= latest["cutoff"]
        for role in c["roles"]:
            if role["event_id"] in deps:
                assert c["available_at"] <= deps[role["event_id"]]
            if role["role"] == "AS_OF_LAGGARD_SURVEILLANCE":
                assert (role["event_id"],role["cutoff"]) == (smt["family_id"],latest["cutoff"])
    assert receipt(client,key,at(13)-1)["status"] == "NOT_ALIGNED"
    assert receipt(client,key,at(13)) == latest
    # Deterministic pane-independent producers, even though the session ID is different.
    reverse = session(client,symbol="GBPUSD",end=at(14),views=[dict(view="2",symbol="EURUSD",timeframe="M5")])
    assert receipt(client,reverse,at(13))["components"] == latest["components"]


@pytest.mark.parametrize("overlap",[False,True])
def test_actual_expiry_disjoint_versus_overlap(client,overlap):
    sweep_index = 70 if overlap else 73
    left = candles(LEFT)+[bar(i,11) for i in range(12,76)]
    right = candles(RIGHT)+[bar(i,24) for i in range(12,76)]
    left[sweep_index] = bar(sweep_index,16,18,15)
    key, _ = capture(client,left,right)
    before = receipt(client,key,at(sweep_index+1)-1)
    after = receipt(client,key,at(sweep_index+1))
    assert before["status"] != "ALIGNED"
    assert after["components"]["crt"]["condition"] == "SATISFIED"
    if overlap:
        assert (after["status"],after["components"]["smt"]["state"]) == ("ALIGNED","SMT_CONFIRMED")
    else:
        assert (after["status"],after["components"]["smt"]["state"]) == ("NOT_ALIGNED","EXPIRED")
        assert after["components"]["smt"]["available_at"] == at(73)
        assert after["components"]["smt"]["revisions"][1]["state"] == "SMT_CONFIRMED"


def test_candidate_delayed_peer_and_recovery_both_clocks(client):
    left = candles(LEFT)+[bar(12,16,18,15)]
    right = candles(RIGHT)+[bar(12,24)]
    right[9]["available_at"] = at(10)+17
    key, _ = capture(client,left,right)
    candidate = receipt(client,key,at(9))["components"]["smt"]
    assert (candidate["state"],candidate["condition"]) == ("SMT_CANDIDATE","INCOMPLETE")
    blocked = receipt(client,key,at(10)+16)
    assert blocked["status"] == "INCOMPLETE"
    assert blocked["components"]["smt"]["condition"] == "INCOMPLETE"
    assert blocked["components"]["smt"]["coverage_reason"] == "MISSING_OPEN_CANDLES"
    smt = receipt(client,key,at(10)+17)["components"]["smt"]
    assert (smt["state"],smt["available_at"],smt["family_id"]) == ("SMT_CONFIRMED",at(10)+17,candidate["family_id"])
    proof = next(c for c in smt["evidence"] if (c["symbol"], c["time"]) == ("GBPUSD",at(9)))
    assert (proof["availability_basis"],proof["available_at"]) == ("ARCHIVE_SUPPLIED",at(10)+17)
    assert receipt(client,key,at(9))["components"]["smt"] == candidate


def test_new_smt_candidate_beats_prior_confirmation_and_latest_crt_beats_prior_sweep(client):
    left, right = candles(LEFT), candles(RIGHT)
    left[10],right[10] = bar(10,18,19,17),bar(10,25,26,24)
    left.extend(bar(i,11,12,10) for i in range(12,24))
    right.extend(bar(i,24,25,23) for i in range(12,24))
    left[14] = bar(14,16,20,15)
    key, _ = capture(client,left,right)
    old = receipt(client,key,at(12))["components"]["smt"]
    new = receipt(client,key,at(13))["components"]["smt"]
    assert old["state"] == "SMT_CONFIRMED" and new["state"] == "SMT_CANDIDATE"
    assert new["candidate_at"] > old["candidate_at"] and new["family_id"] != old["family_id"]
    active = receipt(client,key,at(15))
    assert active["status"] == "ALIGNED"
    replaced = receipt(client,key,at(24))
    assert replaced["components"]["crt"]["state"] == "ACTIVE"
    assert replaced["components"]["crt"]["family_id"] != active["components"]["crt"]["family_id"]
    assert replaced["status"] == "NOT_ALIGNED"


def test_pinned_receipt_survives_newer_evaluation_and_restart_but_rejects_corruption(client,monkeypatch):
    left = candles(LEFT)+[bar(12,16,18,15),bar(13,13),bar(14,10,11,9)]
    right = candles(RIGHT)+[bar(i,24) for i in range(12,15)]
    left[13]["available_at"] = at(15)
    key,pins = capture(client,left,right)
    result = receipt(client,key,at(13))
    pin = result["receipt_id"]
    assert receipt(client,key,at(15))["components"]["crt"]["state"] == "OPPOSITE_BOUNDARY_REACHED"
    assert db.one("SELECT count(*) AS n FROM replay_confluence_receipts")["n"] == 0
    assert request(client,key,at(13),receipt_id=pin).status_code == 409
    for _ in range(2):
        assert receipt(client,key,at(13),verb="post",receipt_id=pin) == result
    assert db.one("SELECT count(*) AS n FROM replay_confluence_receipts")["n"] == 1
    assert request(client,key,at(15),receipt_id=pin).status_code == 409
    assert request(client,key,at(15),verb="post",receipt_id=pin).status_code == 409
    assert request(client,key,at(13),receipt_id="0"*64).status_code == 409
    # Reopen a stored version even after the evaluator changes.
    db.initialize()
    monkeypatch.setattr("research_backend.app.replay_confluence.crt_component",lambda *_:pytest.fail("recomputed receipt"))
    assert receipt(client,key,at(13),receipt_id=pin) == result
    (storage.MARKET_DIR/pins[1]["relative_path"]).write_bytes(b"controlled corruption")
    assert request(client,key,at(13),receipt_id=pin).status_code == 409

