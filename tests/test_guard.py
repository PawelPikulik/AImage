"""Mismatch guard unit tests — the decision core.

The brief's contract: a wolf must never be accepted for a fox post, the
rejection must explain itself, and 'no candidate is good enough' is a valid
answer.
"""

from __future__ import annotations

from app.db.models import Image, Post
from app.services import guard
from app.services.guard import GuardThresholds

THRESHOLDS = GuardThresholds(match_threshold=0.25, confidence_floor=0.70)


def _post(subjects, title="The behavior of red foxes", body="About foxes.") -> Post:
    return Post(id=1, title=title, body=body, subjects=subjects, status="processed")


def _image(subject, confidence=0.94, flagged=False, tag_category="animal") -> Image:
    return Image(
        id=1,
        filename="x.jpg",
        sha256="x",
        category="red_fox",
        status="tagged",
        subject=subject,
        tag_category=tag_category,
        attributes=["a"],
        caption="c",
        confidence=confidence,
        flagged_low_confidence=flagged,
    )


class TestSubjectConflict:
    def test_wolf_rejected_for_fox_post_with_explanation(self):
        decision = guard.evaluate(_post(["fox"]), _image("gray wolf"), 0.80, THRESHOLDS)
        assert decision.status == guard.REJECTED
        assert any("category mismatch" in r for r in decision.reasons)
        assert any("expected fox" in r and "detected wolf" in r for r in decision.reasons)

    def test_fox_accepted_for_fox_post(self):
        decision = guard.evaluate(_post(["fox"]), _image("red fox"), 0.60, THRESHOLDS)
        assert decision.status == guard.ACCEPTED
        assert decision.reasons  # acceptance is explained too

    def test_latin_alias_matches(self):
        """'Vulpes vulpes' in the post ≡ 'red fox' on the image — concept, not words."""
        decision = guard.evaluate(_post(["Vulpes vulpes"]), _image("red fox"), 0.60, THRESHOLDS)
        assert decision.status == guard.ACCEPTED

    def test_dog_rejected_for_fox_post(self):
        decision = guard.evaluate(_post(["fox"]), _image("domestic dog"), 0.55, THRESHOLDS)
        assert decision.status == guard.REJECTED
        assert any("detected dog" in r for r in decision.reasons)


class TestConfidenceFloor:
    def test_low_confidence_flagged_image_rejected(self):
        decision = guard.evaluate(_post(["fox"]), _image("red fox", confidence=0.45, flagged=True), 0.90, THRESHOLDS)
        assert decision.status == guard.REJECTED
        assert any("low vision confidence" in r for r in decision.reasons)


class TestSimilarityThreshold:
    def test_below_threshold_rejected(self):
        decision = guard.evaluate(_post(["fox"]), _image("red fox"), 0.10, THRESHOLDS)
        assert decision.status == guard.REJECTED
        assert any("below threshold" in r for r in decision.reasons)

    def test_post_without_subjects_falls_back_to_threshold(self):
        """Coral-reefs post: no known subject → similarity bar alone decides."""
        post = _post([], title="Coral reefs", body="Parrotfish and clownfish.")
        weak = guard.evaluate(post, _image("red fox"), 0.05, THRESHOLDS)
        assert weak.status == guard.REJECTED
        assert any("below threshold" in r for r in weak.reasons)

    def test_multiple_reasons_collected(self):
        """A bad candidate can fail several checks; all reasons are reported."""
        decision = guard.evaluate(_post(["fox"]), _image("gray wolf", confidence=0.40, flagged=True), 0.05, THRESHOLDS)
        assert decision.status == guard.REJECTED
        assert len(decision.reasons) == 3  # mismatch + low confidence + below threshold
