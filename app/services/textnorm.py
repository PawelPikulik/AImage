"""Canonical subject vocabulary + alias resolution.

The mismatch guard compares *canonical* subjects so that "red fox",
"Vulpes vulpes" and "wild fox species" are the same concept, while "fox" and
"wolf" — siblings in the animal category — are a provable mismatch.
"""

from __future__ import annotations

import re

# Canonical subjects per category. Two *different* canonical subjects in the
# same category conflict (fox vs wolf); subjects in different categories are
# simply unrelated (handled by the similarity threshold).
CATEGORY_SUBJECTS: dict[str, set[str]] = {
    "animal": {"fox", "wolf", "dog", "bear", "deer"},
}

# Alias → canonical subject. Includes latin names so scientific phrasing in a
# post matches common-name image tags (and vice versa).
ALIASES: dict[str, str] = {
    "red fox": "fox",
    "fox": "fox",
    "foxes": "fox",
    "vulpes vulpes": "fox",
    "vulpes": "fox",
    "gray wolf": "wolf",
    "grey wolf": "wolf",
    "wolf": "wolf",
    "wolves": "wolf",
    "canis lupus": "wolf",
    "dog": "dog",
    "dogs": "dog",
    "domestic dog": "dog",
    "canis familiaris": "dog",
    "brown bear": "bear",
    "grizzly bear": "bear",
    "grizzly": "bear",
    "bear": "bear",
    "bears": "bear",
    "ursus arctos": "bear",
    "deer": "deer",
    "red deer": "deer",
    "fallow deer": "deer",
    "white-tailed deer": "deer",
    "cervus elaphus": "deer",
    "dama dama": "deer",
    # Breeds/subspecies the live vision model actually returns (observed in the
    # 2026-10-01 smoke run: "Mexican wolf", "Cane Corso") → map to canonical.
    "mexican wolf": "wolf",
    "arctic wolf": "wolf",
    "timber wolf": "wolf",
    "cane corso": "dog",
    "mastiff": "dog",
    "labrador": "dog",
    "golden retriever": "dog",
    "german shepherd": "dog",
    "fox kit": "fox",
    "fox kits": "fox",
    "bear cub": "bear",
    "fawn": "deer",
}

# Longest aliases first so "red fox" wins over "fox" when scanning free text.
_ALIASES_BY_LENGTH = sorted(ALIASES, key=len, reverse=True)

_WORD_RE = re.compile(r"[a-z]+")


def canonical_subject(text: str) -> str | None:
    """Map an arbitrary subject string to a canonical subject, if known."""
    return ALIASES.get(text.strip().lower())


def subjects_in_text(text: str) -> set[str]:
    """Find canonical subjects mentioned anywhere in free text (word-boundary safe)."""
    lowered = text.lower()
    found: set[str] = set()
    for alias in _ALIASES_BY_LENGTH:
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            found.add(ALIASES[alias])
    return found


def tokens(text: str) -> set[str]:
    """Lowercased word tokens with light plural stripping (foxes → fox)."""
    out: set[str] = set()
    for word in _WORD_RE.findall(text.lower()):
        if word.endswith("es") and len(word) > 3:
            word = word[:-2]
        elif word.endswith("s") and len(word) > 3:
            word = word[:-1]
        out.add(word)
    return out


def canonical_text(text: str) -> str:
    """Replace known aliases with their canonical subject — makes 'Vulpes vulpes'
    lexically overlap with 'red fox' for embedding-style comparisons."""
    lowered = text.lower()
    for alias in _ALIASES_BY_LENGTH:
        lowered = re.sub(rf"\b{re.escape(alias)}\b", ALIASES[alias], lowered)
    return lowered
