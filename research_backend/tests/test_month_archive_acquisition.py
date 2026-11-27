import json
import lzma
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
from time import monotonic, sleep
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


def test_archive_command_restarts_after_terminated_receipt_write(tmp_path):
    receipt = tmp_path / "requests.jsonl"
    pid_path = receipt.with_suffix(".interrupt.pid")
    marker = receipt.with_suffix(".ready")
    calls = receipt.with_suffix(".calls")
    cwd = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "TRADEFORGE_STORAGE": str(tmp_path / "storage"),
        "TRADEFORGE_DATA_DIR": str(tmp_path / "storage" / "research"),
    }

    def command(mode):
        return [
            sys.executable, "-m", "research_backend.tests.month_archive_interruption", mode,
            "2025-11", "--source", str(tmp_path / "raw"), "--fetch", "--receipt", str(receipt),
        ]

    with subprocess.Popen(
        command("interrupt"), cwd=cwd, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    ) as child:
        try:
            deadline = monotonic() + 30
            while not marker.is_file() and child.poll() is None and monotonic() < deadline:
                sleep(0.02)
            reached_write = marker.is_file()
        finally:
            if child.poll() is None:
                # The Windows venv launcher may be a separate process from the writer.
                pid = pid_path.read_text(encoding="ascii") if pid_path.is_file() else ""
                if pid:
                    os.kill(int(pid), signal.SIGTERM)
                else:
                    child.kill()
            _, stderr = child.communicate(timeout=10)
    assert reached_write, stderr
    assert child.returncode != 0
    prefix, fragment = receipt.read_bytes().rsplit(b"\n", 1)
    committed = prefix + b"\n"
    assert [json.loads(line)["http"] for line in committed.splitlines()] == [503, 503]
    assert len(fragment) == 31
    assert len(calls.read_bytes().splitlines()) == 3

    resumed = subprocess.run(command("resume"), cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    assert resumed.returncode == 0, resumed.stderr
    assert "PROVIDER_COOLDOWN" in resumed.stderr
    assert sum(bool(hour["pin"]) for hour in json.loads(resumed.stdout)["hours"]) == 1
    appended = receipt.read_bytes()
    assert appended.startswith(committed)
    assert appended.endswith(b"\n")
    assert [json.loads(line)["http"] for line in appended.splitlines()] == [503, 503, 200, 429]
    assert len(calls.read_bytes().splitlines()) == 5

    restarted = subprocess.run(command("cooldown"), cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    assert restarted.returncode == 0, restarted.stderr
    assert "PROVIDER_COOLDOWN_ACTIVE" in restarted.stderr
    assert receipt.read_bytes() == appended
    assert len(calls.read_bytes().splitlines()) == 5


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
