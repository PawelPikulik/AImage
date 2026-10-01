"""AI provider abstraction.

The services layer talks only to this interface, so the Gemini implementation
can be swapped for a local model (Ollama) or the deterministic MockProvider
used by tests and offline development — without touching business logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CallUsage:
    """Token usage + cost metadata for one AI call (feeds cost_log)."""

    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


@dataclass
class StructuredResult:
    data: dict  # raw, *unvalidated* model output — services validate it
    usage: CallUsage


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    usage: CallUsage


class AIProvider(ABC):
    @abstractmethod
    def classify_image(self, data: bytes, mime_type: str, *, hint: str | None = None) -> StructuredResult:
        """Return structured tags for one image. `hint` (e.g. filename) lets
        deterministic test providers identify the image; real providers ignore it."""

    @abstractmethod
    def extract_post_subjects(self, title: str, body: str) -> StructuredResult:
        """Return {'subjects': [...], 'topic_category': str} for a blog post."""

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> EmbedResult:
        """Embed texts into the shared semantic space (one vector per text)."""


class ProviderError(Exception):
    """Transient provider failure (rate limit, network, 5xx) — job retries."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


def get_provider(settings=None) -> AIProvider:
    """Factory: MOCK_AI=true → deterministic offline provider, else Gemini."""
    from app.config import get_settings

    settings = settings or get_settings()
    if settings.mock_ai:
        from app.providers.mock import MockProvider

        return MockProvider(dims=settings.embed_dims)
    if not settings.gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Get a free key at "
            "https://aistudio.google.com/apikey (no card) and put it in .env, "
            "or set MOCK_AI=true for offline development."
        )
    from app.providers.gemini import GeminiProvider

    return GeminiProvider(settings)


# Re-exported for convenience in tests.
__all__ = [
    "AIProvider",
    "CallUsage",
    "EmbedResult",
    "ProviderError",
    "StructuredResult",
    "get_provider",
    "field",
]
