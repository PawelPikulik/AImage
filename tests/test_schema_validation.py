"""Schema validation: every AI response is checked; invalid → retried/flagged,
never persisted."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.models import Image, Job, JobItem
from app.providers.base import AIProvider, CallUsage, EmbedResult, StructuredResult
from app.providers.mock import MockProvider
from app.schemas.tags import ImageTags
from app.services import jobs


class TestImageTagsSchema:
    def test_valid_payload_accepted(self):
        tags = ImageTags.model_validate(
            {
                "subject": "red fox",
                "category": "animal",
                "attributes": ["orange fur", "wild", "forest"],
                "caption": "A red fox standing in a forest",
                "confidence": 0.94,
            }
        )
        assert tags.subject == "red fox"
        assert tags.confidence == 0.94

    @pytest.mark.parametrize(
        "payload",
        [
            {"category": "animal", "attributes": ["x"], "caption": "long enough caption", "confidence": 0.9},  # missing subject
            {"subject": "red fox", "category": "animal", "attributes": [], "caption": "long enough caption", "confidence": 0.9},  # empty attributes
            {"subject": "red fox", "category": "animal", "attributes": ["x"], "caption": "short", "confidence": 0.9},  # short caption
            {"subject": "red fox", "category": "animal", "attributes": ["x"], "caption": "long enough caption", "confidence": 1.5},  # confidence > 1
            {"subject": "red fox", "category": "animal", "attributes": ["x"], "caption": "long enough caption", "confidence": "high"},  # wrong type
        ],
    )
    def test_invalid_payloads_rejected(self, payload):
        with pytest.raises(ValidationError):
            ImageTags.model_validate(payload)


class BrokenProvider(MockProvider):
    """Returns schema-invalid vision output (confidence out of range)."""

    def classify_image(self, data, mime_type, *, hint=None) -> StructuredResult:
        return StructuredResult(
            data={"subject": "red fox", "category": "animal", "attributes": ["x"],
                  "caption": "A red fox standing in a forest", "confidence": 7.0},
            usage=CallUsage(model="broken-vision", prompt_tokens=10, completion_tokens=5),
        )


class TestInvalidOutputNeverTrusted:
    def test_invalid_vision_output_fails_job_item_and_persists_nothing(
        self, session_factory, settings, mini_corpus, db
    ):
        image = Image(filename="fox-01.jpg", sha256="sha-broken", category="red_fox", status="pending")
        db.add(image)
        db.flush()
        job = jobs.enqueue_job(db, settings, jobs.KIND_IMAGE, [image.id])
        db.commit()

        jobs.run_until_idle(session_factory, BrokenProvider(), settings)

        with session_factory() as check:
            item = check.query(JobItem).filter(JobItem.job_id == job.id).one()
            assert item.status == "failed"
            assert item.attempts == settings.job_max_attempts  # retried to the cap
            assert "SchemaValidationError" in item.last_error
            stored = check.get(Image, image.id)
            assert stored.status == "pending"  # never tagged from invalid output
            assert stored.subject is None
            finished = check.get(Job, job.id)
            assert finished.status == "completed_with_errors"
