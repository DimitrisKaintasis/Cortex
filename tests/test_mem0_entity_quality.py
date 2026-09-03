from __future__ import annotations

import unittest
from pathlib import Path

from data_retrieval.benchmarks.mem0_entity_quality import (
    ExpectedRelationship,
    Mem0EntityQualityCase,
    Mem0EntityQualityThresholds,
    QualityAtom,
    load_mem0_entity_quality_fixture,
    score_mem0_entity_quality,
)
from data_retrieval.mem0.entities import (
    Mem0Entity,
    Mem0EntityRelationship,
    Mem0ProcessResult,
)


class Mem0EntityQualityTests(unittest.TestCase):
    def test_checked_in_fixture_is_valid(self) -> None:
        suite_id, thresholds, cases = load_mem0_entity_quality_fixture(
            Path("evals/mem0_entity_quality_v1.json")
        )

        self.assertEqual(suite_id, "mem0-entity-quality-v1")
        self.assertEqual(len(cases), 10)
        self.assertEqual(thresholds.evidence_precision, 0.95)

    def test_perfect_output_passes_all_thresholds(self) -> None:
        case = _case()
        output = _output(
            source="Alice",
            predicate="leads",
            target="Project Helios",
            evidence=("case:a1",),
        )

        report = score_mem0_entity_quality(
            suite_id="test",
            cases=(case,),
            outputs=(output,),
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.metrics["entity_recall"], 1.0)
        self.assertEqual(report.metrics["predicate_fidelity"], 1.0)
        self.assertEqual(report.metrics["evidence_recall"], 1.0)

    def test_direction_predicate_and_evidence_are_scored_separately(self) -> None:
        case = _case()
        reversed_output = _output(
            source="Project Helios",
            predicate="managed_by",
            target="Alice",
            evidence=("wrong:a1",),
        )
        thresholds = Mem0EntityQualityThresholds(
            entity_precision=0.0,
            entity_recall=0.0,
            relationship_endpoint_precision=0.0,
            relationship_endpoint_recall=0.0,
            relationship_direction_accuracy=1.0,
            predicate_fidelity=0.0,
            evidence_precision=0.0,
            evidence_recall=0.0,
            maximum_quarantine_rate=1.0,
            maximum_empty_output_rate=1.0,
        )

        report = score_mem0_entity_quality(
            suite_id="test",
            cases=(case,),
            outputs=(reversed_output,),
            thresholds=thresholds,
        )

        self.assertEqual(report.metrics["relationship_endpoint_recall"], 0.0)
        self.assertEqual(report.metrics["relationship_direction_accuracy"], 0.0)
        self.assertEqual(report.metrics["predicate_fidelity"], 1.0)
        self.assertEqual(report.metrics["evidence_relationships_scored"], 0)
        self.assertEqual(report.metrics["evidence_precision"], 1.0)
        self.assertEqual(report.metrics["evidence_recall"], 1.0)
        self.assertEqual(report.failed_thresholds, ("relationship_direction_accuracy",))

    def test_wrong_predicate_does_not_erase_correct_endpoints_or_lineage(self) -> None:
        case = _case()
        output = _output(
            source="Alice",
            predicate="owns",
            target="Project Helios",
            evidence=("case:a1",),
        )

        report = score_mem0_entity_quality(
            suite_id="test",
            cases=(case,),
            outputs=(output,),
        )

        self.assertEqual(report.metrics["relationship_endpoint_recall"], 1.0)
        self.assertEqual(report.metrics["relationship_direction_accuracy"], 1.0)
        self.assertEqual(report.metrics["predicate_fidelity"], 0.0)
        self.assertEqual(report.metrics["evidence_recall"], 1.0)
        self.assertIn("predicate_fidelity", report.failed_thresholds)


def _case() -> Mem0EntityQualityCase:
    return Mem0EntityQualityCase(
        case_id="case",
        atoms=(QualityAtom("case:a1", "Alice leads Project Helios."),),
        expected_entities=("Alice", "Project Helios"),
        expected_relationships=(
            ExpectedRelationship(
                source="Alice",
                target="Project Helios",
                accepted_predicates=("leads",),
                evidence_source_ids=("case:a1",),
            ),
        ),
    )


def _output(
    *, source: str, predicate: str, target: str, evidence: tuple[str, ...]
) -> Mem0ProcessResult:
    entities = (
        Mem0Entity("source", source, evidence),
        Mem0Entity("target", target, evidence),
    )
    return Mem0ProcessResult(
        entities=entities,
        relationships=(
            Mem0EntityRelationship(
                "relationship",
                "source",
                "target",
                predicate,
                evidence,
            ),
        ),
    )


if __name__ == "__main__":
    unittest.main()
