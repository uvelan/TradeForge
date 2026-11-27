import json
import lzma
import struct
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from scripts import month_archive as archive


DAY = datetime(2025,11,3,tzinfo=UTC)


def test_pause_and_resume_preserves_http_evidence(tmp_path, monkeypatch):
    calls = []
    def throttled(day, name):
        calls.append(name)
        return b"", {"http":429, "curl_exit":22, "retry_after_seconds":180}
    monkeypatch.setattr(archive, "download", throttled)
    pending = [(DAY, tmp_path / "00h_ticks.bi5", None)]
    receipt = tmp_path / "requests.jsonl"
    assert archive.acquire(pending, receipt) == "PROVIDER_COOLDOWN"
    assert archive.acquire(pending, receipt) == "PROVIDER_COOLDOWN_ACTIVE"
    saved = json.loads(receipt.read_text())
    assert saved["start"] == int(DAY.timestamp()) and saved["http"] == 429
    assert saved["retry_after_seconds"] == 180 and len(calls) == 1


def test_torn_tail_repair_preserves_cooldown_and_allows_later_append(tmp_path, monkeypatch):
    clock = DAY
    calls = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return clock

    def throttled(day, name):
        calls.append(name)
        return b"", {"http": 429, "curl_exit": 22, "retry_after_seconds": 180}

    monkeypatch.setattr(archive, "datetime", Clock)
    monkeypatch.setattr(archive, "download", throttled)
    pending = [(DAY, tmp_path / "00h_ticks.bi5", None)]
    receipt = tmp_path / "requests.jsonl"

    assert archive.acquire(pending, receipt) == "PROVIDER_COOLDOWN"
    committed = receipt.read_bytes().replace(b"\n", b"\r\n")
    receipt.write_bytes(committed + b'{"validation_error": "\xe2\x82')
    assert archive.acquire(pending, receipt) == "PROVIDER_COOLDOWN_ACTIVE"
    assert len(calls) == 1
    assert receipt.read_bytes() == committed

    clock += timedelta(minutes=4)
    assert archive.acquire(pending, receipt) == "PROVIDER_COOLDOWN"
    assert len(calls) == 2
    appended = receipt.read_bytes()
    assert appended.startswith(committed)
    assert len(appended.splitlines()) == 2
    assert json.loads(appended.splitlines()[1])["at"] == clock.isoformat()
    assert archive.acquire(pending, receipt) == "PROVIDER_COOLDOWN_ACTIVE"
    assert len(calls) == 2
    assert receipt.read_bytes() == appended


def test_complete_final_object_without_newline_preserves_cooldown(tmp_path):
    committed = json.dumps({
        "at": DAY.isoformat(),
        "resume_after_epoch": int(datetime.now(UTC).timestamp()) + 180,
    }).encode("utf-8")
    receipt = tmp_path / "requests.jsonl"
    receipt.write_bytes(committed)
    pending = [(DAY, tmp_path / "00h_ticks.bi5", None)]

    assert archive.acquire(pending, receipt) == "PROVIDER_COOLDOWN_ACTIVE"
    assert receipt.read_bytes() == committed + b"\n"


def test_committed_receipt_corruption_is_visible_and_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "download", lambda *_: pytest.fail("unexpected download"))
    data = b'{"bad"\n{"at": "later"}\n{"torn"'
    receipt = tmp_path / "requests.jsonl"
    receipt.write_bytes(data)
    pending = [(DAY, tmp_path / "00h_ticks.bi5", None)]

    with pytest.raises(ValueError, match="Invalid acquisition receipt at line 1"):
        archive.acquire(pending, receipt)
    assert receipt.read_bytes() == data


