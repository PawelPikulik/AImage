"""The mismatch guard — the decision core of the system.

Per candidate, three checks combine into one explained verdict:

1. SUBJECT CONFLICT (most specific, checked first): if the post clearly names
   a canonical subject (fox) and the image was tagged as a *different* sibling
   subject (wolf), reject with the exact mismatch — "Animal category mismatch:
   expected fox, detected wolf". Alias resolution makes "Vulpes vulpes" ≡ "fox".
2. CONFIDENCE FLOOR: images whose vision confidence fell below the floor were
   flagged at ingestion and are never recommended — "low vision confidence".
3. SIMILARITY THRESHOLD: cosine similarity must clear the tuned threshold from
   config/thresholds.json — otherwise "similarity below threshold".

A rejected candidate carries human-readable reasons; if no candidate is
accepted, the API answers "no confident match" with the top candidates'
reasons. Knowing when the best candidate is still wrong is the whole point.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.db.models import Image, Post
from app.services import textnorm

ACCEPTED = "accepted"
REJECTED = "rejected"


@dataclass
class GuardThresholds:
    match_threshold: float
    confidence_floor: float


@dataclass
class GuardDecision:
    status: str  # ACCEPTED | REJECTED
    reasons: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)


def _image_canonical_subject(image: Image) -> str | None:
    if not image.subject:
        return None
    direct = textnorm.canonical_subject(image.subject)
    if direct:
        return direct
    found = textnorm.subjects_in_text(image.subject)  # e.g. "red fox (Vulpes vulpes)"
    return next(iter(found), None) if len(found) == 1 else None


def _post_canonical_subjects(post: Post) -> set[str]:
    out: set[str] = set()
    for subject in post.subjects or []:
        canon = textnorm.canonical_subject(subject)
        if canon:
            out.add(canon)
        else:
            out.update(textnorm.subjects_in_text(subject))
    if not out:  # extraction found nothing — fall back to scanning the post itself
        out = textnorm.subjects_in_text(f"{post.title} {post.body}")
    return out


def _sibling_category(subjects: set[str]) -> str | None:
    for category, members in textnorm.CATEGORY_SUBJECTS.items():
        if subjects & members:
            return category
    return None


def evaluate(post: Post, image: Image, similarity: float, thresholds: GuardThresholds) -> GuardDecision:
    reasons: list[str] = []
    checks: dict = {}

    # --- 1. Subject conflict -------------------------------------------------
    post_subjects = _post_canonical_subjects(post)
    image_subject = _image_canonical_subject(image)
    checks["post_subjects"] = sorted(post_subjects)
    checks["image_subject"] = image_subject
    if post_subjects:
        if image_subject is None:
            reasons.append(
                f"subject unverifiable: image subject {image.subject!r} is not in the "
                f"known taxonomy, cannot confirm it matches post subject(s) "
                f"{', '.join(sorted(post_subjects))}"
            )
        elif image_subject not in post_subjects:
            category = _sibling_category(post_subjects | {image_subject}) or image.tag_category or "subject"
            expected = ", ".join(sorted(post_subjects))
            reasons.append(
                f"{category.capitalize()} category mismatch: expected {expected}, "
                f"detected {image_subject}"
            )
    checks["subject_check"] = "pass" if not reasons else "fail"

    # --- 2. Confidence floor ---------------------------------------------------
    confidence = image.confidence if image.confidence is not None else 0.0
    checks["confidence"] = confidence
    if image.flagged_low_confidence or confidence < thresholds.confidence_floor:
        reasons.append(
            f"low vision confidence: {confidence:.2f} below floor {thresholds.confidence_floor:.2f} "
            f"- flagged at ingestion, not trusted"
        )

    # --- 3. Similarity threshold ----------------------------------------------
    checks["similarity"] = round(similarity, 4)
    if similarity < thresholds.match_threshold:
        reasons.append(
            f"semantic similarity {similarity:.3f} below threshold {thresholds.match_threshold:.3f}"
        )

    if reasons:
        return GuardDecision(status=REJECTED, reasons=reasons, checks=checks)
    return GuardDecision(
        status=ACCEPTED,
        reasons=[
            f"passed all checks: similarity {similarity:.3f} >= {thresholds.match_threshold:.3f}, "
            f"confidence {confidence:.2f} >= {thresholds.confidence_floor:.2f}, "
            f"subject {image_subject or 'unverified'} consistent with post"
        ],
        checks=checks,
    )
