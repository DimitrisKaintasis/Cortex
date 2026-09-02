from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from data_retrieval.core.identifiers import stable_id

from .models import (
    EvidenceChannel,
    ObservationOutcome,
    RelationshipObservation,
)


def _probability(name: str, value: float) -> None:
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be finite and in [0, 1]")


@dataclass(frozen=True, slots=True)
class CheapReviewFeatures:
    """Payload-free features computed before any model review."""

    entry_id: str
    source_concept_id: str
    target_concept_id: str
    uncertainty: float
    expected_impact: float
    risk: float
    conflict: float = 0.0
    contributor_concentration: float = 0.0
    concept_rarity: float = 0.0
    alignment_confidence: float = 1.0
    novel_relationship: bool = False
    sensitive: bool = False
    candidate_regression: bool = False

    def __post_init__(self) -> None:
        if not self.entry_id.strip():
            raise ValueError("entry_id cannot be empty")
        if self.source_concept_id == self.target_concept_id:
            raise ValueError("review features must describe two different concepts")
        for name in (
            "uncertainty",
            "expected_impact",
            "risk",
            "conflict",
            "contributor_concentration",
            "concept_rarity",
            "alignment_confidence",
        ):
            _probability(name, getattr(self, name))


class TriageDisposition(StrEnum):
    APPLY_CHEAP = "apply_cheap"
    REVIEW_ASYNC = "review_async"
    HOLD_FOR_REVIEW = "hold_for_review"


@dataclass(frozen=True, slots=True)
class TriageDecision:
    entry_id: str
    disposition: TriageDisposition
    priority: float
    reason_codes: tuple[str, ...]
    apply_before_review: bool

    @property
    def escalated(self) -> bool:
        return self.disposition is not TriageDisposition.APPLY_CHEAP

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["disposition"] = self.disposition.value
        payload["escalated"] = self.escalated
        return payload


@dataclass(frozen=True, slots=True)
class ReviewTriagePolicy:
    policy_id: str = "cheap-review-triage-v1"
    priority_threshold: float = 0.35
    hold_threshold: float = 0.8
    conflict_trigger: float = 0.55
    concentration_trigger: float = 0.85
    rarity_trigger: float = 0.85
    alignment_trigger: float = 0.55

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("policy_id cannot be empty")
        for name in (
            "priority_threshold",
            "hold_threshold",
            "conflict_trigger",
            "concentration_trigger",
            "rarity_trigger",
            "alignment_trigger",
        ):
            _probability(name, getattr(self, name))


class CheapReviewTriage:
    """Deterministic gate that spends model calls on uncertain, impactful, risky entries."""

    def __init__(self, policy: ReviewTriagePolicy | None = None) -> None:
        self.policy = policy or ReviewTriagePolicy()

    def evaluate(self, features: CheapReviewFeatures) -> TriageDecision:
        reasons: list[str] = []
        alignment_uncertainty = 1.0 - features.alignment_confidence
        composite_risk = max(
            features.risk,
            features.conflict,
            features.contributor_concentration * 0.75,
            features.concept_rarity * 0.7,
            alignment_uncertainty,
        )
        priority = max(
            features.uncertainty
            * features.expected_impact
            * (0.5 + 0.5 * composite_risk),
            features.conflict * features.expected_impact,
            alignment_uncertainty * features.expected_impact,
            features.concept_rarity * features.risk,
        )
        if features.sensitive:
            priority = 1.0
            reasons.append("sensitive")
        if features.candidate_regression:
            priority = max(priority, 0.95)
            reasons.append("candidate_regression")
        if features.novel_relationship:
            priority = max(priority, features.expected_impact * features.uncertainty)
            reasons.append("novel_relationship")
        if features.conflict >= self.policy.conflict_trigger:
            reasons.append("conflicting_support")
        if features.contributor_concentration >= self.policy.concentration_trigger:
            reasons.append("contributor_concentration")
        if features.concept_rarity >= self.policy.rarity_trigger:
            reasons.append("rare_concept")
        if features.alignment_confidence <= self.policy.alignment_trigger:
            reasons.append("uncertain_alignment")
        if features.uncertainty >= 0.7:
            reasons.append("high_uncertainty")
        if features.expected_impact >= 0.7:
            reasons.append("high_impact")

        priority = min(1.0, max(0.0, priority))
        escalated = priority >= self.policy.priority_threshold
        if not escalated:
            return TriageDecision(
                entry_id=features.entry_id,
                disposition=TriageDisposition.APPLY_CHEAP,
                priority=priority,
                reason_codes=tuple(sorted(set(reasons))) or ("routine_low_risk",),
                apply_before_review=True,
            )
        must_hold = (
            features.sensitive
            or features.candidate_regression
            or (
                composite_risk >= self.policy.hold_threshold
                and features.expected_impact >= 0.5
            )
        )
        return TriageDecision(
            entry_id=features.entry_id,
            disposition=(
                TriageDisposition.HOLD_FOR_REVIEW
                if must_hold
                else TriageDisposition.REVIEW_ASYNC
            ),
            priority=priority,
            reason_codes=tuple(sorted(set(reasons))) or ("priority_threshold",),
            apply_before_review=False,
        )


