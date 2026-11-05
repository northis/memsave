"""Small text helpers shared by search, tags and the AI normalizer."""

from __future__ import annotations

import re
import unicodedata

_TAG_STRIP = re.compile(r"[#\s]+")
_TAG_ALLOWED = re.compile(r"[^\w\s\-+.]", re.UNICODE)
_SPACES = re.compile(r"\s+")


def fold_text(value: str) -> str:
    """Casefold and strip accents/diacritics for LIKE and tag keys."""
    value = value.replace("ё", "е").replace("Ё", "Е")
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _SPACES.sub(" ", without_marks.casefold()).strip()


def normalize_tag(value: str, max_len: int = 48) -> str:
    """Canonical display form of an AI/manual tag."""
    value = _TAG_STRIP.sub(" ", value.strip().lstrip("#"))
    value = _TAG_ALLOWED.sub("", value)
    value = _SPACES.sub(" ", value).strip()
    return value[:max_len].strip()


def tag_key(value: str) -> str:
    return fold_text(normalize_tag(value))


def query_tokens(value: str) -> list[str]:
    return [token for token in fold_text(value).split(" ") if token]


def excerpt(value: str, limit: int = 220) -> str:
    value = _SPACES.sub(" ", value).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"
