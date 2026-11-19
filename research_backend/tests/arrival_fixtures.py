"""Explicit authorized-writer fixtures for archived analytical tests."""
from uuid import uuid4

from research_backend.app.arrival_capture import canonical, fingerprint, seal
from research_backend.app.arrival_policy import RECORDED_ARRIVAL, VERIFIED_UTC
from research_backend.app.db import transaction
from research_backend.app.indicator_coverage import MarketSchedule
from research_backend.app.market import FRAME_MIN


def trusted_archive(records, *, provider, feed, symbol, timeframe, start, end, cutoff=None, calendar=None):
    calendar = MarketSchedule.model_validate(calendar or {"kind":"continuous"})
    session = uuid4().hex
    step = FRAME_MIN[timeframe]*60
    scope = {"provider":provider,"feed_id":feed,"symbol":symbol,"timeframe":timeframe,"price_basis":"BID"}
    with transaction() as db:
        db.execute("INSERT INTO arrival_sessions VALUES(?,?,?,?,?,?,?)",
                   (session,provider,symbol,timeframe,RECORDED_ARRIVAL,start*1000,canonical(calendar.model_dump())))
        previous = {}
        for bar in records:
            if bar.get("closed",True) is not True or bar.get("availability_policy",RECORDED_ARRIVAL) != RECORDED_ARRIVAL:
                continue
            stamp = bar["time"]
            at = bar.get("available_at",stamp+step)*1000
            content = {key:bar[key] for key in ("time","open","high","low","close")}
            content.update(volume=bar.get("volume",0),source_observed_ms=None, timestamp_policy=VERIFIED_UTC, raw_provider_time=stamp)
            candle_hash = fingerprint(content)
            seq = db.execute("""INSERT INTO arrival_observations(
                session_id,provider,feed_id,symbol,timeframe,price_basis,candle_open,nominal_close,
                observed_ms,elapsed_ms,content,fingerprint,previous_sequence,recovery)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (session,provider,feed,symbol,timeframe,"BID",stamp,stamp+step,
                at,at-start*1000,canonical(content),candle_hash,previous.get(stamp),0)).lastrowid
            previous[stamp]=seq
            db.execute("INSERT INTO arrival_receipts VALUES(?,?)",(seq,at))
            db.execute("INSERT INTO arrival_publications VALUES(?,?,?)",(seq,at,session))
    return seal(scope,start,end,cutoff if cutoff is not None else end+3600,calendar)
