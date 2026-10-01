"""Deterministic mock provider — no network, no API key, reproducible.

Used by the test suite and by MOCK_AI=true offline development. It is honest
about being a stand-in: vision "classification" is derived from the corpus
filename hint, embeddings are hashed sparse bag-of-canonical-tokens vectors
(each token lands in 3 hash-picked dims; canonical subjects like "fox" get
double weight). The *behavioral contract* — schema shapes, low-confidence
flagging, alias-based concept matching ("Vulpes vulpes" ≡ "red fox"), and
clear similarity separation between subjects — matches the real provider so
the whole pipeline is exercisable offline.

NEVER use for real evaluation; EVIDENCE.md probes must run against Gemini.
"""

from __future__ import annotations

import hashlib
import math

from app.providers.base import AIProvider, CallUsage, EmbedResult, StructuredResult
from app.services import textnorm

_POSES = ["standing", "hunting", "resting", "playing", "walking", "watching"]
_HABITATS = ["forest", "meadow", "snow", "hillside", "grassland", "riverside"]

# filename prefix → (subject, category, attributes)
_BY_PREFIX: dict[str, tuple[str, str, list[str]]] = {
    "fox": ("red fox", "animal", ["orange fur", "wild", "bushy tail"]),
    "wolf": ("gray wolf", "animal", ["gray fur", "pack", "wild"]),
    "dog": ("domestic dog", "animal", ["domesticated", "pet", "loyal"]),
    "bear": ("brown bear", "animal", ["brown fur", "large", "wild"]),
    "deer": ("deer", "animal", ["antlers", "grazing", "alert"]),
    # Artwork / taxidermy: the model is unsure — confidence below the floor.
    "ambiguous": ("red fox", "animal", ["depiction", "possibly not a live animal"]),
}

_FALLBACK = (
    "unknown subject",
    "unknown",
    ["unclear"],
    "An unclear image with no recognizable subject",
    0.20,
)

_STOPWORDS = {
    "a", "an", "the", "in", "of", "at", "to", "and", "or", "is", "are", "with",
    "on", "for", "from", "by", "as", "its", "it", "this", "that", "these",
    "those", "we", "you", "your", "their", "his", "her", "our", "my", "into",
    "than", "then", "when", "how", "why", "what", "who", "can", "do", "doe",
    "not", "no", "so", "if", "but", "be", "been", "being", "have", "has", "had",
}

_CANONICAL_SUBJECTS = set(textnorm.ALIASES.values())
_DIMS_PER_TOKEN = 3


def _token_dims(token: str, dims: int) -> list[int]:
    digest = hashlib.md5(token.encode()).digest()
    return [int.from_bytes(digest[i * 4 : (i + 1) * 4], "big") % dims for i in range(_DIMS_PER_TOKEN)]


def _hash_vector(text: str, dims: int) -> list[float]:
    """Deterministic unit vector. Canonical subject tokens (fox, wolf, …) weigh
    double so same-subject texts separate cleanly from sibling subjects."""
    vec = [0.0] * dims
    for token in textnorm.tokens(textnorm.canonical_text(text)):
        if token in _STOPWORDS:
            continue
        weight = 2.0 if token in _CANONICAL_SUBJECTS else 1.0
        for idx in _token_dims(token, dims):
            vec[idx] += weight
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class MockProvider(AIProvider):
    def __init__(self, dims: int = 768):
        self.dims = dims

    def classify_image(self, data: bytes, mime_type: str, *, hint: str | None = None) -> StructuredResult:
        filename = hint or ""
        prefix = filename.split("-")[0].lower()
        if prefix == "ambiguous":
            subject, category, attributes = _BY_PREFIX[prefix]
            caption = "A depiction of a red fox, possibly an artwork or taxidermy mount"
            confidence = 0.45
        elif prefix in _BY_PREFIX:
            subject, category, attributes = _BY_PREFIX[prefix]
            try:
                n = int(filename.split("-")[1].split(".")[0])
            except (IndexError, ValueError):
                n = 0
            pose = _POSES[n % len(_POSES)]
            habitat = _HABITATS[n % len(_HABITATS)]
            caption = f"A {subject} {pose} in a {habitat}"
            confidence = 0.90 + (n % 5) * 0.01
        else:
            subject, category, attributes, caption, confidence = _FALLBACK
        data_out = {
            "subject": subject,
            "category": category,
            "attributes": attributes,
            "caption": caption,
            "confidence": round(confidence, 2),
        }
        usage = CallUsage(model="mock-vision", prompt_tokens=258 + len(data) // 1024, completion_tokens=64)
        return StructuredResult(data=data_out, usage=usage)

    def extract_post_subjects(self, title: str, body: str) -> StructuredResult:
        subjects = sorted(textnorm.subjects_in_text(f"{title} {body}"))
        data = {
            "subjects": subjects,
            "topic_category": "animal" if subjects else "general",
        }
        usage = CallUsage(
            model="mock-text",
            prompt_tokens=len((title + body).split()),
            completion_tokens=16,
        )
        return StructuredResult(data=data, usage=usage)

    def embed_texts(self, texts: list[str]) -> EmbedResult:
        vectors = [_hash_vector(t, self.dims) for t in texts]
        usage = CallUsage(model="mock-embed", prompt_tokens=sum(len(t.split()) for t in texts))
        return EmbedResult(vectors=vectors, usage=usage)
