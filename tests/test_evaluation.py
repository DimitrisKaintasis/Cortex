from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from data_retrieval.evaluation import EvaluationRunner
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.tagging.proposals import TagProposal


class PurposeBuiltEmbedder:
    provider = "test"
    model = "purpose-built"

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        vectors = []
        for text in texts:
            normalized = text.casefold()
            if "car engine" in normalized or "automobile powertrain" in normalized:
                vectors.append((1.0, 0.0, 0.0))
            elif (
                "sqlite database" in normalized
                or "persistent data" in normalized
                or "αποθηκεύονται" in normalized
            ):
                vectors.append((0.0, 1.0, 0.0))
            elif "ssh local" in normalized or "conexiunea laptopului" in normalized:
                vectors.append((0.5, 0.5, 0.0))
            elif "retry helper" in normalized or "retrying failed" in normalized:
                vectors.append((0.5, 0.0, 0.5))
            else:
                vectors.append((0.0, 0.0, 1.0))
        return tuple(vectors)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self.embed_documents((text,))[0]


class PurposeBuiltTagProposer:
    evidence_source = "test-query-tags"
    proposal_version = "test-query-tags-v1"

    def propose_tags(self, *, text, namespace, existing_tags):
        return (TagProposal("vehicles", 1.0),)


class EvaluationRunnerTests(unittest.TestCase):
    def test_runner_can_evaluate_generated_query_tag_path(self) -> None:
        dataset = {
            "namespace": "evaluation:query-tags",
            "documents": [
                {
                    "source": "target",
                    "text": "A car engine converts fuel into motion.",
                    "tags": ["vehicles"],
                },
                {
                    "source": "distractor",
                    "text": "Bread requires flour, yeast, and heat.",
                    "tags": ["cooking"],
                },
            ],
            "cases": [
                {
                    "id": "generated-query-tag",
                    "category": "tags",
                    "query": "How does the powertrain work?",
                    "expected_sources": ["target"],
                    "top_k": 1,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "query-tags.json"
            path.write_text(json.dumps(dataset), encoding="utf-8")
            report = EvaluationRunner(
                InMemoryRepository(),
                tag_proposer=PurposeBuiltTagProposer(),
            ).run(path)

        self.assertEqual(report.hit_at_k, 1.0)
        self.assertEqual(report.cases[0]["query_tags"], ["vehicles"])
        self.assertEqual(report.cases[0]["warnings"], [])

    def test_default_corpus_measures_semantic_and_temporal_behavior(self) -> None:
        dataset = Path(__file__).parents[1] / "evals" / "retrieval_cases.json"

        report = EvaluationRunner(InMemoryRepository(), embedder=PurposeBuiltEmbedder()).run(
            dataset
        )

        self.assertEqual(report.case_count, 9)
        self.assertEqual(report.category_metrics["code"]["hit_at_k"], 1.0)
        self.assertEqual(report.category_metrics["multilingual"]["hit_at_k"], 1.0)
        self.assertEqual(report.category_metrics["semantic"]["hit_at_k"], 1.0)
        self.assertEqual(report.category_metrics["temporal"]["hit_at_k"], 1.0)
        self.assertEqual(report.forbidden_violation_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
