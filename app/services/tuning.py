"""Threshold tuning against the labeled eval set.

Labels are subject-level ("this post's correct image is a fox image") because
the corpus holds several equally-correct images per subject — with 12 fox
photos, pinning one exact file per post would measure luck, not quality.
Top-1 precision = share of labeled posts whose top suggestion has the labeled
canonical subject.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.config import EVAL_SET_PATH, THRESHOLDS_PATH, Settings
from app.db.models import AppConfig, Post
from app.services import guard, matching, textnorm


def _canonical_of_subject(subject: str | None) -> str | None:
    if not subject:
        return None
    direct = textnorm.canonical_subject(subject)
    if direct:
        return direct
    found = textnorm.subjects_in_text(subject)
    return next(iter(found), None) if len(found) == 1 else None


def sweep(session: Session, settings: Settings, threshold: float) -> dict:
    """Precision + rejection accuracy at one threshold, over the eval set."""
    entries = json.loads(EVAL_SET_PATH.read_text(encoding="utf-8"))["entries"]
    thresholds = guard.GuardThresholds(
        match_threshold=threshold, confidence_floor=settings.confidence_floor
    )
    hits = total = rej_pass = rej_total = 0
    for entry in entries:
        post = session.query(Post).filter(Post.title == entry["post_title"]).one_or_none()
        if post is None or post.embedding is None:
            continue
        ranked = matching.rank_images(session, list(post.embedding), limit=settings.ranking_candidates)
        accepted = [
            (image, sim)
            for image, sim in ranked
            if guard.evaluate(post, image, sim, thresholds).status == guard.ACCEPTED
        ]
        if entry.get("expected_rejection"):
            rej_total += 1
            rej_pass += 0 if accepted else 1
            continue
        total += 1
        if accepted and _canonical_of_subject(accepted[0][0].subject) == entry["expected_subject"]:
            hits += 1
    return {
        "top1_precision": hits / total if total else 0.0,
        "rejection_accuracy": rej_pass / rej_total if rej_total else 1.0,
        "labeled_posts": total,
        "rejection_posts": rej_total,
    }


def tune(session: Session, settings: Settings, *, write_file: bool = True) -> dict:
    """Pick the best threshold on the eval set; persist to DB (authoritative)
    and to config/thresholds.json (repo provenance, best-effort)."""
    best: dict | None = None
    for i in range(5, 96):  # 0.05 … 0.95
        threshold = i / 100.0
        result = sweep(session, settings, threshold)
        key = (result["top1_precision"], result["rejection_accuracy"], -threshold)
        if best is None or key > (best["top1_precision"], best["rejection_accuracy"], -best["threshold"]):
            best = {"threshold": threshold, **result}
    assert best is not None

    row = session.get(AppConfig, "match_threshold") or AppConfig(key="match_threshold")
    row.value = {
        "value": round(best["threshold"], 2),
        "top1_precision": round(best["top1_precision"], 3),
        "rejection_accuracy": round(best["rejection_accuracy"], 3),
        "provider": "mock" if settings.mock_ai else "gemini",
    }
    session.add(row)
    session.flush()

    if write_file:
        payload = {
            "match_threshold": round(best["threshold"], 2),
            "confidence_floor": settings.confidence_floor,
            "tuned_by": "scripts/tune_thresholds.py against data/eval_set.json",
            "tuned_result": row.value,
        }
        try:
            THRESHOLDS_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass  # read-only filesystem (container) — DB row is authoritative
    return best
