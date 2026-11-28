"""The coverage API and monthly CLI share the Dukascopy acquisition scope."""
from datetime import UTC, datetime, timedelta
from threading import Event, Thread

from research_backend.app import config
from scripts import month_archive as archive


def test_provider_cooldown_across_months_and_cli(client,tmp_path,monkeypatch):
    monkeypatch.setattr(config,"STORAGE_ROOT",tmp_path)
    clock = datetime(2026,11,20,tzinfo=UTC)
    class Clock(datetime):
        @classmethod
        def now(cls,tz):
            return clock
    monkeypatch.setattr(archive,"datetime",Clock)
    calls=[]
    def throttled(day,name):
        calls.append(day.date())
        return b"",dict(http=429,curl_exit=22,retry_after_seconds=180)
    monkeypatch.setattr(archive,"download",throttled)
    def retry(date):
        stamp=int(datetime.fromisoformat(date).replace(tzinfo=UTC).timestamp())
        reply=client.post("/api/forex/retry",params={"hour":stamp})
        assert reply.status_code==200,reply.text
        return reply.json()["status"]
    assert retry("2026-10-08")=="PROVIDER_COOLDOWN"
    assert retry("2026-10-09")=="PROVIDER_COOLDOWN_ACTIVE"
    third=retry("2026-11-10")
    assert (third,len(calls))==("PROVIDER_COOLDOWN_ACTIVE",1)
    archive.run("2026-09",tmp_path/"custom-cache",True,tmp_path/"custom-receipt.jsonl")
    assert len(calls)==1
    clock+=timedelta(seconds=181)
    entered, release = Event(), Event()
    def delayed(day,name):
        entered.set()
        assert release.wait(10)
        return throttled(day,name)
    monkeypatch.setattr(archive,"download",delayed)
    outcome=[]
    worker=Thread(target=lambda:outcome.append(retry("2026-11-10")))
    worker.start()
    try:
        assert entered.wait(10)
        assert retry("2026-10-09")=="ACQUISITION_IN_PROGRESS"
    finally:
        release.set()
        worker.join(10)
    assert outcome==["PROVIDER_COOLDOWN"] and len(calls)==2


def test_first_retry_honors_existing_other_month_cooldown(client,tmp_path,monkeypatch):
    monkeypatch.setattr(config,"STORAGE_ROOT",tmp_path)
    folder=tmp_path/"t06-input"
    folder.mkdir()
    deadline=int(datetime.now(UTC).timestamp())+180
    path=folder/"2026-10-downloads.jsonl"
    original=(f'{{"resume_after_epoch":{deadline},"http":429}}\n').encode()
    path.write_bytes(original)
    monkeypatch.setattr(archive,"download",lambda *_: (_ for _ in ()).throw(AssertionError("provider called")))
    hour=int(datetime(2026,11,10,tzinfo=UTC).timestamp())
    assert client.post("/api/forex/retry",params={"hour":hour}).json()["status"]=="PROVIDER_COOLDOWN_ACTIVE"
    assert path.read_bytes()==original
