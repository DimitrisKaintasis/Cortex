from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from .cascade import CheapReviewFeatures, CheapReviewTriage, TriageDecision


def _bounded(name: str, value: float) -> None:
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be finite and in [0, 1]")


def _non_negative(name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ContributorSupport:
    contributor_bucket: str
    positive_support: float = 0.0
    negative_support: float = 0.0

    def __post_init__(self) -> None:
        if not self.contributor_bucket.strip():
            raise ValueError("contributor_bucket cannot be empty")
        _non_negative("positive_support", self.positive_support)
        _non_negative("negative_support", self.negative_support)

    @property
    def total_support(self) -> float:
        return self.positive_support + self.negative_support


@dataclass(frozen=True, slots=True)
class LedgerFeatureEvidence:
    relationship_exists: bool
    contributors: tuple[ContributorSupport, ...]
    concept_independent_contributors: int
    profile: str = "collective-ledger-features-v1"

    def __post_init__(self) -> None:
        if self.concept_independent_contributors < 0:
            raise ValueError("concept_independent_contributors cannot be negative")
        if not self.profile.strip():
            raise ValueError("ledger feature profile cannot be empty")
        buckets = [item.contributor_bucket for item in self.contributors]
        if len(buckets) != len(set(buckets)):
            raise ValueError("ledger evidence must aggregate each contributor bucket once")


@dataclass(frozen=True, slots=True)
class VectorFeatureEvidence:
    best_similarity: float
    second_similarity: float
    duplicate_fraction: float
    provider: str
    model: str
    calibration_profile: str

    def __post_init__(self) -> None:
        for name in ("best_similarity", "second_similarity", "duplicate_fraction"):
            _bounded(name, getattr(self, name))
        if self.second_similarity > self.best_similarity:
            raise ValueError("second_similarity cannot exceed best_similarity")
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("vector provider and model cannot be empty")
        if not self.calibration_profile.strip():
            raise ValueError("vector calibration_profile cannot be empty")


@dataclass(frozen=True, slots=True)
class Mem0FeatureEvidence:
    supporting_lineages: tuple[str, ...] = ()
    conflicting_lineages: tuple[str, ...] = ()
    update_detected: bool = False
    profile: str = "mem0-collective-features-v1"

    def __post_init__(self) -> None:
        if not self.profile.strip():
            raise ValueError("Mem0 feature profile cannot be empty")
        if any(
            not value.strip()
            for value in (*self.supporting_lineages, *self.conflicting_lineages)
        ):
            raise ValueError("Mem0 lineage identifiers cannot be empty")

    @property
    def unique_supporting_lineages(self) -> frozenset[str]:
        return frozenset(self.supporting_lineages)

    @property
    def unique_conflicting_lineages(self) -> frozenset[str]:
        return frozenset(self.conflicting_lineages)


@dataclass(frozen=True, slots=True)
class TemporalFeatureEvidence:
    current_support_ratio: float
    superseded_ratio: float
    temporal_conflict_ratio: float
    profile: str = "temporal-collective-features-v1"

    def __post_init__(self) -> None:
        for name in (
            "current_support_ratio",
            "superseded_ratio",
            "temporal_conflict_ratio",
        ):
            _bounded(name, getattr(self, name))
        if not self.profile.strip():
            raise ValueError("Temporal feature profile cannot be empty")


@dataclass(frozen=True, slots=True)
class ImpactFeatureEvidence:
    current_influence: float
    candidate_influence: float
    unrelated_max_delta: float
    target_rank_change: int = 0
    profile: str = "shadow-impact-v1"

    def __post_init__(self) -> None:
        for name in ("current_influence", "candidate_influence", "unrelated_max_delta"):
            _bounded(name, getattr(self, name))
        if not self.profile.strip():
            raise ValueError("impact profile cannot be empty")


@dataclass(frozen=True, slots=True)
class PolicyFeatureEvidence:
    sensitive: bool = False
    scope_violation: bool = False
    abuse_anomaly: float = 0.0
    profile: str = "collective-risk-policy-v1"

    def __post_init__(self) -> None:
        _bounded("abuse_anomaly", self.abuse_anomaly)
        if not self.profile.strip():
            raise ValueError("policy feature profile cannot be empty")


@dataclass(frozen=True, slots=True)
class FeatureExtractionRequest:
    entry_id: str
    source_concept_id: str
    target_concept_id: str
    ledger: LedgerFeatureEvidence
    approved_alignment_confidence: float | None = None
    vectors: VectorFeatureEvidence | None = None
    mem0: Mem0FeatureEvidence | None = None
    temporal: TemporalFeatureEvidence | None = None
    impact: ImpactFeatureEvidence | None = None
    policy: PolicyFeatureEvidence = PolicyFeatureEvidence()

    def __post_init__(self) -> None:
        if not self.entry_id.strip():
            raise ValueError("entry_id cannot be empty")
        if self.source_concept_id == self.target_concept_id:
            raise ValueError("feature extraction requires two different concepts")
        if self.approved_alignment_confidence is not None:
            _bounded("approved_alignment_confidence", self.approved_alignment_confidence)


@dataclass(frozen=True, slots=True)
class FeatureComponent:
    name: str
    value: float | bool | int | str
    provider: str
    profile: str
    explanation: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.provider.strip() or not self.profile.strip():
            raise ValueError("feature component identity cannot be empty")
        if not self.explanation.strip():
            raise ValueError("feature component explanation cannot be empty")


@dataclass(frozen=True, slots=True)
class ExtractedReviewFeatures:
    feature_profile: str
    triage: CheapReviewFeatures
    components: tuple[FeatureComponent, ...]
    evidence_sources: tuple[str, ...]
    missing_sources: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_profile": self.feature_profile,
            "triage": asdict(self.triage),
            "components": [asdict(item) for item in self.components],
            "evidence_sources": list(self.evidence_sources),
            "missing_sources": list(self.missing_sources),
        }