def test_concurrent_acquisition_cannot_bypass_inflight_receipt(tmp_path, monkeypatch):
    receipt = tmp_path / "requests.jsonl"
    pending = [(DAY, tmp_path / "00h_ticks.bi5", None)]

    def throttled(day, name):
        with monkeypatch.context() as nested:
            nested.setattr(archive, "download", lambda *_: pytest.fail("concurrent download"))
            assert archive.acquire(pending, receipt) == "ACQUISITION_IN_PROGRESS"
        return b"", {"http": 429, "curl_exit": 22, "retry_after_seconds": 180}

    monkeypatch.setattr(archive, "download", throttled)
    assert archive.acquire(pending, receipt) == "PROVIDER_COOLDOWN"
    assert archive.acquire(pending, receipt) == "PROVIDER_COOLDOWN_ACTIVE"


def test_timeout_budget_stops_whole_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "download", lambda *_: (b"", {"http":None,"curl_exit":28,"retry_after_seconds":0}))
    receipt = tmp_path / "requests.jsonl"
    pending = [(DAY+timedelta(hours=i), tmp_path/f"{i:02}h_ticks.bi5", None) for i in range(8)]
    assert archive.acquire(pending, receipt) == "FOUR_CONSECUTIVE_FAILURES"
    rows = [json.loads(line) for line in receipt.read_text().splitlines()]
    assert len(rows) == 4 and rows[-1]["stop"] == "FOUR_CONSECUTIVE_FAILURES"


def test_recover_committed_bytes_after_interrupted_cache_publish(client,tmp_path,monkeypatch,capsys):
    raw = lzma.compress(struct.pack(">IIIff", 100, 109001,109000,1.0,2.0),format=lzma.FORMAT_ALONE)
    target = tmp_path/"raw"/"2025-11-03"/"00h_ticks.bi5"
    def pretend_disk_full(*args):
        raise OSError("disk full")
    with monkeypatch.context() as patch:
        patch.setattr(archive, "download", lambda *_: (raw, {"http":200,"curl_exit":0,"retry_after_seconds":0}))
        patch.setattr(archive, "publish", pretend_disk_full)
        with pytest.raises(OSError,match="disk full"):
            archive.acquire([(DAY,target,None)],tmp_path/"requests.jsonl")
    assert not target.exists()
    archive.run("2025-11",tmp_path/"raw",False)
    assert target.read_bytes() == raw
    assert sum(bool(x["pin"]) for x in json.loads(capsys.readouterr().out)["hours"]) == 1
    target.write_bytes(b"changed")
    with pytest.raises(ValueError,match="checksum mismatch"):
        archive.run("2025-11",tmp_path/"raw",False)
    assert target.read_bytes() == b"changed"
    with pytest.raises(ValueError,match="Existing source changed"):
        archive.publish(target,raw,sha256(raw).hexdigest())


def test_stored_conflict_is_not_a_skippable_network_error(tmp_path,monkeypatch):
    raw = lzma.compress(struct.pack(">IIIff", 100, 109001,109000,1.0,2.0), format=lzma.FORMAT_ALONE)
    monkeypatch.setattr(archive,"download",lambda *_:(raw,{"http":200,"curl_exit":0,"retry_after_seconds":0}))
    def conflict(*args):
        raise ValueError("Existing partition corruption")
    monkeypatch.setattr(archive,"import_bi5",conflict)
    with pytest.raises(ValueError,match="Existing partition corruption"):
        archive.acquire([(DAY,tmp_path/"00h_ticks.bi5",None)],tmp_path/"requests.jsonl")
    assert not (tmp_path/"00h_ticks.bi5").exists()


def test_existing_legacy_flat_control_does_not_copy_file(client,tmp_path,monkeypatch,capsys):
    raw = lzma.compress(struct.pack(">IIIff", 100, 109001,109000,1.0,2.0), format=lzma.FORMAT_ALONE)
    monkeypatch.setitem(archive.PINS,13,sha256(raw).hexdigest())
    source = tmp_path / "flat"
    source.mkdir()
    (source / "13h_ticks.bi5").write_bytes(raw)
    archive.run("2025-11", source, False)
    assert [p.name for p in source.iterdir()] == ["13h_ticks.bi5"]
    assert sum(bool(x["pin"]) for x in json.loads(capsys.readouterr().out)["hours"]) == 1
