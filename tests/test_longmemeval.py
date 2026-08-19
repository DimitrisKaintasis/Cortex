from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from data_retrieval.benchmarks.longmemeval import (
    LongMemEvalIngestService,
    iter_longmemeval_cases,
)
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
