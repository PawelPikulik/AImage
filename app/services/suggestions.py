"""Suggestion orchestration: rank candidates, run the guard on each, persist
verdicts idempotently, and shape the explained API/eval response."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings, load_thresholds
from app.db.models import AppConfig, Image, Post, Suggestion
from app.services import guard, matching


class PostNotProcessed(Exception):
    """The post has no embedding yet — its processing job hasn't run."""


def resolve_match_threshold(session: Session, settings: Settings) -> float:
    """Precedence: MATCH_THRESHOLD env override → DB (tuner-written) →
    config/thresholds.json file default."""
    if settings.match_threshold.strip():
        return float(settings.match_threshold)
    row = session.get(AppConfig, "match_threshold")
    if row is not None:
        return float(row.value["value"])
    return load_thresholds()["match_threshold"]


def _thresholds(session: Session, settings: Settings) -> guard.GuardThresholds:
    return guard.GuardThresholds(
        match_threshold=resolve_match_threshold(session, settings),
        confidence_floor=settings.confidence_floor,
    )


def _upsert_suggestion(
    session: Session,
    *,
    post: Post,
    image: Image,
    rank: int,
    similarity: float,
    decision: guard.GuardDecision,
) -> Suggestion:
    """UNIQUE(post_id, image_id) → re-ranking updates in place; human review
    state (review_status / review_reason) is preserved across re-ranks."""
    suggestion = (
        session.query(Suggestion)
        .filter(Suggestion.post_id == post.id, Suggestion.image_id == image.id)
        .one_or_none()
    )
    if suggestion is None:
        suggestion = Suggestion(post_id=post.id, image_id=image.id)
        session.add(suggestion)
    suggestion.rank = rank
    suggestion.similarity = round(similarity, 6)
    suggestion.guard_status = decision.status
    suggestion.guard_reasons = decision.reasons
    session.flush()
    return suggestion


def _candidate_payload(image: Image, similarity: float, decision: guard.GuardDecision) -> dict:
    return {
        "image_id": image.id,
        "filename": image.filename,
        "subject": image.subject,
        "caption": image.caption,
        "confidence": image.confidence,
        "similarity": round(similarity, 4),
        "guard_status": decision.status,
        "guard_reasons": decision.reasons,
    }


def suggest_for_post(session: Session, settings: Settings, post: Post) -> dict:
    """Rank + guard every candidate for a post; persist suggestion rows."""
    if post.embedding is None:
        raise PostNotProcessed(f"post {post.id} has not been processed yet")

    thresholds = _thresholds(session, settings)
    ranked = matching.rank_images(session, list(post.embedding), limit=settings.ranking_candidates)

    candidates: list[dict] = []
    suggestion_payload: dict | None = None
    for rank, (image, similarity) in enumerate(ranked, start=1):
        decision = guard.evaluate(post, image, similarity, thresholds)
        _upsert_suggestion(
            session, post=post, image=image, rank=rank, similarity=similarity, decision=decision
        )
        payload = _candidate_payload(image, similarity, decision)
        candidates.append(payload)
        if suggestion_payload is None and decision.status == guard.ACCEPTED:
            suggestion_payload = payload
    session.commit()

    no_match_reasons: list[str] = []
    if suggestion_payload is None:
        for candidate in candidates[:3]:  # explain why the best candidates failed
            no_match_reasons.append(
                f"{candidate['filename']}: {'; '.join(candidate['guard_reasons'])}"
            )
        if not candidates:
            no_match_reasons.append("no tagged images available — run the image-processing job first")

    return {
        "post_id": post.id,
        "post_title": post.title,
        "post_subjects": post.subjects or [],
        "result": "suggested" if suggestion_payload else "no_confident_match",
        "suggestion": suggestion_payload,
        "no_match_reasons": no_match_reasons,
        "candidates": candidates,
    }


def guard_check_pair(session: Session, settings: Settings, post: Post, image: Image) -> dict:
    """Force one specific post↔image pair through the guard (PROBE 3 — e.g.
    'would you accept the wolf for the fox post?'). Persists the verdict."""
    if post.embedding is None:
        raise PostNotProcessed(f"post {post.id} has not been processed yet")
    if image.embedding is None:
        raise LookupError(f"image {image.id} has not been processed yet")

    similarity = matching.cosine_similarity(list(post.embedding), list(image.embedding))
    decision = guard.evaluate(post, image, similarity, _thresholds(session, settings))
    # Rank 0 = off-list forced check.
    _upsert_suggestion(
        session, post=post, image=image, rank=0, similarity=similarity, decision=decision
    )
    session.commit()
    payload = _candidate_payload(image, similarity, decision)
    return {
        "post_id": post.id,
        "post_title": post.title,
        "candidate": payload,
        "verdict": decision.status.upper(),
        "reasons": decision.reasons,
    }
