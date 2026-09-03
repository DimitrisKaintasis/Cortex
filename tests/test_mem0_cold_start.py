from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from data_retrieval.benchmarks.mem0_cold_start import Mem0ColdStartSuite


class _AlignedEmbedder:
    provider = "fixture"
    model = "aligned-v1"

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0) for _ in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return (1.0, 0.0)


class Mem0ColdStartSuiteTests(unittest.TestCase):
    def test_joint_profile_cannot_invent_edges_and_reports_noise(self) -> None:
        fixture = {
            "suite_id": "fixture-v1",
            "thresholds": {
                "relationship_endpoint_precision": 0.9,
                "relationship_endpoint_recall": 0.9,
            },
            "cases": [
                {
                    "case_id": "case-1",
                    "atoms": [
                        {
                            "source_id": "case-1:a1",
                            "content": "Alice leads Project Helios.",
                        }
                    ],
                    "expected_relationships": [
                        {
                            "source": "Alice",
                            "target": "Project Helios",
                            "accepted_predicates": ["leads"],
                            "evidence_source_ids": ["case-1:a1"],
                        }
                    ],
                }
            ],
        }
        mem0_report = {
            "suite_id": "fixture-v1",
            "cases": [
                {
                    "case_id": "case-1",
                    "actual_relationships": [
                        {
                            "source": "Alice",
                            "target": "Project Helios",
                            "predicate": "leads",
                            "evidence_source_ids": ["case-1:a1"],
                        },
                        {
                            "source": "Project Helios",
                            "target": "Alice",
                            "predicate": "owns",
                            "evidence_source_ids": ["case-1:a1"],
                        },
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture_path = root / "fixture.json"
            report_path = root / "report.json"
            fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
            report_path.write_text(json.dumps(mem0_report), encoding="utf-8")

            result = Mem0ColdStartSuite().run(
                fixture_path=fixture_path,
                mem0_report_path=report_path,
                embedder=_AlignedEmbedder(),
            )

        profiles = {item.profile: item for item in result.profiles}
        self.assertTrue(result.experiment_passed)
        self.assertFalse(result.promotion_passed)
        self.assertEqual(profiles["baseline"].typed_edges, 0)
        self.assertEqual(profiles["vectors_only"].typed_edges, 0)
        self.assertEqual(profiles["mem0_only"].typed_edges, 2)
        self.assertEqual(profiles["mem0_vector_provisional"].typed_edges, 2)
        self.assertEqual(profiles["mem0_vector_provisional"].precision, 0.5)
        self.assertTrue(
            all(item.initial_weight <= 0.25 for item in result.proposals)
        )


if __name__ == "__main__":
    unittest.main()
