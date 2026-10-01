"""Semantic ranking: image vectors × post vector, cosine similarity.

On PostgreSQL the ranking runs inside the database via pgvector's `<=>`
cosine distance operator (backed by an HNSW index). On SQLite — used only by
the offline test suite — candidates are ranked by the same cosine metric
computed in Python; the returned ranking contract is identical.
"""

from __future__ import annotations

import math

from sqlalchemy.orm import Session

from app.db.models import Image


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def rank_images(
    session: Session,
    post_embedding: list[float],
    *,
    limit: int = 10,
) -> list[tuple[Image, float]]:
    """Return [(image, cosine_similarity)] best-first, over tagged images."""
    if session.get_bind().dialect.name == "postgresql":
        distance = Image.embedding.cosine_distance(post_embedding).label("distance")
        rows = (
            session.query(Image, distance)
            .filter(Image.embedding.isnot(None), Image.status == "tagged")
            .order_by(distance)
            .limit(limit)
            .all()
        )
        return [(image, 1.0 - float(dist)) for image, dist in rows]

    rows = (
        session.query(Image)
        .filter(Image.embedding.isnot(None), Image.status == "tagged")
        .all()
    )
    scored = [(image, cosine_similarity(post_embedding, list(image.embedding))) for image in rows]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]
