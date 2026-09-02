from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from data_retrieval.benchmarks.review_cascade import ReviewCascadeSuite
from data_retrieval.collective import (
    AIReviewEvent,
    AIReviewVerdict,
    CheapReviewFeatures,
    CheapReviewTriage,
    ReviewInfluencePolicy,
    SharedConcept,
    TriageDisposition,
)


class ReviewCascadeCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SharedConcept.from_key("storage architecture")
        self.target = SharedConcept.from_key("postgresql")

    def _features(self, **changes) -> CheapReviewFeatures:
        values = {
            "entry_id": "entry-1",
            "source_concept_id": self.source.concept_id,
            "target_concept_id": self.target.concept_id,
            "uncertainty": 0.1,
            "expected_impact": 0.3,
            "risk": 0.1,
        }
        values.update(changes)
        return CheapReviewFeatures(**values)

    def test_routine_entry_uses_cheap_path(self) -> None:
        decision = CheapReviewTriage().evaluate(self._features())

        self.assertEqual(decision.disposition, TriageDisposition.APPLY_CHEAP)
        self.assertTrue(decision.apply_before_review)

    def test_sensitive_entry_is_held_for_review(self) -> None:
        decision = CheapReviewTriage().evaluate(
            self._features(sensitive=True, expected_impact=0.8, risk=0.9)
        )

        self.assertEqual(decision.disposition, TriageDisposition.HOLD_FOR_REVIEW)
        self.assertFalse(decision.apply_before_review)
        self.assertIn("sensitive", decision.reason_codes)

    def test_review_strength_is_calibrated_and_bounded_by_policy(self) -> None:
        review = AIReviewEvent.create(
            local_review_key="review-1",
            reviewer_family="small-local-reviewer",
            source_concept_id=self.source.concept_id,
            target_concept_id=self.target.concept_id,
            verdict=AIReviewVerdict.SUPPORT,
            confidence=1.0,
            recommended_strength=1.0,
            reason_codes=("outcome_supported",),
            reviewed_at=datetime(2026, 9, 2, tzinfo=UTC),
            review_profile="review-v1",
            evidence_digest="digest-1",
        )

        observation = ReviewInfluencePolicy(maximum_equivalent_units=5.0).to_observation(
            review,
            reviewer_reliability=1.0,
            aggregation_epoch="2026-09",
        )

        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertEqual(observation.support, 5.0)
        self.assertEqual(observation.channel.value, "ai_review")

    def test_abstaining_review_makes_no_weight_observation(self) -> None:
        review = AIReviewEvent.create(
            local_review_key="review-1",
            reviewer_family="small-local-reviewer",
            source_concept_id=self.source.concept_id,
            target_concept_id=self.target.concept_id,
            verdict=AIReviewVerdict.ABSTAIN,
            confidence=1.0,
            recommended_strength=0.0,
            reason_codes=("insufficient_evidence",),
            reviewed_at=datetime(2026, 9, 2, tzinfo=UTC),
            review_profile="review-v1",
            evidence_digest="digest-1",
        )

        observation = ReviewInfluencePolicy().to_observation(
            review,
            reviewer_reliability=1.0,
            aggregation_epoch="2026-09",
        )

        self.assertIsNone(observation)


class ReviewCascadeSuiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Path(__file__).parents[1] / "evals" / "review_cascade_v1.json"

    def test_cascade_matches_review_everything_with_fewer_calls(self) -> None:
        report = ReviewCascadeSuite().run(
            self.fixture,
            artifact_location=Path("review-cascade-report.json"),
        )

        self.assertTrue(report.passed)
        self.assertEqual(len(report.checks), 17)
        self.assertEqual(report.cheap_correct, 10)
        self.assertEqual(report.always_review_correct, 13)
        self.assertEqual(report.cascade_correct, 13)
        self.assertEqual(report.cascade_review_count, 3)
        self.assertLess(report.cascade_cost_units, report.always_review_cost_units * 0.3)
        json.dumps(report.as_dict())


if __name__ == "__main__":
    unittest.main()
