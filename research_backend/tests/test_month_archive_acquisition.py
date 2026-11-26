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
