"""Characterize legacy closed-OHLC timing without changing execution semantics."""
from decimal import Decimal
from heapq import heappush

import pytest

from research_backend.app.analysis_evidence import EvidenceIndex
from research_backend.app.indicator_engine import EvidenceBar, StructureRules
from research_backend.app.liquidity import LiquidityDelivery, LiquidityRules, LiquidityStream, Pool
from research_backend.app.order_blocks import Block, OrderBlockStream, OrderBlockRules, OrderBlockDelivery


def liquidity(activation, expires):
    stream=LiquidityStream(LiquidityRules(),LiquidityDelivery(),300)
    price=Decimal(100)
    pool=Pool("pool","HIGH",price,price,price,expires,0,600,
              state="ACTIVE",activated_at=activation)
    stream.active[pool.id]=pool
    stream.buckets[stream.bucket(pool.side,pool.anchor)]={pool.id}
    stream.index(pool)
    heappush(stream.expirations,(expires,pool.id))

    def observe(stamp,available,index,gap):
        bar=EvidenceBar(time=stamp,open=99,high=102,low=96,close=99)
        stream.observe(bar,available,index,gap,[],[],"fixture","fixture")
        return list(stream.pools.events)

    return observe


def order_block(activation, expires):
    stream=OrderBlockStream(OrderBlockRules(),StructureRules(),{},300,OrderBlockDelivery())
    zone=Block("zone",1,Decimal(95),Decimal(100),expires,
               {"first_visibility_time":activation,"formation_fingerprint":"fixture"})
    stream.book[zone.id]=zone
    stream.put(zone)
    heappush(stream.expiry,(expires,zone.id))
    seed=EvidenceBar(time=600,open=101,high=102,low=100,close=101)
    stream.publish(zone,"ACTIVE","FIXTURE",seed,activation)

    def observe(stamp,available,index,gap):
        bar=EvidenceBar(time=stamp,open=101,high=102,low=96,close=99)
        if gap:
            bar=EvidenceBar(time=stamp,open=94,high=94,low=92,close=93)
        stream.advance(bar,available,index)
        return list(stream.revisions.events)

    return observe


@pytest.mark.parametrize("factory,touched", [(liquidity,"SWEPT"),(order_block,"PARTIALLY_MITIGATED")])
@pytest.mark.parametrize("start,available", [(900,1200),(1200,1500),(900,1800)])
def test_legacy_whole_candle_touch_when_activation_precedes_availability(factory,touched,start,available):
    activation=1020
    event,=factory(activation,5)(start,available,2,False)
    assert (event["state"],event["observation_time"],event["confirmation_time"],event["available_at"]) == (
        touched,start,start+300,available)
    # Only the middle case proves the whole observation began after activation.
    assert (start>=activation) == (start==1200)


@pytest.mark.parametrize("factory", [liquidity,order_block])
def test_equal_availability_does_not_interact_and_expiry_preempts_crossing(factory):
    assert factory(1200,5)(900,1200,2,False) == []
    event,=factory(1020,2)(1200,1800,2,False)
    assert (event["state"],event["available_at"]) == ("EXPIRED",1800)


@pytest.mark.parametrize("factory", [liquidity,order_block])
def test_gap_invalidation_overrides_wick_interpretation(factory):
    event,=factory(1020,5)(1200,1800,2,"FIXTURE_GAP")
    assert event["state"] == "INVALIDATED"
    assert event["available_at"] == 1800


def test_replay_rewind_does_not_expose_late_context():
    index=EvidenceIndex()
    index.add("legacy", {"id":"event","available_at":1800,"observation_time":900})
    lookup=index.finish().as_of
    assert lookup(1800,1200)["legacy"]["id"] == "event"
    assert lookup(1799,1200) == {}
    assert lookup(1800,899) == {}
