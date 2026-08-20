import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from temporal_history.core import NormalizedEvent, Period

from data_retrieval.tagging.ollama import OllamaError, OllamaTagProposer
from data_retrieval.temporal.ollama import OllamaTemporalSummarizer


class FakeResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class OllamaTagProposerTests(unittest.TestCase):
    @patch("data_retrieval.tagging.ollama.urlopen")
    def test_sends_structured_non_streaming_request_and_parses_tags(self, mock_open) -> None:
        mock_open.return_value = FakeResponse(
            {
                "message": {
                    "content": (
                        "```json\n"
                        + json.dumps(
                            {
                                "items": [
                                    {
                                        "atom_id": "item_0",
                                        "tags": [
                                            {"text": "Data Retrieval", "confidence": 0.91}
                                        ],
                                    }
                                ]
                            }
                        )
                        + "\n```"
                    )
                }
            }
        )
        proposer = OllamaTagProposer(
            base_url="http://127.0.0.1:11435/",
            model="test-model",
        )

        proposals = proposer.propose_tags(
            text="Atoms make knowledge retrievable.",
            namespace="project-a",
            existing_tags=("architecture",),
        )

        self.assertEqual(proposals[0].text, "Data Retrieval")
        self.assertEqual(proposals[0].confidence, 0.91)
        request = mock_open.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "http://127.0.0.1:11435/api/chat")
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["format"], "json")

    @patch("data_retrieval.tagging.ollama.urlopen")
    def test_rejects_invalid_model_content(self, mock_open) -> None:
        mock_open.return_value = FakeResponse({"message": {"content": "not-json"}})
        proposer = OllamaTagProposer(
            base_url="http://127.0.0.1:11435",
            model="test-model",
        )

        with self.assertRaisesRegex(OllamaError, "invalid structured response"):
            proposer.propose_tags(
                text="Some text",
                namespace="project-a",
                existing_tags=(),
            )


class OllamaTemporalSummarizerTests(unittest.TestCase):
    @patch("data_retrieval.tagging.ollama.urlopen")
    def test_returns_temporal_summary_content_from_structured_json(self, mock_open) -> None:
        summary = {
            "summary_text": "The team retained raw atoms before enrichment.",
            "topics": ["ingestion architecture"],
            "decisions": ["Retain raw atoms before enrichment."],
            "actions": [],
            "open_questions": [],
            "participants": ["user-1"],
            "thread_references": [],
            "artefact_references": [],
            "coverage_gaps": [],
        }
        mock_open.return_value = FakeResponse({"message": {"content": json.dumps(summary)}})
        start = datetime(2026, 8, 19, 12, tzinfo=UTC)
        period = Period(
            timeline_id="main",
            channel_id="main",
            granularity="six_hour",
            timezone="UTC",
            local_start=start,
            local_end=start + timedelta(hours=6),
            utc_start=start,
            utc_end=start + timedelta(hours=6),
        )
        event = NormalizedEvent(
            event_id="atom-1",
            timeline_id="main",
            occurred_at=start,
            recorded_at=start,
            source_order="1",
            actor_id="user-1",
            raw_content="Decision: retain raw atoms before enrichment.",
            display_content="Decision: retain raw atoms before enrichment.",
            source_message_id="message-1",
        )
        summarizer = OllamaTemporalSummarizer(
            base_url="http://127.0.0.1:11435",
            model="test-model",
        )

        result = summarizer.summarize_raw_period(period, (event,), {})

        self.assertEqual(result.content.summary_text, summary["summary_text"])
        self.assertEqual(result.content.decisions, summary["decisions"])
        request = mock_open.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertFalse(payload["think"])
        self.assertEqual(payload["format"], "json")
        self.assertIn("source events", payload["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
