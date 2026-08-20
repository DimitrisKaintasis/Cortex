import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from temporal_history.core import NormalizedEvent, Period

from data_retrieval.tagging.openrouter import OpenRouterTagProposer
from data_retrieval.temporal.openrouter import OpenRouterTemporalSummarizer


class FakeResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class OpenRouterTagProposerTests(unittest.TestCase):
    @patch("data_retrieval.inference.openrouter.urlopen")
    def test_sends_strict_batch_schema_and_parses_tags(self, mock_open) -> None:
        model_result = {
            "items": [
                {
                    "atom_id": "item_0",
                    "tags": [{"text": "Data Retrieval", "confidence": 0.94}],
                },
                {
                    "atom_id": "item_1",
                    "tags": [{"text": "Remote Inference", "confidence": 0.87}],
                },
            ]
        }
        mock_open.return_value = FakeResponse(
            {"choices": [{"message": {"content": json.dumps(model_result)}}]}
        )
        proposer = OpenRouterTagProposer(api_key="test-key")

        proposals = proposer.propose_tags_batch(
            texts=("Atoms support retrieval.", "The Mac runs models."),
            namespace="project-a",
            existing_tags=("architecture",),
        )

        self.assertEqual(proposals[0][0].text, "Data Retrieval")
        self.assertEqual(proposals[1][0].confidence, 0.87)
        request = mock_open.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(payload["model"], "openai/gpt-5.6-luna")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.assertTrue(payload["provider"]["require_parameters"])
        self.assertEqual(payload["provider"]["data_collection"], "deny")
        self.assertEqual(payload["reasoning"]["effort"], "none")
        schema = payload["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["properties"]["items"]["minItems"], 2)
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")


class OpenRouterTemporalSummarizerTests(unittest.TestCase):
    @patch("data_retrieval.inference.openrouter.urlopen")
    def test_returns_temporal_summary_from_strict_json(self, mock_open) -> None:
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
        mock_open.return_value = FakeResponse(
            {"choices": [{"message": {"content": json.dumps(summary)}}]}
        )
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
        summarizer = OpenRouterTemporalSummarizer(api_key="test-key")

        result = summarizer.summarize_raw_period(period, (event,), {})

        self.assertEqual(result.content.summary_text, summary["summary_text"])
        self.assertEqual(result.content.decisions, summary["decisions"])
        request = mock_open.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["reasoning"]["effort"], "low")
        self.assertEqual(
            set(payload["response_format"]["json_schema"]["schema"]["required"]),
            set(summary),
        )


if __name__ == "__main__":
    unittest.main()
