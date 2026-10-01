"""Posts endpoints: create, list, ranked image suggestions, forced guard check."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, settings_dep
from app.config import Settings
from app.db.models import Image, Post
from app.schemas.api import (
    GuardCheckRequest,
    JobCreateResponse,
    PostCreate,
    PostImagesResponse,
    PostOut,
)
from app.services import jobs, suggestions

router = APIRouter(tags=["posts"])


@router.post("/posts", response_model=JobCreateResponse, status_code=202)
def create_post(
    payload: PostCreate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
):
    """Create a post and enqueue its processing job (subject extraction +
    embedding run asynchronously — slow AI work never blocks the request)."""
    existing = db.query(Post).filter(Post.title == payload.title).one_or_none()
    if existing:  # idempotent create: same title returns the existing post's job
        raise HTTPException(status_code=409, detail=f"post titled {payload.title!r} already exists (id={existing.id})")
    post = Post(title=payload.title, body=payload.body, status="pending")
    db.add(post)
    db.flush()
    job = jobs.enqueue_job(db, settings, jobs.KIND_POST, [post.id])
    db.commit()
    return JobCreateResponse(job_id=job.id, kind=job.kind, status=job.status, total=job.total)


@router.get("/posts", response_model=list[PostOut])
def list_posts(db: Session = Depends(get_db)):
    return db.query(Post).order_by(Post.id).all()


@router.get("/posts/{post_id}", response_model=PostOut)
def get_post(post_id: int, db: Session = Depends(get_db)):
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail=f"post {post_id} not found")
    return post


@router.get("/posts/{post_id}/images", response_model=PostImagesResponse)
def post_images(
    post_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
):
    """Ranked image suggestions for a post, with per-candidate guard verdicts.
    Either a top suggestion or 'no_confident_match' with reasons."""
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail=f"post {post_id} not found")
    try:
        return suggestions.suggest_for_post(db, settings, post)
    except suggestions.PostNotProcessed as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/posts/{post_id}/guard-check")
def guard_check(
    post_id: int,
    payload: GuardCheckRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
):
    """Force one specific image through the mismatch guard for this post —
    e.g. 'would the wolf image be accepted for the fox post?' (it would not)."""
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail=f"post {post_id} not found")
    image = db.get(Image, payload.image_id)
    if image is None:
        raise HTTPException(status_code=404, detail=f"image {payload.image_id} not found")
    try:
        return suggestions.guard_check_pair(db, settings, post, image)
    except suggestions.PostNotProcessed as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
