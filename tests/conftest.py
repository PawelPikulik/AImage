"""Test fixtures: isolated SQLite DB per test, deterministic MockProvider,
a seeded mini-corpus (fox/wolf/dog + ambiguous artwork), and an API client.

SQLite note: pgvector's Vector column degrades to a TEXT-affinity column on
SQLite and ranking falls back to in-Python cosine — identical contract, no
Docker needed for the test suite. The production compose stack runs the same
code on real pgvector.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Base, Image, Post
from app.db.session import make_engine, make_session_factory
from app.providers.mock import MockProvider
from app.services import jobs

FOX_POST = {
    "title": "The behavior of red foxes",
    "body": "Red foxes are solitary hunters. A red fox uses its bushy tail; its "
    "orange fur blends into autumn forest light. Foxes raise kits in dens.",
}
CORAL_POST = {
    "title": "Coral reefs: the underwater cities",
    "body": "Coral reefs host a quarter of all marine species. Parrotfish and "
    "clownfish build these living structures. No land animals appear here.",
}


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path}/test.db",
        mock_ai=True,
        corpus_dir=str(tmp_path / "corpus"),
        match_threshold="0.25",
        confidence_floor=0.70,
        job_retry_base_seconds=0.0,  # no backoff delay in tests
        job_max_attempts=3,
        job_item_pace_seconds=0.0,  # explicit: don't inherit a live-run env value
        cost_budget_usd=5.0,
        ranking_candidates=10,
    )


@pytest.fixture()
def session_factory(settings):
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


@pytest.fixture()
def db(session_factory) -> Session:
    with session_factory() as session:
        yield session


@pytest.fixture()
def provider() -> MockProvider:
    return MockProvider()


def _write_fake_jpeg(directory, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    # Minimal JFIF header bytes; content realism is irrelevant to the mock.
    (directory / name).write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 512 + b"\xff\xd9")


@pytest.fixture()
def mini_corpus(settings):
    for name in ("fox-01.jpg", "wolf-01.jpg", "dog-01.jpg", "ambiguous-01.jpg"):
        _write_fake_jpeg(__import__("pathlib").Path(settings.corpus_dir), name)
    return settings.corpus_dir


def _seed_rows(session: Session) -> dict:
    images = {}
    for i, name in enumerate(("fox-01.jpg", "wolf-01.jpg", "dog-01.jpg", "ambiguous-01.jpg")):
        image = Image(filename=name, sha256=f"sha-{name}", category=name.split("-")[0], status="pending")
        session.add(image)
        session.flush()
        images[name] = image
    posts = {}
    for row in (FOX_POST, CORAL_POST):
        post = Post(title=row["title"], body=row["body"], status="pending")
        session.add(post)
        session.flush()
        posts[row["title"]] = post
    session.commit()
    return {"images": images, "posts": posts}


@pytest.fixture()
def seeded_db(session_factory, settings, provider, mini_corpus):
    """DB with 4 images + 2 posts, all processed through the real job engine."""
    with session_factory() as session:
        refs = _seed_rows(session)
        image_ids = [img.id for img in refs["images"].values()]
        post_ids = [p.id for p in refs["posts"].values()]
        jobs.enqueue_job(session, settings, jobs.KIND_IMAGE, image_ids)
        jobs.enqueue_job(session, settings, jobs.KIND_POST, post_ids)
        session.commit()
    jobs.run_until_idle(session_factory, provider, settings)
    with session_factory() as session:
        yield session


@pytest.fixture()
def client(session_factory, settings):
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.api.main import create_app

    app = create_app()

    def override_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[deps.get_db] = override_db
    app.dependency_overrides[deps.settings_dep] = lambda: settings
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
