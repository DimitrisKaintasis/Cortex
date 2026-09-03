from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from data_retrieval.benchmarks.longmemeval_ablation import LongMemEvalAblationSuite
from data_retrieval.domain.models import TagLevel
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.tagging.proposals import TagProposal


class StubTagProposer:
    evidence_source = "stub:query-tags"
    proposal_version = "stub-query-tags-v1"

    def __init__(self) -> None:
        self.calls = 0

    def propose_tags(self, *, text, namespace, existing_tags):
        self.calls += 1
        return (TagProposal("city", 0.9, TagLevel.SPECIFIC),)


class StubEmbedder:
    provider = "stub"
    model = "stub-v1"

    def __init__(self) -> None:
        self.query_calls = 0

    def embed_documents(self, texts):
        return tuple((1.0, 0.0) for _ in texts)

    def embed_query(self, text):
        self.query_calls += 1
        return (1.0, 0.0)


def _case() -> dict[str, object]:
    return {
        "question_id": "question-1",
        "question_type": "single-session-user",
        "question": "Which city did I move to?",
        "answer": "Athens",
        "question_date": "2024/03/03 (Sun) 12:30",
        "haystack_session_ids": ["session-1"],
        "haystack_dates": ["2024/03/01 (Fri) 11:15"],
        "haystack_sessions": [
            [
                {
                    "role": "user",
                    "content": "I moved to Athens.",
                    "has_answer": True,
                }
            ]
        ],
        "answer_session_ids": ["session-1"],
    }


class LongMemEvalAblationTests(unittest.TestCase):
    def test_freezes_query_features_and_reuses_them_across_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            cache = root / "query-features.json"
            dataset.write_text(json.dumps([_case()]), encoding="utf-8")
            proposer = StubTagProposer()
            embedder = StubEmbedder()
            suite = LongMemEvalAblationSuite(
                InMemoryRepository(),
                tag_proposer=proposer,
                embedder=embedder,
            )

            first = suite.run(
                dataset_path=dataset,
                dataset_id="ablation-test",
                query_feature_path=cache,
                top_ks=(1,),
            )
            second = suite.run(
                dataset_path=dataset,
                dataset_id="ablation-test",
                query_feature_path=cache,
                top_ks=(1,),
            )

        self.assertFalse(first["query_features"]["reused"])
        self.assertTrue(second["query_features"]["reused"])
        self.assertEqual(proposer.calls, 1)
        self.assertEqual(embedder.query_calls, 1)
        self.assertEqual(len(first["results"]), 7)
        self.assertEqual(
            {result["profile"] for result in first["results"]},
            set(first["profiles"]),
        )


if __name__ == "__main__":
    unittest.main()
