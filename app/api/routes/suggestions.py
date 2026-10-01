"""Review workflow: list suggestions, approve, reject — with the guard's
'explain why' preserved on every row."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Suggestion, utcnow
from app.schemas.api import ReviewRequest, SuggestionOut

router = APIRouter(tags=["suggestions"])


@router.get("/suggestions", response_model=list[SuggestionOut])
def list_suggestions(
    status: str | None = Query(default=None, description="pending | approved | rejected"),
    post_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(Suggestion).order_by(Suggestion.post_id, Suggestion.rank)
    if status is not None:
        if status not in ("pending", "approved", "rejected"):
            raise HTTPException(status_code=422, detail="status must be pending|approved|rejected")
        query = query.filter(Suggestion.review_status == status)
    if post_id is not None:
        query = query.filter(Suggestion.post_id == post_id)
    return query.limit(200).all()


def _review(suggestion_id: int, payload: ReviewRequest, decision: str, db: Session) -> Suggestion:
    suggestion = db.get(Suggestion, suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail=f"suggestion {suggestion_id} not found")
    if suggestion.review_status == decision and suggestion.review_reason == payload.reason:
        return suggestion  # idempotent: repeating the same review changes nothing
    suggestion.review_status = decision
    suggestion.review_reason = payload.reason
    suggestion.reviewed_at = utcnow()
    db.commit()
    return suggestion


@router.post("/suggestions/{suggestion_id}/approve", response_model=SuggestionOut)
def approve(suggestion_id: int, payload: ReviewRequest | None = None, db: Session = Depends(get_db)):
    return _review(suggestion_id, payload or ReviewRequest(), "approved", db)


@router.post("/suggestions/{suggestion_id}/reject", response_model=SuggestionOut)
def reject(suggestion_id: int, payload: ReviewRequest | None = None, db: Session = Depends(get_db)):
    return _review(suggestion_id, payload or ReviewRequest(), "rejected", db)