@dataclass(frozen=True, slots=True)
class FeatureTriageResult:
    features: ExtractedReviewFeatures
    decision: TriageDecision

    def as_dict(self) -> dict[str, Any]:
        return {
            "features": self.features.as_dict(),
            "decision": self.decision.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class FeatureExtractionPolicy:
    profile: str = "collective-feature-extractor-v1"
    maturity_support_units: float = 5.0
    maturity_contributors: int = 5
    common_contributors: int = 10
    vector_lower_similarity: float = 0.65
    vector_upper_similarity: float = 0.85
    vector_clear_margin: float = 0.15
    impact_influence_scale: float = 0.30
    impact_regression_scale: float = 0.10
    impact_rank_scale: int = 5
    missing_processor_penalty: float = 0.25
    missing_impact_default: float = 0.50

    def __post_init__(self) -> None:
        if not self.profile.strip():
            raise ValueError("feature extraction profile cannot be empty")
        for name in (
            "maturity_support_units",
            "vector_clear_margin",
            "impact_influence_scale",
            "impact_regression_scale",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be greater than zero")
        if self.maturity_contributors <= 0 or self.common_contributors <= 0:
            raise ValueError("contributor thresholds must be greater than zero")
        if self.impact_rank_scale <= 0:
            raise ValueError("impact_rank_scale must be greater than zero")
        for name in (
            "vector_lower_similarity",
            "vector_upper_similarity",
            "missing_processor_penalty",
            "missing_impact_default",
        ):
            _bounded(name, getattr(self, name))
        if self.vector_upper_similarity <= self.vector_lower_similarity:
            raise ValueError("vector upper similarity must exceed lower similarity")


class CollectiveFeatureExtractor:
    """Combines measurable graph and cached-processor evidence without model calls."""

    def __init__(self, policy: FeatureExtractionPolicy | None = None) -> None:
        self.policy = policy or FeatureExtractionPolicy()

    def extract(self, request: FeatureExtractionRequest) -> ExtractedReviewFeatures:
        components: list[FeatureComponent] = []
        missing: list[str] = []
        ledger = request.ledger
        positive = sum(item.positive_support for item in ledger.contributors)
        negative = sum(item.negative_support for item in ledger.contributors)
        total = positive + negative
        contributor_count = sum(item.total_support > 0 for item in ledger.contributors)
        support_maturity = min(1.0, total / self.policy.maturity_support_units)
        contributor_maturity = min(
            1.0, contributor_count / self.policy.maturity_contributors
        )
        maturity = min(support_maturity, contributor_maturity)
        balance = 0.0 if total == 0 else 2.0 * min(positive, negative) / total
        conflict = balance * support_maturity
        concentration = (
            0.0
            if total == 0
            else max((item.total_support for item in ledger.contributors), default=0.0) / total
        )
        rarity = 1.0 - min(
            1.0,
            ledger.concept_independent_contributors / self.policy.common_contributors,
        )
        insufficient_evidence = 1.0 - maturity
        self._add(
            components,
            "ledger_conflict",
            conflict,
            "ledger",
            ledger.profile,
            f"positive={positive:.3f}, negative={negative:.3f}, maturity={support_maturity:.3f}",
        )
        self._add(
            components,
            "contributor_concentration",
            concentration,
            "ledger",
            ledger.profile,
            f"largest contributor share across {contributor_count} contributing buckets",
        )
        self._add(
            components,
            "concept_rarity",
            rarity,
            "ledger",
            ledger.profile,
            f"{ledger.concept_independent_contributors} independent concept contributors",
        )
        self._add(
            components,
            "relationship_maturity",
            maturity,
            "ledger",
            ledger.profile,
            "minimum of support-volume and independent-contributor maturity",
        )

        vector_alignment = None
        vector_novelty = 0.0
        duplicate_risk = 0.0
        if request.vectors is not None:
            vector_alignment = self._vector_alignment(request.vectors)
            vector_novelty = 1.0 - request.vectors.best_similarity
            duplicate_risk = request.vectors.duplicate_fraction
            self._add(
                components,
                "vector_alignment_confidence",
                vector_alignment,
                "vectors",
                request.vectors.calibration_profile,
                (
                    f"best={request.vectors.best_similarity:.3f}, "
                    f"second={request.vectors.second_similarity:.3f}, "
                    f"model={request.vectors.provider}/{request.vectors.model}"
                ),
            )
            self._add(
                components,
                "vector_novelty",
                vector_novelty,
                "vectors",
                request.vectors.calibration_profile,
                "one minus nearest calibrated similarity",
            )
        else:
            missing.append("vectors")

        if request.approved_alignment_confidence is not None:
            alignment = request.approved_alignment_confidence
            alignment_provider = "approved_catalog"
            alignment_profile = "approved-concept-mapping-v1"
        elif vector_alignment is not None:
            alignment = vector_alignment
            alignment_provider = "vectors"
            alignment_profile = request.vectors.calibration_profile  # type: ignore[union-attr]
        else:
            alignment = 0.0
            alignment_provider = "missing"
            alignment_profile = self.policy.profile

        mem0_conflict = 0.0
        mem0_lineages = 0
        mem0_support_confidence = 0.0
        if request.mem0 is not None:
            supporting = request.mem0.unique_supporting_lineages
            conflicting = request.mem0.unique_conflicting_lineages
            mem0_lineages = len(supporting | conflicting)
            mem0_total = len(supporting) + len(conflicting)
            mem0_balance = (
                0.0
                if mem0_total == 0
                else 2.0 * min(len(supporting), len(conflicting)) / mem0_total
            )
            mem0_maturity = min(1.0, mem0_lineages / 3.0)
            mem0_conflict = mem0_balance * mem0_maturity
            if request.mem0.update_detected:
                mem0_conflict = max(mem0_conflict, 0.5)
            mem0_support_confidence = (
                min(1.0, len(supporting) / 3.0) * (1.0 - mem0_conflict)
            )
            self._add(
                components,
                "mem0_conflict",
                mem0_conflict,
                "mem0",
                request.mem0.profile,
                (
                    f"{len(supporting)} unique supporting and {len(conflicting)} unique "
                    "conflicting source lineages"
                ),
            )
            self._add(
                components,
                "mem0_unique_lineages",
                mem0_lineages,
                "mem0",
                request.mem0.profile,
                "duplicate derived facts sharing lineage count once",
            )
            self._add(
                components,
                "mem0_interpretation_support",
                mem0_support_confidence,
                "mem0",
                request.mem0.profile,
                "unique supporting lineages discounted by Mem0 conflict",
            )
        else:
            missing.append("mem0")

        if (
            request.approved_alignment_confidence is None
            and vector_alignment is None
            and mem0_support_confidence > 0
        ):
            mem0_alignment = 0.75 * mem0_support_confidence
            if mem0_alignment > alignment:
                alignment = mem0_alignment
                alignment_provider = "mem0"
                alignment_profile = request.mem0.profile  # type: ignore[union-attr]
        self._add(
            components,
            "alignment_confidence",
            alignment,
            alignment_provider,
            alignment_profile,
            (
                "approved catalog mapping wins; otherwise use calibrated vectors and "
                "bounded Mem0 corroboration"
            ),
        )

        temporal_uncertainty = 0.0
        if request.temporal is not None:
            temporal_uncertainty = max(
                request.temporal.superseded_ratio,
                request.temporal.temporal_conflict_ratio,
                1.0 - request.temporal.current_support_ratio,
            )
            self._add(
                components,
                "temporal_uncertainty",
                temporal_uncertainty,
                "temporal",
                request.temporal.profile,
                "maximum of supersession, temporal conflict, and missing current support",
            )
        else:
            missing.append("temporal")

        regression_risk = 0.0
        if request.impact is not None:
            influence_delta = abs(
                request.impact.candidate_influence - request.impact.current_influence
            )
            influence_impact = min(1.0, influence_delta / self.policy.impact_influence_scale)
            regression_risk = min(
                1.0,
                request.impact.unrelated_max_delta / self.policy.impact_regression_scale,
            )
            rank_impact = min(
                1.0,
                abs(request.impact.target_rank_change) / self.policy.impact_rank_scale,
            )
            expected_impact = max(influence_impact, regression_risk, rank_impact)
            self._add(
                components,
                "expected_impact",
                expected_impact,
                "shadow",
                request.impact.profile,
                (
                    f"influence_delta={influence_delta:.3f}, "
                    f"unrelated_delta={request.impact.unrelated_max_delta:.3f}, "
                    f"rank_change={request.impact.target_rank_change}"
                ),
            )
        else:
            expected_impact = self.policy.missing_impact_default
            missing.append("impact")
            self._add(
                components,
                "expected_impact",
                expected_impact,
                "missing",
                self.policy.profile,
                "conservative default because no shadow dry-run was supplied",
            )

        missing_penalty = self.policy.missing_processor_penalty * (1.0 - maturity)
        novelty_uncertainty = vector_novelty * expected_impact
        uncertainty = max(
            1.0 - alignment,
            conflict,
            insufficient_evidence,
            mem0_conflict,
            temporal_uncertainty,
            novelty_uncertainty,
            missing_penalty if missing else 0.0,
        )
        risk = max(
            1.0 if request.policy.sensitive else 0.0,
            1.0 if request.policy.scope_violation else 0.0,
            request.policy.abuse_anomaly,
            rarity,
            concentration,
            duplicate_risk,
            regression_risk,
            temporal_uncertainty,
        )
        novel = not ledger.relationship_exists
        self._add(
            components,
            "overall_uncertainty",
            uncertainty,
            "extractor",
            self.policy.profile,
            "maximum material uncertainty; processor agreement cannot create maturity",
        )
        self._add(
            components,
            "overall_risk",
            risk,
            "extractor",
            self.policy.profile,
            "maximum sensitivity, privacy, concentration, anomaly, and regression risk",
        )
        sources = ["ledger"]
        if request.vectors is not None:
            sources.append("vectors")
        if request.mem0 is not None:
            sources.append("mem0")
        if request.temporal is not None:
            sources.append("temporal")
        if request.impact is not None:
            sources.append("impact")
        return ExtractedReviewFeatures(
            feature_profile=self.policy.profile,
            triage=CheapReviewFeatures(
                entry_id=request.entry_id,
                source_concept_id=request.source_concept_id,
                target_concept_id=request.target_concept_id,
                uncertainty=uncertainty,
                expected_impact=expected_impact,
                risk=risk,
                conflict=max(conflict, mem0_conflict, temporal_uncertainty),
                contributor_concentration=concentration,
                concept_rarity=rarity,
                alignment_confidence=alignment,
                novel_relationship=novel,
                sensitive=request.policy.sensitive,
                candidate_regression=regression_risk >= 1.0,
            ),
            components=tuple(components),
            evidence_sources=tuple(sources),
            missing_sources=tuple(missing),
        )

    def _vector_alignment(self, evidence: VectorFeatureEvidence) -> float:
        if evidence.best_similarity < self.policy.vector_lower_similarity:
            return 0.0
        similarity_position = min(
            1.0,
            (evidence.best_similarity - self.policy.vector_lower_similarity)
            / (
                self.policy.vector_upper_similarity
                - self.policy.vector_lower_similarity
            ),
        )
        margin = evidence.best_similarity - evidence.second_similarity
        ambiguity_factor = min(1.0, margin / self.policy.vector_clear_margin)
        return similarity_position * ambiguity_factor

    @staticmethod
    def _add(
        components: list[FeatureComponent],
        name: str,
        value: float | bool | int | str,
        provider: str,
        profile: str,
        explanation: str,
    ) -> None:
        components.append(
            FeatureComponent(
                name=name,
                value=value,
                provider=provider,
                profile=profile,
                explanation=explanation,
            )
        )


class CollectiveFeatureTriage:
    """Runs deterministic feature extraction and the cheap review gate together."""

    def __init__(
        self,
        *,
        extractor: CollectiveFeatureExtractor | None = None,
        triage: CheapReviewTriage | None = None,
    ) -> None:
        self.extractor = extractor or CollectiveFeatureExtractor()
        self.triage = triage or CheapReviewTriage()

    def evaluate(self, request: FeatureExtractionRequest) -> FeatureTriageResult:
        features = self.extractor.extract(request)
        return FeatureTriageResult(
            features=features,
            decision=self.triage.evaluate(features.triage),
        )
