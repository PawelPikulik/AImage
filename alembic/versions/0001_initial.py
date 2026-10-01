"""initial schema — images, posts, suggestions, jobs, job_items, cost_log

Revision ID: 0001
Revises:
Create Date: 2026-09-30

pgvector-specific DDL (CREATE EXTENSION, vector columns, HNSW indexes) is
guarded to PostgreSQL; SQLite (test runs) gets plain TEXT-affinity columns via
the Vector type's generic DDL and no vector index.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "images",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("filename", sa.String(200), nullable=False, unique=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("subject", sa.String(80)),
        sa.Column("tag_category", sa.String(40)),
        sa.Column("attributes", sa.JSON),
        sa.Column("caption", sa.Text),
        sa.Column("confidence", sa.Float),
        sa.Column("flagged_low_confidence", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("embedding", Vector(768), nullable=True),
    )
    op.create_index("ix_images_status", "images", ["status"])
    op.create_index("ix_images_tag_category", "images", ["tag_category"])

    op.create_table(
        "posts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.String(200), nullable=False, unique=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("subjects", sa.JSON),
        sa.Column("topic_category", sa.String(40)),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_posts_status", "posts", ["status"])

    op.create_table(
        "suggestions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("post_id", sa.Integer, sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("image_id", sa.Integer, sa.ForeignKey("images.id"), nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("similarity", sa.Float, nullable=False),
        sa.Column("guard_status", sa.String(20), nullable=False),
        sa.Column("guard_reasons", sa.JSON),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("review_reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("post_id", "image_id", name="uq_suggestion_pair"),
    )
    op.create_index("ix_suggestions_post_id", "suggestions", ["post_id"])
    op.create_index("ix_suggestions_image_id", "suggestions", ["image_id"])
    op.create_index("ix_suggestions_review_status", "suggestions", ["review_status"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("done", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])

    op.create_table(
        "job_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("ref_id", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
        sa.Column("run_after", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "ref_id", name="uq_job_item"),
    )
    op.create_index("ix_job_items_job_id", "job_items", ["job_id"])
    op.create_index("ix_job_items_status", "job_items", ["status"])
    op.create_index("ix_job_items_run_after", "job_items", ["run_after"])
    op.create_index("ix_job_items_poll", "job_items", ["status", "run_after"])

    op.create_table(
        "app_config",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("value", sa.JSON),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "cost_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("ref_table", sa.String(20)),
        sa.Column("ref_id", sa.Integer),
        sa.Column("job_id", sa.Integer),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_cost_log_created_at", "cost_log", ["created_at"])
    op.create_index("ix_cost_log_kind", "cost_log", ["kind"])
    op.create_index("ix_cost_log_job_id", "cost_log", ["job_id"])

    if _is_postgres():
        # Vector indexes for "find the closest vectors" ranking (cosine).
        op.execute(
            "CREATE INDEX ix_images_embedding ON images USING hnsw (embedding vector_cosine_ops)"
        )
        op.execute(
            "CREATE INDEX ix_posts_embedding ON posts USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    op.drop_table("cost_log")
    op.drop_table("app_config")
    op.drop_table("job_items")
    op.drop_table("jobs")
    op.drop_table("suggestions")
    op.drop_table("posts")
    op.drop_table("images")
    if _is_postgres():
        op.execute("DROP EXTENSION IF EXISTS vector")
