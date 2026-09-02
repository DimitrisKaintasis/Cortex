from __future__ import annotations

import json
import unittest

from data_retrieval.collective import (
    CollectiveFeatureExtractor,
    CollectiveFeatureTriage,
    ContributorSupport,
    FeatureExtractionRequest,
    ImpactFeatureEvidence,
    LedgerFeatureEvidence,
    Mem0FeatureEvidence,
    PolicyFeatureEvidence,
    SharedConcept,
    TemporalFeatureEvidence,
    TriageDisposition,
    VectorFeatureEvidence,
)


class CollectiveFeatureExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = SharedConcept.from_key("storage architecture")
        self.target = SharedConcept.from_key("postgresql")
        self.extractor = CollectiveFeatureExtractor()

    @staticmethod
    def _contributors(
        count: int,
        *,
        positive: float = 1.0,
        negative: float = 0.0,
    ) -> tuple[ContributorSupport, ...]:
        return tuple(
            ContributorSupport(
                contributor_bucket=f"bucket-{index}",
                positive_support=positive,
                negative_support=negative,
            )
            for index in range(count)
        )

    def _request(self, **changes) -> FeatureExtractionRequest:
        values = {
            "entry_id": "entry-1",
            "source_concept_id": self.source.concept_id,
            "target_concept_id": self.target.concept_id,
            "ledger": LedgerFeatureEvidence(
                relationship_exists=True,
                contributors=self._contributors(5),
                concept_independent_contributors=20,
            ),
            "approved_alignment_confidence": 1.0,
            "vectors": VectorFeatureEvidence(
                best_similarity=0.92,
                second_similarity=0.50,
                duplicate_fraction=0.1,
                provider="fixture",
                model="fixture-embedding-v1",
                calibration_profile="fixture-vector-calibration-v1",
            ),
            "mem0": Mem0FeatureEvidence(
                supporting_lineages=("atom-a", "atom-b", "atom-c"),
            ),
            "temporal": TemporalFeatureEvidence(
                current_support_ratio=1.0,
                superseded_ratio=0.0,
                temporal_conflict_ratio=0.0,
            ),
            "impact": ImpactFeatureEvidence(
                current_influence=0.40,
                candidate_influence=0.45,
                unrelated_max_delta=0.0,
            ),
        }
        values.update(changes)
        return FeatureExtractionRequest(**values)

    @staticmethod
    def _components(result) -> dict[str, object]:
        return {item.name: item.value for item in result.components}

    def test_mature_consistent_relationship_uses_cheap_path(self) -> None:
        result = CollectiveFeatureTriage().evaluate(self._request())

        self.assertEqual(result.decision.disposition, TriageDisposition.APPLY_CHEAP)
        self.assertLess(result.features.triage.uncertainty, 0.05)
        self.assertEqual(result.features.missing_sources, ())

    def test_immature_missing_evidence_is_held_for_review(self) -> None:
        request = self._request(
            ledger=LedgerFeatureEvidence(
                relationship_exists=False,
                contributors=self._contributors(1, positive=0.2),
                concept_independent_contributors=1,
            ),
            approved_alignment_confidence=None,
            vectors=None,
            mem0=None,
            temporal=None,
            impact=None,
        )

        result = CollectiveFeatureTriage().evaluate(request)

        self.assertEqual(result.decision.disposition, TriageDisposition.HOLD_FOR_REVIEW)
        self.assertEqual(result.features.triage.uncertainty, 1.0)
        self.assertEqual(
            set(result.features.missing_sources),
            {"vectors", "mem0", "temporal", "impact"},
        )

    def test_clear_cached_vector_match_can_support_alignment(self) -> None:
        request = self._request(approved_alignment_confidence=None)

        result = self.extractor.extract(request)

        self.assertEqual(result.triage.alignment_confidence, 1.0)
        self.assertIn("vectors", result.evidence_sources)

    def test_ambiguous_vector_margin_triggers_review(self) -> None:
        request = self._request(
            approved_alignment_confidence=None,
            vectors=VectorFeatureEvidence(
                best_similarity=0.90,
                second_similarity=0.88,
                duplicate_fraction=0.0,
                provider="fixture",
                model="fixture-embedding-v1",
                calibration_profile="fixture-vector-calibration-v1",
            ),
            impact=ImpactFeatureEvidence(
                current_influence=0.20,
                candidate_influence=0.41,
                unrelated_max_delta=0.0,
            ),
        )

        result = CollectiveFeatureTriage().evaluate(request)

        self.assertTrue(result.decision.escalated)
        self.assertIn("uncertain_alignment", result.decision.reason_codes)

    def test_mem0_duplicate_facts_share_one_source_lineage(self) -> None:
        request = self._request(
            mem0=Mem0FeatureEvidence(
                supporting_lineages=("atom-a", "atom-a", "atom-a"),
            )
        )

        result = self.extractor.extract(request)

        self.assertEqual(self._components(result)["mem0_unique_lineages"], 1)
        self.assertEqual(result.triage.conflict, 0.0)

    def test_mem0_can_corroborate_interpretation_but_not_claim_full_confidence(self) -> None:
        request = self._request(
            approved_alignment_confidence=None,
            vectors=None,
            mem0=Mem0FeatureEvidence(
                supporting_lineages=("atom-a", "atom-b", "atom-c"),
            ),
        )

        result = self.extractor.extract(request)

        self.assertEqual(result.triage.alignment_confidence, 0.75)
        self.assertEqual(self._components(result)["relationship_maturity"], 1.0)

    def test_mem0_conflict_escalates_without_creating_contributors(self) -> None:
        request = self._request(
            mem0=Mem0FeatureEvidence(
                supporting_lineages=("atom-a", "atom-b"),
                conflicting_lineages=("atom-c", "atom-d"),
            ),
            impact=ImpactFeatureEvidence(
                current_influence=0.20,
                candidate_influence=0.41,
                unrelated_max_delta=0.0,
            ),
        )

        result = CollectiveFeatureTriage().evaluate(request)

        self.assertTrue(result.decision.escalated)
        self.assertEqual(result.features.triage.conflict, 1.0)
        self.assertEqual(
            self._components(result.features)["relationship_maturity"],
            1.0,
        )

    def test_temporal_supersession_holds_high_impact_update(self) -> None:
        request = self._request(
            temporal=TemporalFeatureEvidence(
                current_support_ratio=0.1,
                superseded_ratio=0.9,
                temporal_conflict_ratio=0.8,
            ),
            impact=ImpactFeatureEvidence(
                current_influence=0.20,
                candidate_influence=0.41,
                unrelated_max_delta=0.0,
            ),
        )

        result = CollectiveFeatureTriage().evaluate(request)

        self.assertEqual(result.decision.disposition, TriageDisposition.HOLD_FOR_REVIEW)
        self.assertEqual(result.features.triage.conflict, 0.9)

    def test_missing_processors_do_not_penalize_mature_ledger_evidence(self) -> None:
        request = self._request(vectors=None, mem0=None, temporal=None)

        result = CollectiveFeatureTriage().evaluate(request)

        self.assertEqual(result.decision.disposition, TriageDisposition.APPLY_CHEAP)
        self.assertEqual(result.features.triage.uncertainty, 0.0)
        self.assertEqual(set(result.features.missing_sources), {"vectors", "mem0", "temporal"})

    def test_processor_agreement_cannot_manufacture_relationship_maturity(self) -> None:
        request = self._request(
            ledger=LedgerFeatureEvidence(
                relationship_exists=False,
                contributors=self._contributors(1, positive=0.2),
                concept_independent_contributors=1,
            ),
            approved_alignment_confidence=None,
            impact=ImpactFeatureEvidence(
                current_influence=0.0,
                candidate_influence=0.18,
                unrelated_max_delta=0.0,
            ),
        )

        result = CollectiveFeatureTriage().evaluate(request)

        self.assertTrue(result.decision.escalated)
        self.assertGreater(result.features.triage.uncertainty, 0.9)
        self.assertLess(self._components(result.features)["relationship_maturity"], 0.1)

    def test_sensitive_policy_is_a_hard_hold(self) -> None:
        result = CollectiveFeatureTriage().evaluate(
            self._request(policy=PolicyFeatureEvidence(sensitive=True))
        )

        self.assertEqual(result.decision.disposition, TriageDisposition.HOLD_FOR_REVIEW)
        self.assertIn("sensitive", result.decision.reason_codes)

    def test_feature_output_does_not_export_contributor_or_lineage_identifiers(self) -> None:
        private_bucket = "PRIVATE_CONTRIBUTOR_SENTINEL"
        private_lineage = "PRIVATE_ATOM_LINEAGE_SENTINEL"
        request = self._request(
            ledger=LedgerFeatureEvidence(
                relationship_exists=True,
                contributors=(
                    ContributorSupport(
                        contributor_bucket=private_bucket,
                        positive_support=5.0,
                    ),
                ),
                concept_independent_contributors=10,
            ),
            mem0=Mem0FeatureEvidence(supporting_lineages=(private_lineage,)),
        )

        serialized = json.dumps(self.extractor.extract(request).as_dict())

        self.assertNotIn(private_bucket, serialized)
        self.assertNotIn(private_lineage, serialized)


if __name__ == "__main__":
    unittest.main()
