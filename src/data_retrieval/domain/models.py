from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(UTC)


class TagLevel(StrEnum):
    BROAD = "broad"
    SPECIFIC = "specific"


class TagState(StrEnum):
    CANONICAL = "canonical"
    PROPOSED_NEW = "proposed_new"


class TagOrigin(StrEnum):
    CATALOG_MATCH = "catalog_match"
    PROPOSED_NEW = "proposed_new"


class AtomKind(StrEnum):
    SOURCE = "source"
    TEMPORAL_SUMMARY = "temporal_summary"


class AtomLinkRelation(StrEnum):
    SUMMARIZES = "summarizes"
    DERIVED_FROM = "derived_from"


@dataclass(frozen=True, slots=True)
class Document:
    document_id: str
    namespace: str
    source: str
    content_hash: str
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Atom:
    atom_id: str
    document_id: str
    namespace: str
    position: int
    char_start: int
    char_end: int
    content: str
    content_hash: str
    kind: AtomKind = AtomKind.SOURCE
    occurred_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AtomLink:
    from_atom_id: str
    to_atom_id: str
    relation: AtomLinkRelation
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Tag:
    tag_id: str
    namespace: str
    canonical_text: str
    display_text: str
    level: TagLevel
    state: TagState
    aliases: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class AtomTag:
    atom_id: str
    tag_id: str
    weight_raw: float
    confidence: float
    origin: TagOrigin
    evidence_sources: tuple[str, ...]
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.weight_raw < 0:
            raise ValueError("weight_raw must be non-negative")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class IngestionBundle:
    document: Document
    atoms: tuple[Atom, ...]
    tags: tuple[Tag, ...]
    atom_tags: tuple[AtomTag, ...]
    atom_links: tuple[AtomLink, ...] = ()
