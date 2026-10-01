"""API boundary tests: bad input → clean 4xx (never 500), the suggestion flow,
the review workflow, and the forced guard check (PROBE 3)."""

from __future__ import annotations

import pathlib

import pytest

from app.db.models import Image, Post
from app.services import jobs

from .conftest import FOX_POST, _seed_rows, _write_fake_jpeg


@pytest.fixture()
def seeded_client(client, session_factory, settings, provider, mini_corpus):
    """API client whose DB has been seeded + processed via the job engine."""
    with session_factory() as session:
        refs = _seed_rows(session)
        jobs.enqueue_job(session, settings, jobs.KIND_IMAGE, [i.id for i in refs["images"].values()])
        jobs.enqueue_job(session, settings, jobs.KIND_POST, [p.id for p in refs["posts"].values()])
        session.commit()
    jobs.run_until_idle(session_factory, provider, settings)
    return client


class TestBoundaryValidation:
    def test_create_post_rejects_short_title(self, client):
        resp = client.post("/posts", json={"title": "ab", "body": "long enough body text"})
        assert resp.status_code == 422
        assert resp.json()["detail"] == "validation error"

    def test_create_post_rejects_missing_body(self, client):
        resp = client.post("/posts", json={"title": "A perfectly fine title"})
        assert resp.status_code == 422

    def test_unknown_post_is_404(self, client):
        assert client.get("/posts/9999").status_code == 404
        assert client.get("/posts/9999/images").status_code == 404

    def test_unknown_suggestion_review_is_404(self, client):
        assert client.post("/suggestions/9999/approve", json={}).status_code == 404

    def test_bad_suggestions_status_filter_is_422(self, client):
        assert client.get("/suggestions?status=bogus").status_code == 422

    def test_malformed_body_is_422_not_500(self, seeded_client, session_factory):
        """Regression: raw bytes in pydantic errors must not break the handler."""
        with session_factory() as session:
            post_id = session.query(Post).filter_by(title=FOX_POST["title"]).one().id
        resp = seeded_client.post(
            f"/posts/{post_id}/guard-check",
            content=b"'{image_id:",  # mangled body, not an object
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] == "validation error"

    def test_unprocessed_post_images_is_409(self, client, session_factory):
        with session_factory() as session:
            post = Post(title="Unprocessed post title", body="Never sent through the job.", status="pending")
            session.add(post)
            session.commit()
            post_id = post.id
        resp = client.get(f"/posts/{post_id}/images")
        assert resp.status_code == 409  # honest state, not a 500


class TestSuggestionFlow:
    def test_fox_post_ranks_fox_first_and_rejects_wolf(self, seeded_client, session_factory):
        with session_factory() as session:
            post_id = session.query(Post).filter_by(title=FOX_POST["title"]).one().id
        resp = seeded_client.get(f"/posts/{post_id}/images")
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "suggested"
        assert data["suggestion"]["filename"].startswith("fox-")
        wolf = next(c for c in data["candidates"] if c["filename"] == "wolf-01.jpg")
        assert wolf["guard_status"] == "rejected"
        assert any("category mismatch" in r for r in wolf["guard_reasons"])

    def test_no_confident_match_for_coral_post(self, seeded_client, session_factory):
        with session_factory() as session:
            post_id = session.query(Post).filter_by(title="Coral reefs: the underwater cities").one().id
        resp = seeded_client.get(f"/posts/{post_id}/images")
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "no_confident_match"
        assert data["suggestion"] is None
        assert data["no_match_reasons"]

    def test_forced_guard_check_rejects_wolf_with_explanation(self, seeded_client, session_factory):
        """PROBE 3: force the wolf as a candidate for the fox post."""
        with session_factory() as session:
            post_id = session.query(Post).filter_by(title=FOX_POST["title"]).one().id
            wolf_id = session.query(Image).filter_by(filename="wolf-01.jpg").one().id
        resp = seeded_client.post(f"/posts/{post_id}/guard-check", json={"image_id": wolf_id})
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "REJECTED"
        assert any("category mismatch: expected fox, detected wolf" in r for r in data["reasons"])


class TestReviewWorkflow:
    def test_approve_is_idempotent_and_listed(self, seeded_client, session_factory):
        with session_factory() as session:
            post_id = session.query(Post).filter_by(title=FOX_POST["title"]).one().id
        seeded_client.get(f"/posts/{post_id}/images")  # materialize suggestions
        pending = seeded_client.get("/suggestions?status=pending").json()
        assert pending
        target = next(s for s in pending if s["guard_status"] == "accepted")

        first = seeded_client.post(f"/suggestions/{target['id']}/approve", json={"reason": "correct pairing"})
        assert first.status_code == 200
        assert first.json()["review_status"] == "approved"
        second = seeded_client.post(f"/suggestions/{target['id']}/approve", json={"reason": "correct pairing"})
        assert second.status_code == 200  # idempotent repeat, same state

        approved = seeded_client.get("/suggestions?status=approved").json()
        assert any(s["id"] == target["id"] for s in approved)

    def test_reject_with_reason(self, seeded_client, session_factory):
        with session_factory() as session:
            post_id = session.query(Post).filter_by(title=FOX_POST["title"]).one().id
        seeded_client.get(f"/posts/{post_id}/images")
        rejected_guard = [
            s for s in seeded_client.get("/suggestions?status=pending").json()
            if s["guard_status"] == "rejected"
        ]
        assert rejected_guard  # the wolf suggestion exists with its explanation
        resp = seeded_client.post(
            f"/suggestions/{rejected_guard[0]['id']}/reject", json={"reason": "wolf is not a fox"}
        )
        assert resp.status_code == 200
        assert resp.json()["review_status"] == "rejected"
        assert resp.json()["guard_reasons"]  # 'why' is inspectable on the row


class TestCostsEndpoint:
    def test_cost_summary_reflects_calls(self, seeded_client):
        resp = seeded_client.get("/costs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_calls"] == 12  # 4 images ×2 + 2 posts ×2
        assert data["total_estimated_cost_usd"] > 0
