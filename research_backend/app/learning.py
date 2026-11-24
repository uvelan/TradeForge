"""One immutable, synthetic, blind SMT exercise; never a signal feed."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .db import one, transaction, utc_now
from .domain import journal_materialize

router = APIRouter(prefix="/api/learning", tags=["learning"])

LESSON = {
    "id": "smt-swing-timing-v1",
    "title": "Blind SMT drill: when is the divergence known?",
    "source": "SYNTHETIC_LEARNING_ONLY",
    "objective": "Separate a candidate pivot from a confirmed comparison across two instruments.",
    "prompt": "At 10:05 UTC the new candles close. Are both new M5 swing highs confirmed?",
    "candles": [
        {"open_utc": "09:50", "eurusd_high": "1.1030", "gbpusd_high": "1.2430"},
        {"open_utc": "09:55", "eurusd_high": "1.1025", "gbpusd_high": "1.2425"},
        {"open_utc": "10:00", "eurusd_high": "1.1035", "gbpusd_high": "1.2420"},
    ],
    "context": (
        "In this drill, the prior confirmed highs were EURUSD 1.1030 and GBPUSD 1.2430. "
        "A new swing high must exceed the high immediately before and after it. "
        "This is a simplified teaching rule, not the production SMT algorithm."
    ),
    "choices": [
        {"id": "confirmed", "label": "Yes, this is confirmed SMT."},
        {"id": "not_yet", "label": "No, wait for the next closed candle."},
    ],
}
REVEAL = {
    "answer": "not_yet",
    "explanation": (
        "A higher candidate on EURUSD is not a confirmed divergence. At the cutoff, the right-side 10:05 M5 candle "
        "has not closed, and GBPUSD's 10:00 candle is below even its immediate left neighbor. "
        "The fixture's 10:05 candle closes at 10:10. That confirms the EURUSD swing under the drill rule, "
        "but does not establish a paired GBPUSD swing. Skip the signal; this is not an entry instruction."
    ),
    "next_candle": {"open_utc": "10:05", "eurusd_high": "1.1028", "gbpusd_high": "1.2423"},
}


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choice: Literal["confirmed", "not_yet"]
    explanation: str = Field(min_length=10, max_length=1000)

    @field_validator("explanation", mode="before")
    @classmethod
    def strip_space(cls, text):
        return text.strip() if isinstance(text, str) else text


def check_lesson(lesson_id):
    if lesson_id != LESSON["id"]:
        raise HTTPException(404, "Lesson unavailable")


def result(row):
    return {
        "choice": row["choice"], "explanation": row["hypothesis"],
        "submitted_at": row["submitted_at"], "journal_id": row["journal_id"],
        "correct": row["choice"] == REVEAL["answer"], "reveal": REVEAL,
    }


@router.get("/lessons/{lesson_id}")
def lesson(lesson_id: str):
    check_lesson(lesson_id)
    row = one("SELECT * FROM learning_attempts WHERE lesson_id=?", (lesson_id,))
    return {"lesson": LESSON, "attempt": result(row) if row else None}


@router.post("/lessons/{lesson_id}/hypothesis")
def submit(lesson_id: str, hypothesis: Hypothesis):
    check_lesson(lesson_id)
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM learning_attempts WHERE lesson_id=?", (lesson_id,)).fetchone()
        if row:
            if (row["choice"], row["hypothesis"]) != (hypothesis.choice, hypothesis.explanation):
                raise HTTPException(409, "A hypothesis is already recorded; reload to review it.")
        else:
            number = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM journal_entries WHERE kind='CHART'").fetchone()[0]
            journal_id = f"CHART-{number:05d}"
            now = utc_now()
            choice_text = next(option["label"] for option in LESSON["choices"] if option["id"] == hypothesis.choice)
            content = (
                f"Synthetic learning exercise ({lesson_id}); not a trade signal.\n\n"
                f"Submitted: {choice_text}\nHypothesis: {hypothesis.explanation}\n\n"
                f"Review: {REVEAL['explanation']}"
            )
            db.execute("INSERT INTO journal_entries VALUES(?,?,?,?,?,?)",
                       (journal_id, "CHART", number, LESSON["title"], content, now))
            db.execute("INSERT INTO journal_entry_context VALUES(?, json_object('lesson_id', ?, 'tags', json_array('learning', 'synthetic')))",
                       (journal_id, lesson_id))
            db.execute("INSERT INTO learning_attempts VALUES(?,?,?,?,?)",
                       (lesson_id, hypothesis.choice, hypothesis.explanation, journal_id, now))
            row = db.execute("SELECT * FROM learning_attempts WHERE lesson_id=?", (lesson_id,)).fetchone()
    # SQLite is authoritative. Repeating a committed submit retries regenerating the derived Markdown.
    journal_materialize()
    return result(row)
