"""Schemas that every AI response is validated against before it is trusted.

An invalid model response is never persisted: validation happens at the
provider boundary and failures go through job retry / flagging.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ImageTags(BaseModel):
    """Structured vision output for one image (matches brief Section 4)."""

    subject: str = Field(min_length=2, max_length=80, description='Specific subject, e.g. "red fox"')
    category: str = Field(min_length=2, max_length=40, description='Broad category, e.g. "animal"')
    attributes: list[str] = Field(min_length=1, max_length=8)
    caption: str = Field(min_length=10, max_length=300)
    confidence: float = Field(ge=0.0, le=1.0)


class PostSubjects(BaseModel):
    """Structured extraction of what a post is about."""

    subjects: list[str] = Field(default_factory=list, max_length=6)
    topic_category: str = Field(default="general", max_length=40)


class SchemaValidationError(Exception):
    """Raised when a model response fails schema validation — retried, never trusted."""
