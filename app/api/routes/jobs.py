"""Job endpoints: enqueue the batch vision job, inspect progress."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, settings_dep
from app.config import Settings
from app.db.models import Image, Job
from app.schemas.api import JobCreateResponse, JobOut
from app.services import costs, jobs

router = APIRouter(tags=["jobs"])


class ImageJobRequest(BaseModel):
    force: bool = False  # re-tag already-tagged images too


@router.post("/jobs/image-processing", response_model=JobCreateResponse, status_code=202)
def enqueue_image_processing(
    payload: ImageJobRequest | None = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(settings_dep),
):
    """Enqueue the batch vision job over all images that need tagging.
    Idempotent: images already queued in an active job are skipped; tagged
    images are skipped unless force=true."""
    force = bool(payload and payload.force)
    query = db.query(Image.id)
    if not force:
        query = query.filter(Image.status != "tagged")
    ref_ids = [row.id for row in query.all()]
    try:
        job = jobs.enqueue_job(db, settings, jobs.KIND_IMAGE, ref_ids)
    except costs.BudgetExceededError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    db.commit()
    return JobCreateResponse(job_id=job.id, kind=job.kind, status=job.status, total=job.total)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return job


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(db: Session = Depends(get_db)):
    return db.query(Job).order_by(Job.id.desc()).limit(50).all()
