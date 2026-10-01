"""Batch job engine.

Jobs are DB rows (`jobs` + `job_items`), so slow/bulk AI work never blocks an
HTTP request and survives restarts. Per item: exponential-backoff retries,
permanent failure after JOB_MAX_ATTEMPTS (logged as the failure alert), and a
UNIQUE(job_id, ref_id) constraint so a retried enqueue never duplicates work.
Cost rows written by handlers are committed even when the item fails — a
failed attempt still spent money.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db.models import Job, JobItem, utcnow
from app.providers.base import AIProvider, ProviderError
from app.schemas.tags import SchemaValidationError
from app.services import costs, posts as posts_service, vision

log = logging.getLogger("jobs")

KIND_IMAGE = "image_processing"
KIND_POST = "post_processing"

_HANDLERS = {
    KIND_IMAGE: vision.process_image,
    KIND_POST: posts_service.process_post,
}


def enqueue_job(session: Session, settings: Settings, kind: str, ref_ids: list[int]) -> Job:
    """Create a job over ref_ids, skipping refs already queued in an active job
    of the same kind (idempotent enqueue). Raises BudgetExceededError."""
    if kind not in _HANDLERS:
        raise ValueError(f"unknown job kind: {kind}")
    costs.check_budget(session, settings)

    active = (
        session.query(JobItem.ref_id)
        .join(Job, Job.id == JobItem.job_id)
        .filter(Job.kind == kind, Job.status.in_(["pending", "running"]), JobItem.status == "pending")
        .all()
    )
    already_queued = {r for (r,) in active}
    new_refs = [r for r in dict.fromkeys(ref_ids) if r not in already_queued]

    job = Job(kind=kind, total=len(new_refs), status="pending")
    session.add(job)
    session.flush()
    for ref_id in new_refs:
        session.add(JobItem(job_id=job.id, ref_id=ref_id))
    session.flush()
    return job


def _backoff_seconds(settings: Settings, attempts: int) -> float:
    return min(settings.job_retry_base_seconds * (2 ** max(attempts - 1, 0)), 60.0)


def run_next_item(session: Session, provider: AIProvider, settings: Settings) -> bool:
    """Run the next due job item, if any. Returns True when work happened."""
    now = utcnow()
    item = (
        session.query(JobItem)
        .filter(JobItem.status == "pending", JobItem.run_after <= now)
        .order_by(JobItem.id)
        .first()
    )
    if item is None:
        return False
    job = session.get(Job, item.job_id)
    job.status = "running"
    handler = _HANDLERS[job.kind]

    try:
        handler(session, provider, settings, item.ref_id, job_id=job.id)
    except Exception as exc:
        retryable = not isinstance(exc, (LookupError,)) and (
            not isinstance(exc, ProviderError) or exc.retryable
        )
        item.attempts += 1
        item.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        if retryable and item.attempts < settings.job_max_attempts:
            wait = _backoff_seconds(settings, item.attempts)
            item.run_after = utcnow() + timedelta(seconds=wait)
            log.warning(
                "job %s item ref=%s attempt %d failed (%s) — retrying in %.0fs",
                job.id, item.ref_id, item.attempts, type(exc).__name__, wait,
            )
        else:
            item.status = "failed"
            job.failed += 1
            # Failure alert: at this scale the alert channel is the error log.
            log.error(
                "JOB FAILURE ALERT job=%s kind=%s ref_id=%s attempts=%d error=%s",
                job.id, job.kind, item.ref_id, item.attempts, item.last_error,
            )
    else:
        item.status = "done"
        job.done += 1

    if job.done + job.failed >= job.total:
        job.status = "completed" if job.failed == 0 else "completed_with_errors"
        if job.failed:
            job.error = f"{job.failed}/{job.total} items failed; see job_items.last_error"
    session.commit()
    return True


def pending_count(session: Session) -> int:
    return session.query(JobItem).filter(JobItem.status == "pending").count()


def run_until_idle(
    session_factory: sessionmaker[Session],
    provider: AIProvider,
    settings: Settings,
    *,
    max_rounds: int = 10_000,
) -> dict:
    """Drain the queue (used by seed script and tests).

    When nothing is currently due but pending retries remain scheduled in the
    future (backoff), wait for the next due item instead of exiting early —
    otherwise a rate-limit storm would leave the queue half-drained.
    `job_item_pace_seconds` adds courtesy spacing between items (free-tier RPM).
    """
    worked = 0
    with session_factory() as session:
        while worked < max_rounds:
            if run_next_item(session, provider, settings):
                worked += 1
                if settings.job_item_pace_seconds > 0:
                    time.sleep(settings.job_item_pace_seconds)
                continue
            next_due = (
                session.query(func.min(JobItem.run_after))
                .filter(JobItem.status == "pending")
                .scalar()
            )
            if next_due is None:
                break
            if next_due.tzinfo is None:  # SQLite returns naive datetimes
                next_due = next_due.replace(tzinfo=timezone.utc)
            wait = (next_due - utcnow()).total_seconds()
            if wait <= 0:
                continue
            log.info("queue drained for now; next retry due in %.1fs — waiting", min(wait, 60))
            time.sleep(min(wait + 0.05, 60))
    return {"items_processed": worked}
