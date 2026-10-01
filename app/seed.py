"""Seed demo data — `python -m app.seed`.

Idempotent: images are keyed by sha256 and posts by title, so re-seeding
never duplicates. Steps: corpus manifest → image rows → post rows → enqueue
image + post processing jobs → optionally run the worker inline until idle.

    python -m app.seed            # seed + wait for all jobs to finish
    python -m app.seed --no-wait  # seed and let the worker service pick up
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys

from pathlib import Path

from app.config import MANIFEST_PATH, POSTS_SEED_PATH, get_settings
from app.db.session import get_session_factory
from app.providers.base import get_provider
from app.db.models import Image, Post
from app.services import jobs, tuning


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def seed(session, settings) -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    corpus_dir = Path(settings.corpus_dir)
    created_images = 0
    for entry in manifest:
        path = corpus_dir / entry["filename"]
        if not path.exists():
            sys.exit(
                f"Missing corpus file {path} — run: python scripts/download_corpus.py"
            )
        digest = _sha256(path)
        if session.query(Image).filter(Image.sha256 == digest).one_or_none():
            continue  # already seeded — idempotent
        session.add(
            Image(filename=entry["filename"], sha256=digest, category=entry["category"], status="pending")
        )
        created_images += 1
    session.flush()

    posts_seed = json.loads(POSTS_SEED_PATH.read_text(encoding="utf-8"))
    created_posts = 0
    for row in posts_seed:
        if session.query(Post).filter(Post.title == row["title"]).one_or_none():
            continue
        session.add(Post(title=row["title"], body=row["body"], status="pending"))
        created_posts += 1
    session.flush()
    return {"images_created": created_images, "posts_created": created_posts}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-wait", action="store_true", help="enqueue jobs but do not run them here")
    args = parser.parse_args()

    settings = get_settings()
    session_factory = get_session_factory()
    with session_factory() as session:
        stats = seed(session, settings)
        image_ids = [r.id for r in session.query(Image.id).filter(Image.status != "tagged")]
        post_ids = [r.id for r in session.query(Post.id).filter(Post.status != "processed")]
        image_job = jobs.enqueue_job(session, settings, jobs.KIND_IMAGE, image_ids) if image_ids else None
        post_job = jobs.enqueue_job(session, settings, jobs.KIND_POST, post_ids) if post_ids else None
        session.commit()
        print(
            f"seeded: +{stats['images_created']} images, +{stats['posts_created']} posts; "
            f"image_job={image_job.id if image_job else '-'} ({len(image_ids)} items), "
            f"post_job={post_job.id if post_job else '-'} ({len(post_ids)} items)"
        )

    if args.no_wait:
        print("jobs enqueued — the worker service will process them")
        return

    provider = get_provider(settings)
    print("running jobs inline…")
    result = jobs.run_until_idle(session_factory, provider, settings)
    with session_factory() as session:
        tagged = session.query(Image).filter(Image.status == "tagged").count()
        flagged = session.query(Image).filter(Image.flagged_low_confidence.is_(True)).count()
        processed = session.query(Post).filter(Post.status == "processed").count()
    print(
        f"done: {result['items_processed']} items processed; "
        f"{tagged} images tagged ({flagged} flagged low-confidence), {processed} posts processed"
    )

    # Tune the guard's similarity threshold on the labeled eval set — picked
    # from data, not guessed (Section 2: 'defend the boundary with a number').
    with session_factory() as session:
        best = tuning.tune(session, settings)
        session.commit()
    print(
        f"guard threshold tuned: {best['threshold']:.2f} "
        f"(eval precision {best['top1_precision']:.3f}, "
        f"rejection accuracy {best['rejection_accuracy']:.3f})"
    )


if __name__ == "__main__":
    main()
