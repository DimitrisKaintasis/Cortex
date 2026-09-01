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
    """Legacy processing kind retained for storage and API compatibility."""

    SOURCE = "source"
    TEMPORAL_SUMMARY = "temporal_summary"
    INTERACTION = "interaction"
    UNCERTAINTY = "uncertainty"


class AtomRole(StrEnum):
    """Architectural role an atom plays in the evidence graph."""

    SOURCE = "source"
    DERIVED = "derived"
    INTERACTION = "interaction"
    UNCERTAINTY = "uncertainty"


class PayloadModality(StrEnum):
    """Form of the payload; independent from its architectural role."""

    TEXT = "text"
    CODE = "code"
    EVENT = "event"
    IMAGE = "image"
    AUDIO = "audio"
    BINARY_REFERENCE = "binary_reference"


class AtomLinkRelation(StrEnum):
    SUMMARIZES = "summarizes"
    DERIVED_FROM = "derived_from"
    SUPERSEDES = "supersedes"
    CO_USED = "co_used"
    CONFLICTS_WITH = "conflicts_with"
    ADJACENT_TO = "adjacent_to"


class CalibrationTarget(StrEnum):
    """Native object whose prior or relationship is informed by a signal."""

    ATOM = "atom"
    ATOM_TAG = "atom_tag"
    TAG_RELATION = "tag_relation"
    ATOM_LINK = "atom_link"


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
    role: AtomRole | None = None
    modality: PayloadModality = PayloadModality.TEXT
    occurred_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.role is not None:
            return
        inferred_roles = {
            AtomKind.SOURCE: AtomRole.SOURCE,
            AtomKind.TEMPORAL_SUMMARY: AtomRole.DERIVED,
            AtomKind.INTERACTION: AtomRole.INTERACTION,
            AtomKind.UNCERTAINTY: AtomRole.UNCERTAINTY,
        }
        object.__setattr__(self, "role", inferred_roles[self.kind])


@dataclass(frozen=True, slots=True)
class AtomLink:
    from_atom_id: str
    to_atom_id: str
    relation: AtomLinkRelation
    weight_raw: float = 1.0
    confidence: float = 1.0
    evidence_sources: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.weight_raw < 0:
            raise ValueError("weight_raw must be non-negative")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


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
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.weight_raw < 0:
            raise ValueError("weight_raw must be non-negative")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class TagRelation:
    source_tag_id: str
    target_tag_id: str
    relation_type: str
    weight_raw: float
    confidence: float
    evidence_sources: tuple[str, ...]
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.source_tag_id == self.target_tag_id:
            raise ValueError("a tag cannot relate to itself")
        if not self.relation_type.strip():
            raise ValueError("relation_type cannot be empty")
        if self.weight_raw < 0:
            raise ValueError("weight_raw must be non-negative")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class CalibrationSignal:
    """Immutable, replay-safe evidence used to initialize or adjust graph weights."""

    signal_id: str
    namespace: str
    target_type: CalibrationTarget
    target_id: str
    signal_type: str
    value: float
    confidence: float
    multiplier: float
    provider: str
    profile_version: str
    related_id: str | None = None
    relation_type: str | None = None
    source_reference: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("signal_id", self.signal_id),
            ("namespace", self.namespace),
            ("target_id", self.target_id),
            ("signal_type", self.signal_type),
            ("provider", self.provider),
            ("profile_version", self.profile_version),
        ):
            if not value.strip():
                raise ValueError(f"{name} cannot be empty")
        if not 0.0 <= self.value <= 1.0:
            raise ValueError("value must be between 0 and 1")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.multiplier <= 0.0:
            raise ValueError("multiplier must be positive")
        if self.target_type in {
            CalibrationTarget.ATOM_TAG,
            CalibrationTarget.TAG_RELATION,
            CalibrationTarget.ATOM_LINK,
        }:
            if not self.related_id:
                raise ValueError("related_id is required for an edge calibration signal")
        if self.target_type in {CalibrationTarget.TAG_RELATION, CalibrationTarget.ATOM_LINK}:
            if not self.relation_type:
                raise ValueError("relation_type is required for a relationship signal")


@dataclass(frozen=True, slots=True)
class IngestionBundle:
    document: Document
    atoms: tuple[Atom, ...]
    tags: tuple[Tag, ...]
    atom_tags: tuple[AtomTag, ...]
    atom_links: tuple[AtomLink, ...] = ()
