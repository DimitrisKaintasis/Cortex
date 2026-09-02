from __future__ import annotations

import json
import platform
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from data_retrieval.collective import (
    AIReviewEvent,
    AIReviewVerdict,
    CheapReviewFeatures,
    CheapReviewTriage,
    CollectivePolicy,
    EvidenceChannel,
    ObservationOutcome,
    RelationshipObservation,
    RelationshipState,
    ReviewBudgetPlanner,
    ReviewInfluencePolicy,
    ShadowCollectiveAggregator,
    SharedConcept,
    TriageDisposition,
)
from data_retrieval.core.identifiers import content_hash

from .collective_transfer import ExperimentCheck


@dataclass(frozen=True, slots=True)
class CascadeScenarioResult:
    scenario_id: str
    expected_verdict: str
    cheap_verdict: str
    review_verdict: str
    cascade_verdict: str
    escalated: bool
    disposition: str
    priority: float
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewCascadeReport:
    suite_id: str
    fixture_path: str
    fixture_hash: str
    code_revision: str
    working_tree_dirty: bool
    python_version: str
    duration_ms: float
    artifact_location: str | None
    passed: bool
    checks: tuple[ExperimentCheck, ...]
    scenario_count: int
    cheap_correct: int
    always_review_correct: int
    cascade_correct: int
    cascade_review_count: int
    cheap_cost_units: float
    always_review_cost_units: float
    cascade_cost_units: float
    scenarios: tuple[CascadeScenarioResult, ...]
    limitations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "fixture_path": self.fixture_path,
            "fixture_hash": self.fixture_hash,
            "code_revision": self.code_revision,
            "working_tree_dirty": self.working_tree_dirty,
            "python_version": self.python_version,
            "duration_ms": self.duration_ms,
            "artifact_location": self.artifact_location,
            "passed": self.passed,
            "check_count": len(self.checks),
            "passed_check_count": sum(check.passed for check in self.checks),
            "scenario_count": self.scenario_count,
            "cheap_correct": self.cheap_correct,
            "always_review_correct": self.always_review_correct,
            "cascade_correct": self.cascade_correct,
            "cascade_review_count": self.cascade_review_count,
            "cheap_cost_units": self.cheap_cost_units,
            "always_review_cost_units": self.always_review_cost_units,
            "cascade_cost_units": self.cascade_cost_units,
            "checks": [asdict(check) for check in self.checks],
            "scenarios": [asdict(item) for item in self.scenarios],
            "limitations": list(self.limitations),
        }


