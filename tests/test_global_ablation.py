from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from data_retrieval.benchmarks.global_ablation import GlobalAblationRunner
from data_retrieval.storage.memory import InMemoryRepository


def _case() -> dict[str, object]:
    return {
        "question_id": "q-1",
        "question_type": "knowledge-update",
        "question": "Where did I move?",
        "answer": "Athens",
        "question_date": "2024/03/03 (Sun) 12:30",
        "haystack_session_ids": ["sess-1", "sess-2"],
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
        "answer_session_ids": ["sess-2"],
    }


class GlobalAblationTests(unittest.TestCase):
    def test_global_ablation_runner_evaluates_single_namespace(self) -> None:
        c1 = _case()
        c2 = _case()
        c2["question_id"] = "q-2"
        c2["question"] = "Where was my old home?"

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "test_ablation.json"
            path.write_text(json.dumps([c1, c2]), encoding="utf-8")
            repository = InMemoryRepository()

            runner = GlobalAblationRunner(repository)
            report = runner.run(
                dataset_path=path,
                dataset_id="test-global",
                top_k=5,
            )

        self.assertEqual(report.case_count, 2)
        self.assertEqual(report.total_sessions_in_namespace, 4)
        self.assertIn("variant_0_lexical", report.variant_summaries)
        self.assertIn("variant_2_atom_engine", report.variant_summaries)


if __name__ == "__main__":
    unittest.main()
