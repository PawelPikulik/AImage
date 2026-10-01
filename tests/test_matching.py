"""End-to-end matching through the real job engine (MockProvider, real DB):
the fox post ranks the fox first; the coral post finds no confident match."""

from __future__ import annotations

from app.db.models import Image, Post
from app.services import suggestions


class TestRanking:
    def test_fox_post_suggests_fox_first(self, seeded_db, settings):
        post = seeded_db.query(Post).filter_by(title="The behavior of red foxes").one()
        result = suggestions.suggest_for_post(seeded_db, settings, post)

        assert result["result"] == "suggested"
        assert result["suggestion"]["filename"].startswith("fox-")
        ranked = {c["filename"]: c for c in result["candidates"]}
        fox_sim = ranked["fox-01.jpg"]["similarity"]
        # Wolf and dog rank clearly lower than the fox.
        assert ranked["wolf-01.jpg"]["similarity"] < fox_sim - 0.15
        assert ranked["dog-01.jpg"]["similarity"] < fox_sim - 0.15
        # The wolf candidate is guard-rejected with a mismatch explanation.
        assert ranked["wolf-01.jpg"]["guard_status"] == "rejected"
        assert any("category mismatch" in r for r in ranked["wolf-01.jpg"]["guard_reasons"])
        # The low-confidence ambiguous image is flagged out, never suggested.
        assert ranked["ambiguous-01.jpg"]["guard_status"] == "rejected"
        assert any("low vision confidence" in r for r in ranked["ambiguous-01.jpg"]["guard_reasons"])

    def test_coral_post_gets_no_confident_match_with_reasons(self, seeded_db, settings):
        post = seeded_db.query(Post).filter_by(title="Coral reefs: the underwater cities").one()
        result = suggestions.suggest_for_post(seeded_db, settings, post)

        assert result["result"] == "no_confident_match"
        assert result["suggestion"] is None
        assert result["no_match_reasons"]  # explained, not silent

    def test_suggestions_are_idempotent(self, seeded_db, settings):
        """Re-querying updates the same rows — no duplicate suggestions."""
        from app.db.models import Suggestion

        post = seeded_db.query(Post).filter_by(title="The behavior of red foxes").one()
        suggestions.suggest_for_post(seeded_db, settings, post)
        first = seeded_db.query(Suggestion).filter_by(post_id=post.id).count()
        suggestions.suggest_for_post(seeded_db, settings, post)  # again
        second = seeded_db.query(Suggestion).filter_by(post_id=post.id).count()
        assert first == second

    def test_ingestion_flags_low_confidence_image(self, seeded_db):
        """PROBE 1 behavior: the ambiguous artwork is flagged, not guessed."""
        ambiguous = seeded_db.query(Image).filter_by(filename="ambiguous-01.jpg").one()
        assert ambiguous.status == "tagged"
        assert ambiguous.flagged_low_confidence is True
        assert ambiguous.confidence < 0.70
