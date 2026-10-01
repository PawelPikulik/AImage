"""Database models. Schema is managed by Alembic migrations (alembic/versions).

Embeddings use pgvector's `Vector` column type. On PostgreSQL this is a real
`vector(768)` with an HNSW index; on SQLite (test runs) it degrades to a plain
'[...]' text affinity column and ranking falls back to in-Python cosine —
production semantics unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBED_DIMS = 768  # keep in sync with EMBED_DIMS / migration 0001


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Image(Base):
    __tablename__ = "images"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(200), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)  # idempotent re-seeding
    category: Mapped[str] = mapped_column(String(40))  # corpus category, e.g. red_fox
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # Vision output (null until tagged)
    subject: Mapped[str | None] = mapped_column(String(80))
    tag_category: Mapped[str | None] = mapped_column(String(40), index=True)
    attributes: Mapped[list | None] = mapped_column(JSON)
    caption: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    flagged_low_confidence: Mapped[bool] = mapped_column(default=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIMS), nullable=True)

    suggestions: Mapped[list["Suggestion"]] = relationship(back_populates="image")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), unique=True)  # idempotent seeding
    body: Mapped[str] = mapped_column(Text)
    subjects: Mapped[list] = mapped_column(JSON, default=list)
    topic_category: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBED_DIMS), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    suggestions: Mapped[list["Suggestion"]] = relationship(back_populates="post")


class Suggestion(Base):
    """A ranked image↔post pairing plus its guard verdict and human review.

    UNIQUE(post_id, image_id) makes re-ranking idempotent: re-querying updates
    the row in place instead of duplicating it.
    """

    __tablename__ = "suggestions"
    __table_args__ = (UniqueConstraint("post_id", "image_id", name="uq_suggestion_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), index=True)
    image_id: Mapped[int] = mapped_column(ForeignKey("images.id"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    similarity: Mapped[float] = mapped_column(Float)
    guard_status: Mapped[str] = mapped_column(String(20))  # accepted | rejected
    guard_reasons: Mapped[list] = mapped_column(JSON, default=list)
    review_status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    review_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    post: Mapped[Post] = relationship(back_populates="suggestions")
    image: Mapped[Image] = relationship(back_populates="suggestions")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))  # image_processing | post_processing
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    items: Mapped[list["JobItem"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobItem(Base):
    """One unit of retryable work. UNIQUE(job_id, ref_id) → retries never
    duplicate an item; `run_after` implements exponential backoff."""

    __tablename__ = "job_items"
    __table_args__ = (UniqueConstraint("job_id", "ref_id", name="uq_job_item"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    ref_id: Mapped[int] = mapped_column(Integer)  # images.id or posts.id
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    job: Mapped[Job] = relationship(back_populates="items")


class AppConfig(Base):
    """Runtime-tunable config shared by api + worker containers and persistent
    across restarts — e.g. the guard's match_threshold written by the tuner."""

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CostLog(Base):
    """One row per AI call — attributed to a record and job, with token usage
    and an estimated USD cost at published list prices (free tier bills $0;
    the tracking habit is the point)."""

    __tablename__ = "cost_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # vision | text | embedding
    model: Mapped[str] = mapped_column(String(60))
    ref_table: Mapped[str | None] = mapped_column(String(20))
    ref_id: Mapped[int | None] = mapped_column(Integer)
    job_id: Mapped[int | None] = mapped_column(Integer, index=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
