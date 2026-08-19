from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable


def normalize_tag(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = normalized.replace("_", " ").replace("-", " ")
    normalized = re.sub(r"[^\w\s]", "", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def deduplicate_tags(values: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """Return unique ``(canonical, first display value)`` pairs in input order."""

    unique: dict[str, str] = {}
    for value in values:
        canonical = normalize_tag(value)
        if canonical and canonical not in unique:
            unique[canonical] = value.strip()
    return tuple(unique.items())
