from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from data_retrieval.core.identifiers import stable_id

from .models import (
    CollectiveRoute,
    CollectiveSnapshot,
    EvidenceChannel,
    ObservationOutcome,
    PrivateCandidate,
    RankedPrivateCandidate,
    RelationshipObservation,
    RelationshipProjection,
    RelationshipState,
)


@dataclass(frozen=True, slots=True)
class CollectivePolicy:
    policy_id: str = "unbounded-separated-decay-v1"
    learning_enabled: bool = True
    initial_observed_support: float = 0.0
    contributor_support_cap: float = 1.0
    reviewer_support_cap: float = 5.0
    review_support_multiplier: float = 1.0
    relationship_support_cap: float | None = None
    half_life_days: float | None = 30.0
    negative_penalty: float = 1.0
    keep_negative_separate: bool = True
    normalization: str = "maximum"
    reference_percentile: float = 0.9
    hot_threshold: float = 2.0
    warm_threshold: float = 1.0
    dormant_threshold: float = 0.1

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("policy_id cannot be empty")
        for name, value in (
            ("initial_observed_support", self.initial_observed_support),
            ("contributor_support_cap", self.contributor_support_cap),
            ("reviewer_support_cap", self.reviewer_support_cap),
            ("review_support_multiplier", self.review_support_multiplier),
            ("negative_penalty", self.negative_penalty),
            ("hot_threshold", self.hot_threshold),
            ("warm_threshold", self.warm_threshold),
            ("dormant_threshold", self.dormant_threshold),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.contributor_support_cap == 0:
            raise ValueError("contributor_support_cap must be greater than zero")
        if self.reviewer_support_cap == 0:
            raise ValueError("reviewer_support_cap must be greater than zero")
        if self.half_life_days is not None and self.half_life_days <= 0:
            raise ValueError("half_life_days must be greater than zero")
        if self.relationship_support_cap is not None and self.relationship_support_cap <= 0:
            raise ValueError("relationship_support_cap must be greater than zero")
        if self.normalization not in {"maximum", "percentile"}:
            raise ValueError("normalization must be maximum or percentile")
        if not 0 < self.reference_percentile <= 1:
            raise ValueError("reference_percentile must be in (0, 1]")
        if not self.hot_threshold >= self.warm_threshold >= self.dormant_threshold:
            raise ValueError("lifecycle thresholds must be ordered hot >= warm >= dormant")


class ShadowCollectiveAggregator:
    """Pure projection builder: observations in, immutable snapshot out."""

    def __init__(self, policy: CollectivePolicy) -> None:
        self.policy = policy

    def build(
        self,
        observations: Iterable[RelationshipObservation],
        *,
        as_of: datetime,
    ) -> CollectiveSnapshot:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("snapshot time must be timezone-aware")
        unique = self._deduplicate(observations)
        canonical = [item.canonical_payload() for item in unique]
        encoded = json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()

        relationships: tuple[RelationshipProjection, ...] = ()
        if self.policy.learning_enabled:
            relationships = self._project(unique, as_of=as_of)
        snapshot_id = stable_id(
            "collective_snapshot",
            self.policy.policy_id,
            as_of.isoformat(),
            digest,
        )
        return CollectiveSnapshot(
            snapshot_id=snapshot_id,
            policy_id=self.policy.policy_id,
            as_of=as_of,
            observation_digest=digest,
            observation_count=len(unique),
            relationships=relationships,
            normalization=self.policy.normalization,
            reference_percentile=self.policy.reference_percentile,
        )

    @staticmethod
    def _deduplicate(
        observations: Iterable[RelationshipObservation],
    ) -> tuple[RelationshipObservation, ...]:
        by_id: dict[str, RelationshipObservation] = {}
        for observation in observations:
            prior = by_id.get(observation.observation_id)
            if prior is not None and prior != observation:
                raise ValueError(
                    "observation ID collision has conflicting payload: "
                    f"{observation.observation_id}"
                )
            by_id[observation.observation_id] = observation
        return tuple(sorted(by_id.values(), key=lambda item: item.observation_id))

    def _project(
        self,
        observations: tuple[RelationshipObservation, ...],
        *,
        as_of: datetime,
    ) -> tuple[RelationshipProjection, ...]:
        contributions: dict[
            tuple[str, str],
            dict[str, dict[EvidenceChannel, dict[ObservationOutcome, float]]],
        ] = defaultdict(
            lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
        )
        for observation in observations:
            if observation.observed_at > as_of:
                continue
            edge = (observation.source_concept_id, observation.target_concept_id)
            contributions[edge][observation.contributor_bucket][observation.channel][
                observation.outcome
            ] += self._decayed(observation, as_of=as_of)

        projections: list[RelationshipProjection] = []
        for (source_id, target_id), contributors in sorted(contributions.items()):
            behavioral_positive = self.policy.initial_observed_support
            behavioral_negative = 0.0
            review_positive = 0.0
            review_negative = 0.0
            reviewer_count = 0
            behavioral_contributor_count = 0
            for channels in contributors.values():
                behavioral = channels[EvidenceChannel.BEHAVIORAL]
                review = channels[EvidenceChannel.AI_REVIEW]
                if any(behavioral.values()):
                    behavioral_contributor_count += 1
                behavioral_positive += min(
                    behavioral[ObservationOutcome.POSITIVE],
                    self.policy.contributor_support_cap,
                )
                behavioral_negative += min(
                    behavioral[ObservationOutcome.NEGATIVE],
                    self.policy.contributor_support_cap,
                )
                if any(review.values()):
                    reviewer_count += 1
                review_positive += min(
                    review[ObservationOutcome.POSITIVE],
                    self.policy.reviewer_support_cap,
                )
                review_negative += min(
                    review[ObservationOutcome.NEGATIVE],
                    self.policy.reviewer_support_cap,
                )
            positive = (
                behavioral_positive
                + self.policy.review_support_multiplier * review_positive
            )
            negative = (
                behavioral_negative
                + self.policy.review_support_multiplier * review_negative
            )
            if self.policy.relationship_support_cap is not None:
                positive = min(positive, self.policy.relationship_support_cap)
                negative = min(negative, self.policy.relationship_support_cap)
            effective = max(0.0, positive - self.policy.negative_penalty * negative)
            stored_negative = negative
            stored_positive = positive
            if not self.policy.keep_negative_separate:
                stored_positive = effective
                stored_negative = 0.0
            projections.append(
                RelationshipProjection(
                    source_concept_id=source_id,
                    target_concept_id=target_id,
                    positive_support=stored_positive,
                    negative_support=stored_negative,
                    effective_support=effective,
                    contributor_count=behavioral_contributor_count,
                    state=self._state(positive, negative, effective),
                    behavioral_positive_support=behavioral_positive,
                    behavioral_negative_support=behavioral_negative,
                    review_positive_support=review_positive,
                    review_negative_support=review_negative,
                    reviewer_count=reviewer_count,
                )
            )
        return tuple(projections)

    def _decayed(self, observation: RelationshipObservation, *, as_of: datetime) -> float:
        if self.policy.half_life_days is None:
            return observation.support
        age_days = max(0.0, (as_of - observation.observed_at).total_seconds() / 86_400.0)
        return observation.support * 2 ** (-age_days / self.policy.half_life_days)

    def _state(
        self,
        positive: float,
        negative: float,
        effective: float,
    ) -> RelationshipState:
        if negative > 0 and self.policy.negative_penalty * negative >= positive:
            return RelationshipState.INHIBITED
        if effective < self.policy.dormant_threshold:
            return RelationshipState.DORMANT
        if effective >= self.policy.hot_threshold:
            return RelationshipState.HOT
        if effective >= self.policy.warm_threshold:
            return RelationshipState.WARM
        return RelationshipState.COLD


class ShadowCollectiveRanker:
    """Adds a bounded relative bonus to private candidate IDs without reading payloads."""

    def __init__(self, *, bonus_weight: float = 0.3) -> None:
        if not math.isfinite(bonus_weight) or bonus_weight < 0:
            raise ValueError("bonus_weight must be finite and non-negative")
        self.bonus_weight = bonus_weight

    def rank(
        self,
        *,
        query_concept_ids: tuple[str, ...],
        candidates: tuple[PrivateCandidate, ...],
        snapshot: CollectiveSnapshot,
    ) -> tuple[RankedPrivateCandidate, ...]:
        ranked: list[RankedPrivateCandidate] = []
        for candidate in candidates:
            best_route: CollectiveRoute | None = None
            for source_id in query_concept_ids:
                influences = snapshot.relative_influences(source_id)
                for target_id in candidate.concept_ids:
                    influence = influences.get(target_id, 0.0)
                    if influence <= 0:
                        continue
                    route = CollectiveRoute(source_id, target_id, influence)
                    if best_route is None or (
                        route.influence,
                        route.source_concept_id,
                        route.target_concept_id,
                    ) > (
                        best_route.influence,
                        best_route.source_concept_id,
                        best_route.target_concept_id,
                    ):
                        best_route = route
            bonus = self.bonus_weight * (best_route.influence if best_route else 0.0)
            ranked.append(
                RankedPrivateCandidate(
                    candidate_id=candidate.candidate_id,
                    baseline_score=candidate.baseline_score,
                    collective_bonus=bonus,
                    final_score=candidate.baseline_score + bonus,
                    route=best_route,
                )
            )
        return tuple(sorted(ranked, key=lambda item: (-item.final_score, item.candidate_id)))