class ReviewCascadeSuite:
    """Compares cheap-only, review-everything, and deterministic gated review."""

    def run(
        self,
        fixture_path: Path,
        *,
        artifact_location: Path | None = None,
    ) -> ReviewCascadeReport:
        started = time.perf_counter()
        fixture_text = fixture_path.read_text(encoding="utf-8")
        fixture = json.loads(fixture_text)
        if not isinstance(fixture, dict) or fixture.get("fixture_id") != "review-cascade-v1":
            raise ValueError("review cascade fixture must have fixture_id review-cascade-v1")
        as_of = self._datetime(fixture["as_of"])
        source = SharedConcept.from_key(fixture["concepts"]["source"])
        target = SharedConcept.from_key(fixture["concepts"]["target"])
        triage = CheapReviewTriage()
        results: list[CascadeScenarioResult] = []
        decisions = []
        reviews: dict[str, AIReviewEvent] = {}
        for value in fixture["scenarios"]:
            features = self._features(value, source=source, target=target)
            decision = triage.evaluate(features)
            review = self._review(value, source=source, target=target, reviewed_at=as_of)
            decisions.append(decision)
            reviews[value["id"]] = review
            cascade_verdict = (
                review.verdict.value if decision.escalated else value["cheap_verdict"]
            )
            results.append(
                CascadeScenarioResult(
                    scenario_id=value["id"],
                    expected_verdict=value["expected_verdict"],
                    cheap_verdict=value["cheap_verdict"],
                    review_verdict=review.verdict.value,
                    cascade_verdict=cascade_verdict,
                    escalated=decision.escalated,
                    disposition=decision.disposition.value,
                    priority=decision.priority,
                    reason_codes=decision.reason_codes,
                )
            )

        scenario_count = len(results)
        cheap_correct = sum(item.cheap_verdict == item.expected_verdict for item in results)
        always_correct = sum(item.review_verdict == item.expected_verdict for item in results)
        cascade_correct = sum(item.cascade_verdict == item.expected_verdict for item in results)
        review_count = sum(item.escalated for item in results)
        cheap_unit = float(fixture["costs"]["cheap_evaluation"])
        review_unit = float(fixture["costs"]["expensive_review"])
        cheap_cost = scenario_count * cheap_unit
        always_cost = cheap_cost + scenario_count * review_unit
        cascade_cost = cheap_cost + review_count * review_unit
        by_id = {item.scenario_id: item for item in results}
        selected = ReviewBudgetPlanner.select(tuple(decisions), maximum_reviews=2)
        review_checks = self._review_influence_checks(
            source=source,
            target=target,
            reviewed_at=as_of,
            conflict_review=reviews["conflicting-high-impact"],
        )
        checks = (
            self._check("cheap_only_misses_risky_cases", 10, cheap_correct),
            self._check("review_everything_quality", scenario_count, always_correct),
            self._check("cascade_matches_review_quality", always_correct, cascade_correct),
            self._check("cascade_reviews_only_risky_cases", 3, review_count),
            self._check(
                "cascade_cost_below_thirty_percent",
                True,
                cascade_cost / always_cost < 0.3,
            ),
            self._check(
                "routine_entry_uses_cheap_path",
                TriageDisposition.APPLY_CHEAP.value,
                by_id["routine-support-1"].disposition,
            ),
            self._check(
                "conflict_is_held_for_review",
                TriageDisposition.HOLD_FOR_REVIEW.value,
                by_id["conflicting-high-impact"].disposition,
            ),
            self._check(
                "novel_alignment_is_reviewed",
                True,
                by_id["novel-uncertain-alignment"].escalated,
            ),
            self._check(
                "sensitive_entry_is_held",
                TriageDisposition.HOLD_FOR_REVIEW.value,
                by_id["sensitive-rare-concept"].disposition,
            ),
            self._check(
                "budget_selects_highest_priorities",
                ["sensitive-rare-concept", "conflicting-high-impact"],
                [item.entry_id for item in selected],
            ),
            *review_checks,
        )
        revision, dirty = self._git_identity(fixture_path.parent)
        return ReviewCascadeReport(
            suite_id=fixture["fixture_id"],
            fixture_path=str(fixture_path),
            fixture_hash=content_hash(fixture_text),
            code_revision=revision,
            working_tree_dirty=dirty,
            python_version=platform.python_version(),
            duration_ms=(time.perf_counter() - started) * 1_000,
            artifact_location=str(artifact_location) if artifact_location else None,
            passed=all(check.passed for check in checks),
            checks=checks,
            scenario_count=scenario_count,
            cheap_correct=cheap_correct,
            always_review_correct=always_correct,
            cascade_correct=cascade_correct,
            cascade_review_count=review_count,
            cheap_cost_units=cheap_cost,
            always_review_cost_units=always_cost,
            cascade_cost_units=cascade_cost,
            scenarios=tuple(results),
            limitations=(
                "Fixed review verdicts test orchestration, not real model judgment quality.",
                "The fixture is deliberately separable and cannot establish production thresholds.",
                "Cost units are relative model-call units, not provider prices.",
                "Coordinated identities and private-evidence review remain unimplemented.",
            ),
        )

    def _review_influence_checks(
        self,
        *,
        source: SharedConcept,
        target: SharedConcept,
        reviewed_at: datetime,
        conflict_review: AIReviewEvent,
    ) -> tuple[ExperimentCheck, ...]:
        influence = ReviewInfluencePolicy(maximum_equivalent_units=5.0)
        review_observation = influence.to_observation(
            conflict_review,
            reviewer_reliability=1.0,
            aggregation_epoch="2026-09",
        )
        assert review_observation is not None
        repeated_review = AIReviewEvent.create(
            local_review_key="conflict-repeat",
            reviewer_family=conflict_review.reviewer_family,
            source_concept_id=source.concept_id,
            target_concept_id=target.concept_id,
            verdict=AIReviewVerdict.OPPOSE,
            confidence=1.0,
            recommended_strength=1.0,
            reason_codes=("conflicting_support",),
            reviewed_at=reviewed_at,
            review_profile="fixture-review-v1",
            evidence_digest="digest-repeat",
        )
        repeated_observation = influence.to_observation(
            repeated_review,
            reviewer_reliability=1.0,
            aggregation_epoch="2026-09",
        )
        assert repeated_observation is not None
        behavioral = RelationshipObservation.create(
            local_event_key="behavioral-conflict",
            contributor_bucket="behavioral-contributor",
            source_concept_id=source.concept_id,
            target_concept_id=target.concept_id,
            outcome=ObservationOutcome.POSITIVE,
            support=1.0,
            observed_at=reviewed_at,
            policy_version="cascade-fixture-v1",
        )
        policy = CollectivePolicy(
            policy_id="cascade-fixture-v1",
            half_life_days=None,
            reviewer_support_cap=5.0,
        )
        projection = ShadowCollectiveAggregator(policy).build(
            (behavioral, review_observation, repeated_observation),
            as_of=reviewed_at,
        ).relationships[0]
        abstention = AIReviewEvent.create(
            local_review_key="abstain",
            reviewer_family="fixture-reviewer",
            source_concept_id=source.concept_id,
            target_concept_id=target.concept_id,
            verdict=AIReviewVerdict.ABSTAIN,
            confidence=1.0,
            recommended_strength=0.0,
            reason_codes=("insufficient_evidence",),
            reviewed_at=reviewed_at,
            review_profile="fixture-review-v1",
            evidence_digest="digest-abstain",
        )
        exported = conflict_review.as_dict()
        forbidden = {"content", "payload", "reasoning", "chain_of_thought"}
        return (
            self._check(
                "review_uses_separate_channel",
                EvidenceChannel.AI_REVIEW,
                review_observation.channel,
            ),
            self._check("review_strength_is_bounded", True, review_observation.support <= 5.0),
            self._check(
                "same_reviewer_repetition_is_capped",
                5.0,
                projection.review_negative_support,
            ),
            self._check(
                "behavioral_support_remains_visible",
                1.0,
                projection.behavioral_positive_support,
            ),
            self._check("strong_review_can_inhibit", RelationshipState.INHIBITED, projection.state),
            self._check(
                "abstention_changes_no_weight",
                None,
                influence.to_observation(
                    abstention,
                    reviewer_reliability=1.0,
                    aggregation_epoch="2026-09",
                ),
            ),
            self._check(
                "review_export_has_no_reasoning",
                [],
                sorted(forbidden.intersection(exported)),
            ),
        )

    @staticmethod
    def _features(
        value: dict[str, Any],
        *,
        source: SharedConcept,
        target: SharedConcept,
    ) -> CheapReviewFeatures:
        features = value["features"]
        return CheapReviewFeatures(
            entry_id=value["id"],
            source_concept_id=source.concept_id,
            target_concept_id=target.concept_id,
            uncertainty=float(features["uncertainty"]),
            expected_impact=float(features["expected_impact"]),
            risk=float(features["risk"]),
            conflict=float(features.get("conflict", 0.0)),
            contributor_concentration=float(features.get("contributor_concentration", 0.0)),
            concept_rarity=float(features.get("concept_rarity", 0.0)),
            alignment_confidence=float(features.get("alignment_confidence", 1.0)),
            novel_relationship=bool(features.get("novel_relationship", False)),
            sensitive=bool(features.get("sensitive", False)),
            candidate_regression=bool(features.get("candidate_regression", False)),
        )

    @staticmethod
    def _review(
        value: dict[str, Any],
        *,
        source: SharedConcept,
        target: SharedConcept,
        reviewed_at: datetime,
    ) -> AIReviewEvent:
        review = value["review"]
        return AIReviewEvent.create(
            local_review_key=value["id"],
            reviewer_family="fixture-reviewer",
            source_concept_id=source.concept_id,
            target_concept_id=target.concept_id,
            verdict=AIReviewVerdict(review["verdict"]),
            confidence=float(review["confidence"]),
            recommended_strength=float(review["strength"]),
            reason_codes=("fixture_verdict",),
            reviewed_at=reviewed_at,
            review_profile="fixture-review-v1",
            evidence_digest=content_hash(value["id"]),
        )

    @staticmethod
    def _check(name: str, expected: Any, actual: Any) -> ExperimentCheck:
        def json_value(value: Any) -> Any:
            if hasattr(value, "value"):
                return value.value
            return value

        return ExperimentCheck(
            name=name,
            passed=expected == actual,
            expected=json_value(expected),
            actual=json_value(actual),
        )

    @staticmethod
    def _datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("fixture timestamps must be timezone-aware")
        return parsed

    @staticmethod
    def _git_identity(start: Path) -> tuple[str, bool]:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=start,
            capture_output=True,
            text=True,
            check=False,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=start,
            capture_output=True,
            text=True,
            check=False,
        )
        return revision.stdout.strip() or "unknown", bool(dirty.stdout.strip())
