from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from data_retrieval.benchmarks.collective_transfer import CollectiveTransferSuite
from data_retrieval.collective import (
    CollectivePolicy,
    ObservationOutcome,
    RelationshipObservation,
    ShadowCollectiveAggregator,
    SharedConcept,
)


class CollectiveCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 2, 12, tzinfo=UTC)
        self.source = SharedConcept.from_key("Storage Architecture")
        self.target = SharedConcept.from_key("PostgreSQL")
        self.policy = CollectivePolicy(policy_id="test-policy")

    def _observation(self) -> RelationshipObservation:
        return RelationshipObservation.create(
            local_event_key="event-1",
            contributor_bucket="epoch-bucket-a",
            source_concept_id=self.source.concept_id,
            target_concept_id=self.target.concept_id,
            outcome=ObservationOutcome.POSITIVE,
            support=0.5,
            observed_at=self.now,
            policy_version=self.policy.policy_id,
        )

    def test_shared_concept_identity_is_independent_of_spacing_and_case(self) -> None:
        equivalent = SharedConcept.from_key("  storage   architecture ")

        self.assertEqual(self.source, equivalent)

    def test_observation_rejects_private_or_unstable_concept_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "stable shared concept ID"):
            RelationshipObservation.create(
                local_event_key="event-1",
                contributor_bucket="epoch-bucket-a",
                source_concept_id="private:alice:storage",
                target_concept_id=self.target.concept_id,
                outcome=ObservationOutcome.POSITIVE,
                support=0.5,
                observed_at=self.now,
                policy_version=self.policy.policy_id,
            )

    def test_duplicate_id_with_conflicting_payload_is_rejected(self) -> None:
        observation = self._observation()
        conflicting = replace(observation, support=0.75)

        with self.assertRaisesRegex(ValueError, "conflicting payload"):
            ShadowCollectiveAggregator(self.policy).build(
                (observation, conflicting),
                as_of=self.now,
            )

    def test_future_observation_is_not_projected_early(self) -> None:
        future = replace(self._observation(), observed_at=self.now + timedelta(days=1))

        snapshot = ShadowCollectiveAggregator(self.policy).build((future,), as_of=self.now)

        self.assertEqual(snapshot.observation_count, 1)
        self.assertEqual(snapshot.relationships, ())


class CollectiveTransferSuiteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = Path(__file__).parents[1] / "evals" / "collective_transfer_v1.json"

    def test_default_fixture_passes_transfer_and_safety_checks(self) -> None:
        report = CollectiveTransferSuite().run(
            self.fixture,
            artifact_location=Path("collective-report.json"),
        )

        self.assertTrue(report.passed)
        self.assertEqual(len(report.checks), 18)
        self.assertTrue(all(check.passed for check in report.checks))
        self.assertEqual(report.baseline_ranking[0]["candidate_id"], "b-storage-graph")
        self.assertEqual(report.positive_ranking[0]["candidate_id"], "b-storage-postgresql")
        self.assertEqual(report.negative_ranking[0]["candidate_id"], "b-storage-graph")
        variants = {variant.policy_id: variant for variant in report.policy_variants}
        self.assertEqual(variants["control-no-learning"].positive_target_rank, 2)
        self.assertEqual(variants["unbounded-proportional-decay"].positive_target_rank, 1)
        self.assertEqual(variants["maximum-reference-with-outlier"].positive_target_rank, 2)
        self.assertEqual(variants["percentile-reference-with-outlier"].positive_target_rank, 1)
        serialized = json.dumps(report.as_dict())
        fixture = json.loads(self.fixture.read_text(encoding="utf-8"))
        self.assertTrue(
            all(sentinel not in serialized for sentinel in fixture["private_sentinels"])
        )

    def test_changed_transfer_expectation_fails_the_specific_check(self) -> None:
        fixture = json.loads(self.fixture.read_text(encoding="utf-8"))
        fixture["storage_case"]["candidates"][0]["baseline_score"] = 0.0
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "changed.json"
            changed.write_text(json.dumps(fixture), encoding="utf-8")
            report = CollectiveTransferSuite().run(changed)

        failed = {check.name for check in report.checks if not check.passed}
        self.assertFalse(report.passed)
        self.assertIn("positive_transfer_target_rank", failed)


if __name__ == "__main__":
    unittest.main()
