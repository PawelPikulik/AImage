"""HTTP boundary schemas — bad input is rejected here with clean 4xx errors."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PostCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=10, max_length=20_000)


class PostOut(BaseModel):
    id: int
    title: str
    body: str
    subjects: list[str]
    topic_category: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class JobCreateResponse(BaseModel):
    job_id: int
    kind: str
    status: str
    total: int


class JobOut(BaseModel):
    id: int
    kind: str
    status: str
    total: int
    done: int
    failed: int
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GuardCheckRequest(BaseModel):
    image_id: int


class ReviewRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class SuggestionOut(BaseModel):
    id: int
    post_id: int
    image_id: int
    rank: int
    similarity: float
    guard_status: str
    guard_reasons: list[str]
    review_status: str
    review_reason: str | None

    model_config = {"from_attributes": True}


class CandidateOut(BaseModel):
    image_id: int
    filename: str
    subject: str | None
    caption: str | None
    confidence: float | None
    similarity: float
    guard_status: str
    guard_reasons: list[str]


class PostImagesResponse(BaseModel):
    post_id: int
    post_title: str
    post_subjects: list[str]
    result: str  # "suggested" | "no_confident_match"
    suggestion: CandidateOut | None
    no_match_reasons: list[str]
    candidates: list[CandidateOut]


class CostSummary(BaseModel):
    total_calls: int
    total_estimated_cost_usd: float
    budget_usd: float
    budget_remaining_usd: float
    by_kind: dict[str, dict]
