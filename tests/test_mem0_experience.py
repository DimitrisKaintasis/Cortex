from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from data_retrieval.benchmarks.longmemeval import LongMemEvalIngestService
from data_retrieval.benchmarks.mem0_experience import Mem0ExperienceSuite
from data_retrieval.core.identifiers import content_hash
from data_retrieval.domain.models import AtomLinkRelation
from data_retrieval.mem0 import (
    Mem0Entity,
    Mem0EntityImportService,
    Mem0EntityRelationship,
    Mem0VectorCalibrationService,
)
from data_retrieval.services.learning import ATOM_CO_USED_LEARNING_POLICY
from data_retrieval.storage.memory import InMemoryRepository


class _AlignedEmbedder:
    provider = "fixture"
    model = "aligned-v1"

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0) for _ in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return (1.0, 0.0)


class Mem0ExperienceSuiteTests(unittest.TestCase):
    def test_all_relevant_feedback_creates_auditable_co_used_learning(self) -> None:
        dataset = [
            {
                "question_id": "question-1",
                "question_type": "multi-session",
                "question": "Did I visit Athens and Lisbon?",
                "answer": "Athens and Lisbon",
                "question_date": "2024/03/03 (Sun) 12:30",
                "haystack_session_ids": ["session-1", "session-2", "session-3"],
                "haystack_dates": [
                    "2024/03/01 (Fri) 11:15",
                    "2024/03/02 (Sat) 11:15",
                    "2024/03/02 (Sat) 12:15",
                ],
                "haystack_sessions": [
                    [{"role": "user", "content": "I visited Athens.", "has_answer": True}],
                    [{"role": "user", "content": "I visited Lisbon.", "has_answer": True}],
                    [{"role": "user", "content": "I stayed home.", "has_answer": False}],
                ],
                "answer_session_ids": ["session-1", "session-2"],
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_path = root / "dataset.json"
            feature_path = root / "features.json"
            dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
            repository = InMemoryRepository()
            imported = LongMemEvalIngestService(repository).ingest_path(
                path=dataset_path,
                dataset_id="experience-test",
                namespace_prefix="experience",
            )
            case = imported.cases[0]
            first, second = case.evidence_atom_ids
            Mem0EntityImportService(repository).import_graph(
                namespace=case.namespace,
                batch_id="batch-1",
                entities=(
                    Mem0Entity("athens", "Athens", (first,)),
                    Mem0Entity("lisbon", "Lisbon", (second,)),
                ),
                relationships=(
                    Mem0EntityRelationship(
                        "relation-1",
                        "athens",
                        "lisbon",
                        "visited_with",
                        (first, second),
                    ),
                ),
            )
            Mem0VectorCalibrationService(repository, _AlignedEmbedder()).calibrate_namespace(
                case.namespace
            )
            dataset_hash = hashlib.sha256(dataset_path.read_bytes()).hexdigest()
            feature_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "dataset_id": "experience-test",
                        "dataset_hash": dataset_hash,
                        "embedding_provider": "fixture",
                        "embedding_model": "aligned-v1",
                        "cases": [
                            {
                                "question_id": case.question_id,
                                "namespace": case.namespace,
                                "question_hash": content_hash(case.question),
                                "query_tags": [],
                                "query_vector": [1.0, 0.0],
                                "warnings": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = Mem0ExperienceSuite(repository, embedder=_AlignedEmbedder()).run(
                suite_id="experience-test-v1",
                dataset_path=dataset_path,
                dataset_id="experience-test",
                query_feature_path=feature_path,
                namespace_prefix="experience",
                question_ids=(case.question_id,),
                feedback_selection="all_relevant",
                learning_policy=ATOM_CO_USED_LEARNING_POLICY,
                usage_round_count=1,
                top_k=3,
            )

        self.assertEqual(len(report.snapshots), 2)
        self.assertEqual(
            report.learning_policy["policy_id"],
            ATOM_CO_USED_LEARNING_POLICY.policy_id,
        )
        self.assertEqual(report.usage_rounds[0].selected_items, 2)
        self.assertEqual(report.usage_rounds[0].atom_link_updates, 1)
        self.assertEqual(report.usage_rounds[0].tag_relation_updates, 0)
        self.assertEqual(
            report.snapshots[-1]["graph"]["atom_link_counts"][AtomLinkRelation.CO_USED.value],
            1,
        )
        self.assertGreater(report.snapshots[-1]["graph"]["co_used_weight"], 0.0)
        self.assertGreater(
            report.snapshots[-1]["retrieval"]["hybrid_graph"]["experience_metrics"][
                "useful_context_fraction"
            ],
            0.0,
        )
        self.assertTrue(report.mechanical_passed)


if __name__ == "__main__":
    unittest.main()
