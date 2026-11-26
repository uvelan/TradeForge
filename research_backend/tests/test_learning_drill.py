from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

PATH = "/api/learning/lessons/smt-swing-timing-v1"
WRONG = {"choice": "confirmed", "explanation": "The EURUSD high was above the preceding high."}
RIGHT = {"choice": "not_yet", "explanation": "The right-hand candle is not observable yet."}


def test_blind_until_committed_and_restored_after_restart(client):
    page = client.get(PATH).json()
    assert page["attempt"] is None
    assert page["lesson"]["source"] == "SYNTHETIC_LEARNING_ONLY"
    assert [c["open_utc"] for c in page["lesson"]["candles"]] == ["09:50", "09:55", "10:00"]
    assert "answer" not in str(page) and "next_candle" not in str(page)
    assert client.post(PATH + "/hypothesis", json=WRONG | {"explanation": " " * 12}).status_code == 422
    assert client.get(PATH).json() == page
    assert client.get("/api/journal").json() == []

    submit = client.post(PATH + "/hypothesis", json=WRONG)
    assert submit.status_code == 200
    progress = submit.json()
    assert progress["correct"] is False and progress["reveal"]["answer"] == "not_yet"
    assert progress["reveal"]["next_candle"]["open_utc"] == "10:05"
    assert client.post(PATH + "/hypothesis", json=WRONG).json() == progress
    assert client.post(PATH + "/hypothesis", json=RIGHT).status_code == 409
    assert client.get("/api/health").json()["execution_enabled"] is False

    # New HTTP app lifespan, same SQLite path, without any client-side state.
    with TestClient(client.app) as restarted:
        assert restarted.get(PATH).json()["attempt"] == progress
        entry = restarted.get("/api/journal/entries/" + progress["journal_id"]).json()
        assert entry["lesson_id"] == page["lesson"]["id"]
        assert entry["kind"] == "CHART"
        assert entry.get("signal_reference") is None
        assert WRONG["explanation"] in entry["content"]
        assert "not a trade signal" in entry["content"]
        assert len(restarted.get("/api/journal").json()) == 1
        assert WRONG["explanation"] in restarted.get("/api/journal/download").text


def test_competing_hypotheses_have_one_journal_event(client):
    def send(payload):
        return client.post(PATH + "/hypothesis", json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, [WRONG, RIGHT]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    progress = next(r.json() for r in responses if r.status_code == 200)
    assert client.get(PATH).json()["attempt"] == progress
    assert [entry["id"] for entry in client.get("/api/journal").json()] == [progress["journal_id"]]


def test_journal_is_rolled_back_if_attempt_cannot_be_committed(client):
    from research_backend.app.db import transaction

    with transaction() as db:
        db.execute("""CREATE TRIGGER reject_fixture_attempt BEFORE INSERT ON learning_attempts
                      BEGIN SELECT RAISE(ABORT, 'fixture commit refused'); END""")
    with TestClient(client.app, raise_server_exceptions=False) as probe:
        assert probe.post(PATH + "/hypothesis", json=RIGHT).status_code == 500
        assert probe.get(PATH).json()["attempt"] is None
        assert probe.get("/api/journal").json() == []
    with transaction() as db:
        db.execute("DROP TRIGGER reject_fixture_attempt")
    accepted = client.post(PATH + "/hypothesis", json=RIGHT).json()
    assert accepted["correct"] is True
    assert accepted["journal_id"] == "CHART-00001"
