from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from data_retrieval.benchmarks.longmemeval import (
    LongMemEvalIngestService,
    iter_longmemeval_cases,
)
from data_retrieval.benchmarks.longmemeval_pipeline import LongMemEvalPipelineRunner
from data_retrieval.storage.memory import InMemoryRepository


def _case() -> dict[str, object]:
    return {
        "question_id": "question-1",
        "question_type": "knowledge-update",
        "question": "Which city do I live in now?",
        "answer": "Athens",
        "question_date": "2024/03/03 (Sun) 12:30",
        "haystack_session_ids": ["session-old", "session-new"],
        "haystack_dates": [
            "2024/01/01 (Mon) 09:00",
            "2024/03/01 (Fri) 11:15",
        ],
        "haystack_sessions": [
            [
                {"role": "user", "content": "I live in Patras."},
                {"role": "assistant", "content": "Patras sounds lovely."},
            ],
            [
                {
                    "role": "user",
                    "content": "I moved to Athens.",
                    "has_answer": True,
                },
                {"role": "assistant", "content": "I will remember that."},
            ],
        ],
        "answer_session_ids": ["session-new"],
    }


class LongMemEvalTests(unittest.TestCase):
    def test_ingest_can_select_question_ids_without_ingesting_other_cases(self) -> None:
        first = _case()
        second = _case()
        second["question_id"] = "question-2"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selected.json"
            path.write_text(json.dumps([first, second]), encoding="utf-8")
            repository = InMemoryRepository()

            result = LongMemEvalIngestService(repository).ingest_path(
                path=path, question_ids=("question-2",)
            )

        self.assertEqual(tuple(case.question_id for case in result.cases), ("question-2",))
        self.assertEqual(repository.list_namespaces(), (result.cases[0].namespace,))

    def test_pipeline_report_scores_retrieved_evidence(self) -> None:
        case = _case()
        case["question"] = "Where did I move to Athens?"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pipeline.json"
            path.write_text(json.dumps([case]), encoding="utf-8")
            report = LongMemEvalPipelineRunner(InMemoryRepository()).run(
                dataset_path=path,
                dataset_id="pipeline-test",
                top_k=5,
            )

        self.assertEqual(report.evaluated_case_count, 1)
        self.assertEqual(report.session_hit_at_k, 1.0)
        self.assertEqual(report.turn_hit_at_k, 1.0)
        self.assertGreater(report.mean_reciprocal_rank, 0.0)
        self.assertEqual(report.direct_session_hit_at_k, 1.0)
        self.assertEqual(report.direct_turn_hit_at_k, 1.0)
        self.assertGreater(report.direct_mean_reciprocal_rank, 0.0)
        self.assertIn("metric_semantics", report.as_dict())

    def test_pipeline_excludes_abstention_suffix_from_retrieval_metrics(self) -> None:
        case = _case()
        case["question_id"] = "question-1_abs"
        case["question"] = "Where did I move to Athens?"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "abstention-pipeline.json"
            path.write_text(json.dumps([case]), encoding="utf-8")
            report = LongMemEvalPipelineRunner(InMemoryRepository()).run(
                dataset_path=path,
                dataset_id="pipeline-test",
            )

        self.assertEqual(report.evaluated_case_count, 0)
        self.assertEqual(report.abstention_case_count, 1)

    def test_pipeline_parallel_results_preserve_dataset_order(self) -> None:
        first = _case()
        second = _case()
        second["question_id"] = "question-2"
        second["question"] = "Which city was my previous home?"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "parallel-pipeline.json"
            path.write_text(json.dumps([first, second]), encoding="utf-8")
            report = LongMemEvalPipelineRunner(InMemoryRepository()).run(
                dataset_path=path,
                dataset_id="pipeline-parallel-test",
                max_workers=2,
            )

        self.assertEqual(report.case_count, 2)
        self.assertEqual(
            [case["question_id"] for case in report.cases],
            ["question-1", "question-2"],
        )

    def test_pipeline_can_select_question_ids(self) -> None:
        first = _case()
        second = _case()
        second["question_id"] = "question-2"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selected-pipeline.json"
            path.write_text(json.dumps([first, second]), encoding="utf-8")
            report = LongMemEvalPipelineRunner(InMemoryRepository()).run(
                dataset_path=path,
                dataset_id="pipeline-selected-test",
                question_ids=("question-2",),
            )

        self.assertEqual(report.case_count, 1)
        self.assertEqual(report.cases[0]["question_id"], "question-2")

    def test_streams_cases_and_preserves_session_structure_without_label_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oracle.json"
            path.write_text(json.dumps([_case()]), encoding="utf-8")

            parsed = tuple(iter_longmemeval_cases(path))
            repository = InMemoryRepository()
            first = LongMemEvalIngestService(repository).ingest_path(
                path=path,
                dataset_id="oracle-2025-09",
            )
            second = LongMemEvalIngestService(repository).ingest_path(
                path=path,
                dataset_id="oracle-2025-09",
            )

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].sessions[1].turns[0].role, "user")
        self.assertIsNotNone(parsed[0].sessions[1].occurred_at.utcoffset())
        self.assertEqual(first.case_count, 1)
        self.assertEqual(first.inserted_session_count, 2)
        self.assertEqual(first.atom_count, 4)
        self.assertEqual(len(first.cases[0].evidence_atom_ids), 1)
        self.assertEqual(second.inserted_session_count, 0)
        self.assertEqual(second.reused_session_count, 2)

        atoms = repository.list_atoms(namespace=first.cases[0].namespace)
        self.assertEqual([atom.position for atom in atoms], [0, 1, 0, 1])
        self.assertEqual(
            {atom.metadata["session_id"] for atom in atoms},
            {"session-old", "session-new"},
        )
        self.assertTrue(all("has_answer" not in atom.metadata for atom in atoms))
        self.assertTrue(all("answer" not in atom.metadata for atom in atoms))
        self.assertTrue(all("question" not in atom.metadata for atom in atoms))

    def test_rejects_misaligned_parallel_session_arrays(self) -> None:
        malformed = _case()
        malformed["haystack_dates"] = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed.json"
            path.write_text(json.dumps([malformed]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "equal lengths"):
                tuple(iter_longmemeval_cases(path))

    def test_accepts_empty_abstention_answer(self) -> None:
        abstention = _case()
        abstention["question_id"] = "question-abs"
        abstention["answer"] = ""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "abstention.json"
            path.write_text(json.dumps([abstention]), encoding="utf-8")
            parsed = tuple(iter_longmemeval_cases(path))

        self.assertEqual(parsed[0].answer, "")

    def test_normalizes_numeric_answer_to_text(self) -> None:
        numeric = _case()
        numeric["answer"] = 42
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "numeric.json"
            path.write_text(json.dumps([numeric]), encoding="utf-8")
            parsed = tuple(iter_longmemeval_cases(path))

        self.assertEqual(parsed[0].answer, "42")

    def test_preserves_repeated_session_ids_as_distinct_documents(self) -> None:
        repeated = _case()
        repeated["haystack_session_ids"] = ["repeated", "repeated"]
        repeated["answer_session_ids"] = ["repeated"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "repeated.json"
            path.write_text(json.dumps([repeated]), encoding="utf-8")
            repository = InMemoryRepository()
            result = LongMemEvalIngestService(repository).ingest_path(path=path)

        atoms = repository.list_atoms(namespace=result.cases[0].namespace)
        self.assertEqual(result.session_count, 2)
        self.assertEqual(len({atom.document_id for atom in atoms}), 2)
        self.assertEqual(
            {atom.metadata["session_occurrence"] for atom in atoms},
            {1, 2},
        )


if __name__ == "__main__":
    unittest.main()
