from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from data_retrieval.core.identifiers import stable_id


def _require_finite_positive(name: str, value: float) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and greater than zero")


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("collective observation timestamps must be timezone-aware")


def canonical_concept_key(value: str) -> str:
    canonical = " ".join(value.casefold().strip().split())
    if not canonical:
        raise ValueError("concept key cannot be empty")
    return canonical


@dataclass(frozen=True, slots=True)
class SharedConcept:
    """Stable public-catalog identity; never derived from a private namespace."""

    concept_id: str
    canonical_key: str

    @classmethod
    def from_key(cls, value: str) -> SharedConcept:
        canonical = canonical_concept_key(value)
        return cls(
            concept_id=stable_id("concept", canonical),
            canonical_key=canonical,
        )


class ObservationOutcome(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class EvidenceChannel(StrEnum):
    BEHAVIORAL = "behavioral"
    AI_REVIEW = "ai_review"


@dataclass(frozen=True, slots=True)
class RelationshipObservation:
    """The complete exportable learning input used by the shadow aggregator.

    `contributor_bucket` is an opaque, aggregation-scoped token. It is needed to stop one
    contributor from manufacturing independent support, but it must not be a public account ID.
    """

    observation_id: str
    contributor_bucket: str
    source_concept_id: str
    target_concept_id: str
    outcome: ObservationOutcome
    support: float
    observed_at: datetime
    policy_version: str
    channel: EvidenceChannel = EvidenceChannel.BEHAVIORAL

    def __post_init__(self) -> None:
        if not self.observation_id.strip():
            raise ValueError("observation_id cannot be empty")
        if not self.contributor_bucket.strip():
            raise ValueError("contributor_bucket cannot be empty")
        if self.source_concept_id == self.target_concept_id:
            raise ValueError("a collective relationship must connect two different concepts")
        if not self.source_concept_id.startswith("concept_"):
            raise ValueError("source_concept_id must be a stable shared concept ID")
        if not self.target_concept_id.startswith("concept_"):
            raise ValueError("target_concept_id must be a stable shared concept ID")
        _require_finite_positive("support", self.support)
        _require_aware(self.observed_at)
        if not self.policy_version.strip():
            raise ValueError("policy_version cannot be empty")

    @classmethod
    def create(
        cls,
        *,
        local_event_key: str,
        contributor_bucket: str,
        source_concept_id: str,
        target_concept_id: str,
        outcome: ObservationOutcome,
        support: float,
        observed_at: datetime,
        policy_version: str,
        channel: EvidenceChannel = EvidenceChannel.BEHAVIORAL,
    ) -> RelationshipObservation:
        if not local_event_key.strip():
            raise ValueError("local_event_key cannot be empty")
        observation_id = stable_id(
            "observation",
            contributor_bucket,
            local_event_key,
            source_concept_id,
            target_concept_id,
            outcome.value,
            policy_version,
            channel.value,
        )
        return cls(
            observation_id=observation_id,
            contributor_bucket=contributor_bucket,
            source_concept_id=source_concept_id,
            target_concept_id=target_concept_id,
            outcome=outcome,
            support=support,
            observed_at=observed_at,
            policy_version=policy_version,
            channel=channel,
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "contributor_bucket": self.contributor_bucket,
            "source_concept_id": self.source_concept_id,
            "target_concept_id": self.target_concept_id,
            "outcome": self.outcome.value,
            "support": self.support,
            "observed_at": self.observed_at.isoformat(),
            "policy_version": self.policy_version,
            "channel": self.channel.value,
        }


class RelationshipState(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    DORMANT = "dormant"
    INHIBITED = "inhibited"


@dataclass(frozen=True, slots=True)
class RelationshipProjection:
    source_concept_id: str
    target_concept_id: str
    positive_support: float
    negative_support: float
    effective_support: float
    contributor_count: int
    state: RelationshipState
    behavioral_positive_support: float = 0.0
    behavioral_negative_support: float = 0.0
    review_positive_support: float = 0.0
    review_negative_support: float = 0.0
    reviewer_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


@dataclass(frozen=True, slots=True)
class CollectiveSnapshot:
    snapshot_id: str
    policy_id: str
    as_of: datetime
    observation_digest: str
    observation_count: int
    relationships: tuple[RelationshipProjection, ...]
    normalization: str
    reference_percentile: float

    def __post_init__(self) -> None:
        _require_aware(self.as_of)

    def outgoing(self, source_concept_id: str) -> tuple[RelationshipProjection, ...]:
        return tuple(
            relationship
            for relationship in self.relationships
            if relationship.source_concept_id == source_concept_id
        )

    def relative_influences(self, source_concept_id: str) -> dict[str, float]:
        outgoing = self.outgoing(source_concept_id)
        positive = [item.effective_support for item in outgoing if item.effective_support > 0]
        if not positive:
            return {}
        reference = self._reference(positive)
        denominator = math.log1p(max(1.0, reference))
        return {
            item.target_concept_id: min(
                1.0,
                max(0.0, math.log1p(item.effective_support) / denominator),
            )
            for item in outgoing
            if item.effective_support > 0
        }

    def influence(self, source_concept_id: str, target_concept_id: str) -> float:
        return self.relative_influences(source_concept_id).get(target_concept_id, 0.0)

    def _reference(self, values: list[float]) -> float:
        if self.normalization == "maximum":
            return max(values)
        ordered = sorted(values)
        index = max(0, math.ceil(self.reference_percentile * len(ordered)) - 1)
        return ordered[index]

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "policy_id": self.policy_id,
            "as_of": self.as_of.isoformat(),
            "observation_digest": self.observation_digest,
            "observation_count": self.observation_count,
            "relationships": [item.as_dict() for item in self.relationships],
            "normalization": self.normalization,
            "reference_percentile": self.reference_percentile,
        }


@dataclass(frozen=True, slots=True)
class PrivateCandidate:
    """Private-side ranking input. No payload is needed by the collective ranker."""

    candidate_id: str
    baseline_score: float
    concept_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("candidate_id cannot be empty")
        if not math.isfinite(self.baseline_score):
            raise ValueError("baseline_score must be finite")
        if not self.concept_ids:
            raise ValueError("a private candidate needs at least one shared concept mapping")


@dataclass(frozen=True, slots=True)
class CollectiveRoute:
    source_concept_id: str
    target_concept_id: str
    influence: float


@dataclass(frozen=True, slots=True)
class RankedPrivateCandidate:
    candidate_id: str
    baseline_score: float
    collective_bonus: float
    final_score: float
    route: CollectiveRoute | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "baseline_score": self.baseline_score,
            "collective_bonus": self.collective_bonus,
            "final_score": self.final_score,
            "route": asdict(self.route) if self.route else None,
        }
