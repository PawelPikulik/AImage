"""Gemini provider — vision (structured output), text extraction, embeddings.

Uses the free tier of Google AI Studio (no card). Every call records token
usage + latency for the cost log. HTTP errors are classified retryable vs
permanent so the job engine backs off correctly.
"""

from __future__ import annotations

import json
import time

from google import genai
from google.genai import types

from app.config import Settings
from app.providers.base import AIProvider, CallUsage, EmbedResult, ProviderError, StructuredResult
from app.schemas.tags import ImageTags, PostSubjects

VISION_PROMPT = """You are tagging an image library for an article-matching system.
Analyze this image and describe it precisely.

Rules:
- subject: the specific main subject (e.g. "red fox", not "animal"). If the image
  is artwork, a statue, or taxidermy rather than a live subject, say so in attributes.
- category: one broad category (e.g. "animal", "landscape", "food").
- attributes: 3-6 short descriptors (appearance, setting, mood).
- caption: one factual sentence.
- confidence: your calibrated 0-1 confidence in the subject identification.
  Use < 0.6 when the image is ambiguous, heavily stylized, very distant, or unclear."""

SUBJECT_PROMPT = """Extract what this blog post is about, as structured JSON.

- subjects: the specific main subjects of the post (e.g. ["red fox"]). Use the
  most specific common name; include the latin name too if the post uses it
  (e.g. ["red fox", "Vulpes vulpes"]). Empty list if no concrete subject.
- topic_category: one broad category (e.g. "animal", "food", "space")."""


def _usage(model: str, meta, started: float) -> CallUsage:
    return CallUsage(
        model=model,
        prompt_tokens=getattr(meta, "prompt_token_count", 0) or 0,
        completion_tokens=getattr(meta, "candidates_token_count", 0) or 0,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


class GeminiProvider(AIProvider):
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = genai.Client(api_key=settings.gemini_api_key)

    def _generate_json(self, *, model: str, contents: list, schema, prompt: str) -> StructuredResult:
        started = time.perf_counter()
        try:
            response = self._client.models.generate_content(
                model=model,
                contents=[*contents, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.1,
                ),
            )
        except Exception as exc:  # google.api_core.exceptions hierarchy
            raise _classify(exc) from exc
        usage = _usage(model, getattr(response, "usage_metadata", None), started)
        # Return RAW JSON — the services layer schema-validates before trusting.
        return StructuredResult(data=json.loads(response.text), usage=usage)

    def classify_image(self, data: bytes, mime_type: str, *, hint: str | None = None) -> StructuredResult:
        return self._generate_json(
            model=self._settings.gemini_vision_model,
            contents=[types.Part.from_bytes(data=data, mime_type=mime_type)],
            schema=ImageTags,
            prompt=VISION_PROMPT,
        )

    def extract_post_subjects(self, title: str, body: str) -> StructuredResult:
        return self._generate_json(
            model=self._settings.gemini_text_model,
            contents=[f"TITLE: {title}\n\nBODY:\n{body[:8000]}"],
            schema=PostSubjects,
            prompt=SUBJECT_PROMPT,
        )

    def embed_texts(self, texts: list[str]) -> EmbedResult:
        started = time.perf_counter()
        try:
            result = self._client.models.embed_content(
                model=self._settings.gemini_embed_model,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type="SEMANTIC_SIMILARITY",
                    output_dimensionality=self._settings.embed_dims,
                ),
            )
        except Exception as exc:
            raise _classify(exc) from exc
        vectors = [list(e.values) for e in result.embeddings]
        meta = getattr(result, "metadata", None)
        usage = CallUsage(
            model=self._settings.gemini_embed_model,
            prompt_tokens=getattr(meta, "billable_character_count", 0) or sum(len(t) for t in texts) // 4,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return EmbedResult(vectors=vectors, usage=usage)


def _classify(exc: Exception) -> ProviderError:
    """429 / 5xx / network → retryable; 4xx (bad request, auth) → permanent."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    message = f"Gemini call failed ({status or type(exc).__name__}): {exc}"
    if status in (429, 500, 502, 503, 504):
        return ProviderError(message, retryable=True)
    if status is None:  # network errors etc.
        return ProviderError(message, retryable=True)
    return ProviderError(message, retryable=False)
