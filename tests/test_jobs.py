"""Batch job engine: retries with backoff, idempotent enqueue, failure cap,
and cost rows surviving failed attempts (a failed call still costs money)."""

from __future__ import annotations

from app.db.models import CostLog, Image, Job, JobItem, Post
from app.providers.base import CallUsage, EmbedResult, ProviderError, StructuredResult
from app.providers.mock import MockProvider
from app.services import jobs


class FlakyOnceProvider(MockProvider):
    """Fails the first vision call with a retryable error, then recovers."""

    def __init__(self, dims: int = 768):
        super().__init__(dims)
        self.calls = 0

    def classify_image(self, data, mime_type, *, hint=None) -> StructuredResult:
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("429 rate limited", retryable=True)
        return super().classify_image(data, mime_type, hint=hint)


class AlwaysBrokenEmbedder(MockProvider):
    """Vision works; embedding calls always fail (permanent 400)."""

    def embed_texts(self, texts) -> EmbedResult:
        raise ProviderError("400 bad request", retryable=False)


def _one_image(db) -> Image:
    image = Image(filename="fox-01.jpg", sha256="sha-job", category="red_fox", status="pending")
    db.add(image)
    db.flush()
    return image


class TestRetries:
    def test_retryable_failure_is_retried_and_recovers(
        self, session_factory, settings, mini_corpus, db
    ):
        image = _one_image(db)
        job = jobs.enqueue_job(db, settings, jobs.KIND_IMAGE, [image.id])
        db.commit()
        provider = FlakyOnceProvider()

        jobs.run_until_idle(session_factory, provider, settings)

        with session_factory() as check:
            item = check.query(JobItem).filter_by(job_id=job.id).one()
            assert item.status == "done"
            assert item.attempts == 1  # one failed attempt before success
            assert "429" in item.last_error
            assert check.get(Image, image.id).status == "tagged"
            assert check.get(Job, job.id).status == "completed"

    def test_permanent_failure_stops_without_burning_retries(
        self, session_factory, settings, mini_corpus, db
    ):
        image = _one_image(db)
        job = jobs.enqueue_job(db, settings, jobs.KIND_IMAGE, [image.id])
        db.commit()

        jobs.run_until_idle(session_factory, AlwaysBrokenEmbedder(), settings)

        with session_factory() as check:
            item = check.query(JobItem).filter_by(job_id=job.id).one()
            assert item.status == "failed"
            assert item.attempts == 1  # non-retryable → no wasted retries
            # The vision call succeeded before the embedding call raised: its
            # cost row survives the failed item (a spent call stays logged).
            kinds = [row.kind for row in check.query(CostLog).filter_by(job_id=job.id)]
            assert kinds == ["vision"]


    def test_drain_waits_for_backoff_retries(self, session_factory, settings, mini_corpus, db):
        """Regression: run_until_idle must not exit while retries are scheduled
        in the future (found in the live run: a 429 storm left items pending)."""
        import time

        settings.job_retry_base_seconds = 0.5  # first retry lands 0.5s in the future
        image = _one_image(db)
        job = jobs.enqueue_job(db, settings, jobs.KIND_IMAGE, [image.id])
        db.commit()

        started = time.monotonic()
        jobs.run_until_idle(session_factory, FlakyOnceProvider(), settings)
        elapsed = time.monotonic() - started

        with session_factory() as check:
            item = check.query(JobItem).filter_by(job_id=job.id).one()
            assert item.status == "done"  # retried and recovered, not abandoned
            assert elapsed >= 0.4  # proof it actually waited out the backoff


class TestIdempotentEnqueue:
    def test_double_enqueue_while_active_creates_no_duplicates(
        self, session_factory, settings, mini_corpus, db
    ):
        image = _one_image(db)
        first = jobs.enqueue_job(db, settings, jobs.KIND_IMAGE, [image.id])
        second = jobs.enqueue_job(db, settings, jobs.KIND_IMAGE, [image.id])
        db.commit()
        assert first.total == 1
        assert second.total == 0  # already queued in the active job

    def test_post_job_processes_subjects_and_embedding(
        self, session_factory, settings, provider, db
    ):
        post = Post(title="Why wolves howl", body="A gray wolf howls to rally the pack.", status="pending")
        db.add(post)
        db.flush()
        jobs.enqueue_job(db, settings, jobs.KIND_POST, [post.id])
        db.commit()

        jobs.run_until_idle(session_factory, provider, settings)

        with session_factory() as check:
            stored = check.get(Post, post.id)
            assert stored.status == "processed"
            assert stored.subjects == ["wolf"]  # canonical subject extracted
            assert stored.embedding is not None
