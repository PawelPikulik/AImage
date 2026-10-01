"""Post processing: extract subjects (validated) + embed into the shared space."""

from __future__ import annotations

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Post
from app.providers.base import AIProvider
from app.schemas.tags import PostSubjects, SchemaValidationError
from app.services import costs


def process_post(
    session: Session,
    provider: AIProvider,
    settings: Settings,
    post_id: int,
    *,
    job_id: int | None = None,
) -> Post:
    post = session.get(Post, post_id)
    if post is None:
        raise LookupError(f"post {post_id} not found")

    extracted = provider.extract_post_subjects(post.title, post.body)
    costs.record_cost(
        session, kind="text", usage=extracted.usage, ref_table="posts", ref_id=post.id, job_id=job_id
    )
    try:
        subjects = PostSubjects.model_validate(extracted.data)
    except ValidationError as exc:
        raise SchemaValidationError(f"invalid subject extraction for post {post.id}: {exc}") from exc

    post.subjects = subjects.subjects
    post.topic_category = subjects.topic_category

    embed = provider.embed_texts([f"{post.title}\n\n{post.body}"])
    costs.record_cost(
        session, kind="embedding", usage=embed.usage, ref_table="posts", ref_id=post.id, job_id=job_id
    )
    post.embedding = embed.vectors[0]
    post.status = "processed"
    session.flush()
    return post
