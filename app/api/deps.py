"""Shared FastAPI dependencies."""

from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings
from app.db.session import get_db  # noqa: F401  (re-exported)
from app.providers.base import AIProvider, get_provider


def settings_dep() -> Settings:
    return get_settings()


@lru_cache
def provider_dep() -> AIProvider:
    # One provider per process. Constructed lazily so the API can boot without
    # an API key; only job-triggering endpoints actually need the provider.
    return get_provider(get_settings())
