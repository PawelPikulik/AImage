"""Vision processing for a single image.

Flow: read bytes → provider.classify_image → cost log → Pydantic schema
validation → persist tags (low confidence is FLAGGED, not trusted) → embed
caption → cost log. A schema-invalid response raises SchemaValidationError so
the job engine retries it — invalid model output is never persisted.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Image
from app.providers.base import AIProvider
from app.schemas.tags import ImageTags, SchemaValidationError
from app.services import costs


def process_image(
    session: Session,
    provider: AIProvider,
    settings: Settings,
    image_id: int,
    *,
    job_id: int | None = None,
) -> Image:
    image = session.get(Image, image_id)
    if image is None:
        raise LookupError(f"image {image_id} not found")

    path = Path(settings.corpus_dir) / image.filename
    if not path.exists():
        # Permanent, not transient: retrying cannot fix a missing file.
        raise LookupError(f"corpus file missing: {path} — run scripts/download_corpus.py")

    result = provider.classify_image(path.read_bytes(), "image/jpeg", hint=image.filename)
    costs.record_cost(
        session, kind="vision", usage=result.usage, ref_table="images", ref_id=image.id, job_id=job_id
    )

    try:
        tags = ImageTags.model_validate(result.data)
    except ValidationError as exc:
        raise SchemaValidationError(f"invalid vision output for {image.filename}: {exc}") from exc

    image.subject = tags.subject
    image.tag_category = tags.category
    image.attributes = tags.attributes
    image.caption = tags.caption
    image.confidence = tags.confidence
    # Low confidence → flagged for review instead of silently accepted.
    image.flagged_low_confidence = tags.confidence < settings.confidence_floor

    embed = provider.embed_texts([f"{tags.subject}. {tags.caption}"])
    costs.record_cost(
        session, kind="embedding", usage=embed.usage, ref_table="images", ref_id=image.id, job_id=job_id
    )
    image.embedding = embed.vectors[0]
    image.status = "tagged"
    session.flush()
    return image
