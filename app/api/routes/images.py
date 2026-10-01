"""Images endpoints: inspect the tagged library."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.models import Image

router = APIRouter(tags=["images"])


def _image_out(image: Image) -> dict:
    return {
        "id": image.id,
        "filename": image.filename,
        "category": image.category,
        "status": image.status,
        "subject": image.subject,
        "tag_category": image.tag_category,
        "attributes": image.attributes,
        "caption": image.caption,
        "confidence": image.confidence,
        "flagged_low_confidence": image.flagged_low_confidence,
        "has_embedding": image.embedding is not None,
    }


@router.get("/images")
def list_images(
    flagged: bool | None = Query(default=None, description="true → only low-confidence flagged images"),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    query = db.query(Image).order_by(Image.id)
    if flagged is not None:
        query = query.filter(Image.flagged_low_confidence == flagged)
    if status is not None:
        query = query.filter(Image.status == status)
    return [_image_out(image) for image in query.all()]


@router.get("/images/{image_id}")
def get_image(image_id: int, db: Session = Depends(get_db)):
    image = db.get(Image, image_id)
    if image is None:
        raise HTTPException(status_code=404, detail=f"image {image_id} not found")
    return _image_out(image)
