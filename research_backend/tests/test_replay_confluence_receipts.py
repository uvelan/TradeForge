"""Saved receipt summary discovery through the normal replay routes."""
import json

import pytest

from research_backend.app import db, storage
from research_backend.app.strategy_evidence import fingerprint
from research_backend.tests.test_replay_confluence import LEFT, RIGHT, at, bar, candles, capture, receipt, request
from research_backend.tests.test_replay_hour import URL, session


def test_saved_list_is_cutoff_and_session_scoped_paginated_and_read_only(client, monkeypatch):
    key, pins = capture(client, candles(LEFT)+[bar(12,16,18,15),bar(13,13)],
                       candles(RIGHT)+[bar(12,24),bar(13,24)])
    foreign = session(client, symbol="GBPUSD", end=at(14), views=[dict(view="2",symbol="EURUSD",timeframe="M5")])
    path = f"{URL}/{key}/confluence/receipts"

    def listed(clock, **params):
        result = client.get(path, params=dict(cutoff=clock, **params))
        assert result.status_code == 200, result.text
        return result.json()

    assert listed(at(14)) == dict(receipts=[], next_cursor=None)
    saved = []
    for cutoff in (at(12), at(13), at(14)):
        item = receipt(client, key, cutoff)
        assert receipt(client, key, cutoff, receipt_id=item["receipt_id"], verb="post") == item
        saved.append(item)
    latest = saved[-1]
    other = {field: value for field, value in latest.items() if field != "receipt_id"}
    # A versioned payload at the same cutoff exercises the keyset tie.
    other["rule_version"] = 2 if other["rule_version"] == 1 else 1
    other_id = fingerprint(other)
    with db.transaction() as conn:
        conn.execute("INSERT INTO replay_confluence_receipts(id,session_id,payload) VALUES(?,?,?)",
                     (other_id, key, json.dumps(other, sort_keys=True, separators=(",", ":"))))
    saved.append({**other, "receipt_id":other_id})
    outsider = receipt(client, foreign, at(13))
    assert receipt(client, foreign, at(13), receipt_id=outsider["receipt_id"], verb="post") == outsider
    db.initialize()

    # Listing projects only SQLite metadata and leaves both evaluation and archive verification to exact open.
    with monkeypatch.context() as discovery:
        discovery.setattr("research_backend.app.replay_confluence.read_panes", lambda *_: pytest.fail("read archives"))
        keys = ("session_id", "cutoff", "profile", "rule_version", "receipt_id", "status", "reason", "direction",
                "first_available_at")
        expected = [{name: item[name] for name in keys}
                    for item in sorted(saved, key=lambda item: (item["cutoff"], item["receipt_id"]), reverse=True)]
        params = {}
        for index, summary in enumerate(expected):
            actual = listed(at(14), limit=1, **params)
            assert actual["receipts"] == [summary]
            if index < len(expected) - 1:
                assert actual["next_cursor"] == dict(cutoff=summary["cutoff"], receipt_id=summary["receipt_id"])
                params = dict(before_cutoff=summary["cutoff"], before_id=summary["receipt_id"])
            else:
                assert actual["next_cursor"] is None
        early = listed(at(13), selected_id=latest["receipt_id"])
        assert early == dict(receipts=expected[2:], next_cursor=None, selected=None)
        assert listed(at(14), limit=1, selected_id=saved[0]["receipt_id"])["selected"] == expected[-1]
        assert listed(at(14), selected_id=outsider["receipt_id"])["selected"] is None
        other_list = client.get(f"{URL}/{foreign}/confluence/receipts", params=dict(cutoff=at(14))).json()
        assert [item["receipt_id"] for item in other_list["receipts"]] == [outsider["receipt_id"]]
        assert client.get(path, params=dict(cutoff=at(13), before_id=latest["receipt_id"])).status_code == 422
        assert client.get(path, params=dict(cutoff=at(13), before_cutoff=at(14),
                                           before_id=latest["receipt_id"])).status_code == 422
        assert client.get(path, params=dict(cutoff=at(14), limit=49)).status_code == 422
        assert db.one("SELECT count(*) AS n FROM replay_confluence_receipts")["n"] == 5

    (storage.MARKET_DIR/pins[0]["relative_path"]).write_bytes(b"controlled replay archive corruption")
    assert listed(at(14))["receipts"] == expected
    assert request(client, key, latest["cutoff"], receipt_id=latest["receipt_id"]).status_code == 409
