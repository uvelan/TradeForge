"""Local operator decisions, separate from immutable AI review content."""
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from .db import transaction, utc_now

router=APIRouter(prefix="/api/ai/reviews")
Decision=Literal["ACCEPTED","REJECTED","NEEDS_FOLLOW_UP"]


class ReviewDecision(BaseModel):
    model_config=ConfigDict(extra="forbid")
    decision:Decision
    rationale:Annotated[str,StringConstraints(strip_whitespace=True,min_length=1,max_length=2500)]
    expected_sequence:StrictInt=Field(ge=0)


def require_review(db,review_id):
    if db.execute("SELECT 1 FROM ai_reviews WHERE id=?",(review_id,)).fetchone() is None:
        raise HTTPException(404,"AI review not found")


@router.get("/{review_id}/decisions")
def decision_history(review_id:str,after:int=Query(0,ge=0,le=2**63-1),limit:int=Query(100,ge=1,le=100)):
    with transaction() as db:
        db.execute("BEGIN")
        require_review(db,review_id)
        sequence=db.execute("SELECT COALESCE(MAX(sequence),0) FROM ai_review_decisions WHERE review_id=?",
                            (review_id,)).fetchone()[0]
        rows=[dict(row) for row in db.execute(
            "SELECT * FROM ai_review_decisions WHERE review_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (review_id,after,limit))]
    return {"review_id":review_id,"current_sequence":sequence,"decisions":rows}


@router.post("/{review_id}/decisions",status_code=201)
def append_decision(review_id:str,body:ReviewDecision):
    with transaction() as db:
        db.execute("BEGIN IMMEDIATE")
        require_review(db,review_id)
        current=db.execute(
            "SELECT * FROM ai_review_decisions WHERE review_id=? ORDER BY sequence DESC LIMIT 1",
            (review_id,)).fetchone()
        sequence=current["sequence"] if current else 0
        if body.expected_sequence!=sequence:
            if (current and body.expected_sequence==sequence-1 and body.decision==current["decision"]
                    and body.rationale==current["rationale"]):
                return dict(current)
            raise HTTPException(409,"AI review decision history changed; refresh before recording")
        decision={"review_id":review_id,"sequence":sequence+1,"decision":body.decision,
                  "rationale":body.rationale,"created_at":utc_now()}
        db.execute("INSERT INTO ai_review_decisions VALUES(:review_id,:sequence,:decision,:rationale,:created_at)",decision)
        return decision