class AIReviewVerdict(StrEnum):
    SUPPORT = "support"
    OPPOSE = "oppose"
    UNCERTAIN = "uncertain"
    ABSTAIN = "abstain"


@dataclass(frozen=True, slots=True)
class AIReviewEvent:
    review_id: str
    reviewer_family: str
    source_concept_id: str
    target_concept_id: str
    verdict: AIReviewVerdict
    confidence: float
    recommended_strength: float
    reason_codes: tuple[str, ...]
    reviewed_at: datetime
    review_profile: str
    evidence_digest: str

    def __post_init__(self) -> None:
        if not self.review_id.strip() or not self.reviewer_family.strip():
            raise ValueError("review identity and reviewer family cannot be empty")
        if self.source_concept_id == self.target_concept_id:
            raise ValueError("an AI review must describe two different concepts")
        _probability("confidence", self.confidence)
        _probability("recommended_strength", self.recommended_strength)
        if self.reviewed_at.tzinfo is None or self.reviewed_at.utcoffset() is None:
            raise ValueError("review timestamp must be timezone-aware")
        if not self.review_profile.strip() or not self.evidence_digest.strip():
            raise ValueError("review profile and evidence digest cannot be empty")

    @classmethod
    def create(
        cls,
        *,
        local_review_key: str,
        reviewer_family: str,
        source_concept_id: str,
        target_concept_id: str,
        verdict: AIReviewVerdict,
        confidence: float,
        recommended_strength: float,
        reason_codes: tuple[str, ...],
        reviewed_at: datetime,
        review_profile: str,
        evidence_digest: str,
    ) -> AIReviewEvent:
        return cls(
            review_id=stable_id(
                "ai_review",
                local_review_key,
                reviewer_family,
                source_concept_id,
                target_concept_id,
                review_profile,
            ),
            reviewer_family=reviewer_family,
            source_concept_id=source_concept_id,
            target_concept_id=target_concept_id,
            verdict=verdict,
            confidence=confidence,
            recommended_strength=recommended_strength,
            reason_codes=tuple(sorted(set(reason_codes))),
            reviewed_at=reviewed_at,
            review_profile=review_profile,
            evidence_digest=evidence_digest,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "reviewer_family": self.reviewer_family,
            "source_concept_id": self.source_concept_id,
            "target_concept_id": self.target_concept_id,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "recommended_strength": self.recommended_strength,
            "reason_codes": list(self.reason_codes),
            "reviewed_at": self.reviewed_at.isoformat(),
            "review_profile": self.review_profile,
            "evidence_digest": self.evidence_digest,
        }


@dataclass(frozen=True, slots=True)
class ReviewInfluencePolicy:
    policy_id: str = "bounded-ai-review-influence-v1"
    maximum_equivalent_units: float = 5.0
    minimum_confidence: float = 0.6

    def __post_init__(self) -> None:
        if not math.isfinite(self.maximum_equivalent_units):
            raise ValueError("maximum_equivalent_units must be finite")
        if self.maximum_equivalent_units <= 0:
            raise ValueError("maximum_equivalent_units must be greater than zero")
        _probability("minimum_confidence", self.minimum_confidence)

    def to_observation(
        self,
        review: AIReviewEvent,
        *,
        reviewer_reliability: float,
        aggregation_epoch: str,
    ) -> RelationshipObservation | None:
        _probability("reviewer_reliability", reviewer_reliability)
        if review.confidence < self.minimum_confidence:
            return None
        if review.verdict in {AIReviewVerdict.UNCERTAIN, AIReviewVerdict.ABSTAIN}:
            return None
        outcome = (
            ObservationOutcome.POSITIVE
            if review.verdict is AIReviewVerdict.SUPPORT
            else ObservationOutcome.NEGATIVE
        )
        support = (
            review.confidence
            * review.recommended_strength
            * reviewer_reliability
            * self.maximum_equivalent_units
        )
        if support <= 0:
            return None
        return RelationshipObservation.create(
            local_event_key=review.review_id,
            contributor_bucket=(
                f"reviewer:{review.reviewer_family}:epoch:{aggregation_epoch}"
            ),
            source_concept_id=review.source_concept_id,
            target_concept_id=review.target_concept_id,
            outcome=outcome,
            support=min(self.maximum_equivalent_units, support),
            observed_at=review.reviewed_at,
            policy_version=self.policy_id,
            channel=EvidenceChannel.AI_REVIEW,
        )


class ReviewBudgetPlanner:
    """Selects the highest-priority escalations under a model-call budget."""

    @staticmethod
    def select(
        decisions: tuple[TriageDecision, ...],
        *,
        maximum_reviews: int,
    ) -> tuple[TriageDecision, ...]:
        if maximum_reviews < 0:
            raise ValueError("maximum_reviews cannot be negative")
        eligible = (decision for decision in decisions if decision.escalated)
        ordered = sorted(eligible, key=lambda item: (-item.priority, item.entry_id))
        return tuple(ordered[:maximum_reviews])
