from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from data_retrieval.domain.models import AtomKind, utc_now


class TemporalMode(StrEnum):
    AUTO = "auto"
    NONE = "none"
    CURRENT_STATE = "current_state"
    AS_OF = "as_of"
    RANGE = "range"
    HISTORY = "history"


@dataclass(frozen=True, slots=True)
class QueryPlan:
    query: str
    namespace: str
    query_tags: tuple[str, ...] = ()
    top_k: int = 10
    timeline_id: str | None = None
    temporal_mode: TemporalMode = TemporalMode.AUTO
    as_of: datetime | None = None
    range_start: datetime | None = None
    range_end: datetime | None = None

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query cannot be empty")
        if not self.namespace.strip():
            raise ValueError("namespace cannot be empty")
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")
        for name, value in (
            ("as_of", self.as_of),
            ("range_start", self.range_start),
            ("range_end", self.range_end),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{name} must include a timezone")
        if (self.range_start is None) != (self.range_end is None):
            raise ValueError("range_start and range_end must be provided together")
        if (
            self.range_start is not None
            and self.range_end is not None
            and self.range_start >= self.range_end
        ):
            raise ValueError("range_start must be before range_end")


@dataclass(frozen=True, slots=True)
class AtomEmbedding:
    atom_id: str
    provider: str
    model: str
    dimensions: int
    vector: tuple[float, ...]
    content_hash: str
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("embedding provider and model cannot be empty")
        if self.dimensions <= 0 or self.dimensions != len(self.vector):
            raise ValueError("embedding dimensions must match the vector")


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One bounded candidate returned by a storage-native search channel."""

    atom_id: str
    score: float
    evidence: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.atom_id.strip():
            raise ValueError("search hit atom_id cannot be empty")
        if self.score < 0.0:
            raise ValueError("search hit score must be non-negative")


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    tag: float = 0.0
    lexical: float = 0.0
    semantic: float = 0.0
    relationship: float = 0.0
    temporal: float = 0.0
    final: float = 0.0
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalItem:
    atom_id: str
    content: str
    kind: AtomKind
    occurred_at: datetime | None
    role: str
    score: ScoreBreakdown
    metadata: dict[str, Any]
    lineage_atom_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    retrieval_id: str
    plan: QueryPlan
    resolved_temporal_mode: TemporalMode
    items: tuple[RetrievalItem, ...]
    low_confidence: bool
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FeedbackRequest:
    feedback_id: str
    retrieval_id: str
    selected_atom_ids: tuple[str, ...]
    outcome: str
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.feedback_id.strip() or not self.retrieval_id.strip():
            raise ValueError("feedback_id and retrieval_id cannot be empty")
        if self.outcome not in {"positive", "negative"}:
            raise ValueError("outcome must be positive or negative")
        if not self.selected_atom_ids:
            raise ValueError("at least one selected atom is required")
