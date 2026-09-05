from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_retrieval.storage.sqlite import SQLiteRepository
from scripts import run_locomo_learning as pilot
from scripts.run_locomo_learning import ingest, select_feedback, split_sample


def fixture():
    return {
        "sample_id": "test",
        "conversation": {
            "session_1_date_time": "1:56 pm on 8 May, 2023",
            "session_1": [
                {"dia_id": f"D1:{i}", "speaker": "A", "text": f"Statement {i}"} for i in range(1, 5)
            ],
        },
        "qa": [
            {
                "question": f"Question {i}?",
                "answer": "answer",
                "evidence": [f"D1:{i}"],
                "category": 1,
            }
            for i in range(1, 5)
        ],
    }


class LoCoMoLearningTests(unittest.TestCase):
    def test_split_is_deterministic_and_evidence_disjoint(self):
        config = {
            "eligible_categories": [1, 2, 4],
            "seed": "test",
            "training_questions_per_history": 1,
            "evaluation_per_evidence_group": 15,
        }
        first = split_sample(fixture(), config)
        self.assertEqual(first, split_sample(fixture(), config))
        training_evidence = set(first["training"][0]["evidence"])
        self.assertEqual(len(first["evaluation"]), 3)
        self.assertTrue(
            all(
                q["group"] == "disjoint" and not training_evidence.intersection(q["evidence"])
                for q in first["evaluation"]
            )
        )

    def test_invalid_image_and_duplicate_labels_are_excluded(self):
        sample = fixture()
        sample["conversation"]["session_1"][0]["img_url"] = "https://example.com/image"
        sample["qa"][1]["evidence"] = ["missing"]
        sample["qa"].append(sample["qa"][2].copy())
        result = split_sample(
            sample,
            {
                "eligible_categories": [1],
                "seed": "test",
                "training_questions_per_history": 1,
                "evaluation_per_evidence_group": 15,
            },
        )
        self.assertEqual(result["eligible"], 2)
        self.assertEqual(
            result["excluded"],
            {"image_or_empty_evidence": 1, "missing_evidence": 1, "duplicate_question": 1},
        )

    def test_ingestion_preserves_evidence_and_never_ingests_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteRepository(Path(directory) / "test.sqlite3")
            try:
                namespace = ingest(repository, fixture())
                ingest(repository, fixture())
                atoms = [a for batch in repository.iter_atoms(namespace=namespace) for a in batch]
                self.assertEqual(len(atoms), 4)
                self.assertTrue(all("answer" not in a.content for a in atoms))
                self.assertEqual(
                    {a.metadata["dia_id"] for a in atoms}, {f"D1:{i}" for i in range(1, 5)}
                )
                self.assertTrue(all(a.occurred_at.tzinfo is not None for a in atoms))
            finally:
                repository.close()

    def test_feedback_never_credits_non_gold_or_derived_items(self):
        row = {"retrieved_atom_ids": ["derived", "gold", "other"]}
        self.assertEqual(select_feedback(row, ("gold", "unreturned")), ("gold",))

    def test_feature_preparation_visits_every_history_and_resumes_cached_queries(self):
        class FakeApi:
            def __init__(self):
                self.usage = []
                self.calls = []

            def embeddings(self, texts, model):
                return [[1.0, 0.0] for _ in texts]

            def tags(self, queries, catalog, model):
                self.calls.extend(q["id"] for q in queries)
                return {q["id"]: [] for q in queries}

        config = {"embedding_model": "fixture", "tag_model": "fixture"}
        manifest = {
            "histories": [
                {
                    "sample_id": sid,
                    "training": [{"id": sid + "-train", "query": "Training?"}],
                    "evaluation": [{"id": sid + "-eval", "query": "Evaluation?"}],
                }
                for sid in ("first", "second")
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            repository = SQLiteRepository(root / "prepared.sqlite3")
            try:
                for history in manifest["histories"]:
                    sample = fixture()
                    sample["sample_id"] = history["sample_id"]
                    ingest(repository, sample)
                    (root / (history["sample_id"] + "-mem0.json")).write_text("{}")
            finally:
                repository.close()
            api = FakeApi()
            with patch.object(pilot, "ROOT", root), patch.object(pilot, "Api", return_value=api):
                pilot.features(config, manifest)
                self.assertEqual(
                    set(api.calls), {"first-train", "first-eval", "second-train", "second-eval"}
                )
                self.assertEqual(len(list((root / "query-cache").glob("*.json"))), 4)
                self.assertTrue((root / "features-complete.json").is_file())
                api.calls.clear()
                pilot.features(config, manifest)
                self.assertEqual(api.calls, [])


if __name__ == "__main__":
    unittest.main()
