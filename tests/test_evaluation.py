from __future__ import annotations

import unittest
from pathlib import Path

from data_retrieval.evaluation import EvaluationRunner
from data_retrieval.storage.memory import InMemoryRepository


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


class EvaluationRunnerTests(unittest.TestCase):
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
