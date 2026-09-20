from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from cortex import CortexClient, Outcome, OutcomeValue, Query, TemporalQueryMode
from cortex.testing import validate_source_sync
from cortex_slack import (
    SlackConnector,
    SlackConnectorConfig,
    SlackEventPage,
    SlackMapper,
    SlackMessage,
    event_pages_from_mapping,
)
from cortex_slack.cli import build_parser, channel_projects
from data_retrieval.api import LocalApiConfig, create_app


def _pages() -> tuple[SlackEventPage, ...]:
    path = Path(__file__).parents[1] / "evals" / "slack_event_pages_v1.json"
    return event_pages_from_mapping(json.loads(path.read_text(encoding="utf-8")))


class _ApiTransport(httpx.BaseTransport):
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.client.request(
            request.method,
            request.url.raw_path.decode("ascii"),
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )


def _config(*, suffix: str = "") -> SlackConnectorConfig:
    return SlackConnectorConfig(
        organization_id=f"org:acme{suffix}",
        workspace_id=f"T001{suffix}",
        channel_projects={"C-AUTH": f"project:payments{suffix}"},
        batch_size=2,
    )


def _round_trip(storage: LocalApiConfig, config: SlackConnectorConfig) -> None:
    mapper = SlackMapper(config)
    with TestClient(create_app(storage)) as api_client:
        with CortexClient(
            base_url="http://cortex.test",
            transport=_ApiTransport(api_client),
        ) as client:
            connector = SlackConnector(client, mapper)
            first = connector.sync_pages(_pages())
            replay = connector.sync_pages(_pages())
            if first != replay:
                raise AssertionError("exact Slack page replay changed acknowledgements")
            if first[-1].committed_cursor != "event-page:18":
                raise AssertionError("Slack cursor did not advance to the final page")

            context = client.query(
                Query(
                    request_id=f"slack-query:round-trip{config.workspace_id}",
                    query="PostgreSQL",
                    scope=config.scope_for_channel("C-AUTH"),
                    top_k=10,
                    temporal_mode=TemporalQueryMode.CURRENT_STATE,
                    reference_time=datetime(2026, 9, 18, 10, 30, tzinfo=UTC),
                )
            )
            pointers = mapper.pointers(context)
            edited = next(
                pointer for pointer in pointers if pointer.message_ts == "100.0001"
            )
            if edited.external_version != "edited:100.0005":
                raise AssertionError("Slack edit did not become the current version")
            if edited.channel_id != "C-AUTH" or edited.author_id != "U-ALICE":
                raise AssertionError("Slack navigation metadata was not preserved")

            deleted = client.query(
                Query(
                    request_id=f"slack-query:deleted{config.workspace_id}",
                    query="Celery",
                    scope=config.scope_for_channel("C-AUTH"),
                    top_k=10,
                    temporal_mode=TemporalQueryMode.CURRENT_STATE,
                    reference_time=datetime(2026, 9, 18, 10, 30, tzinfo=UTC),
                )
            )
            if deleted.items:
                raise AssertionError("deleted Slack evidence remained in current serving")

            accepted = client.report_outcome(
                Outcome(
                    request_id=f"slack-outcome:round-trip{config.workspace_id}",
                    retrieval_id=context.retrieval_id,
                    used_evidence_ids=(edited.evidence_id,),
                    outcome=OutcomeValue.POSITIVE,
                    occurred_at=datetime(2026, 9, 18, 10, 31, tzinfo=UTC),
                    reason="The agent used the current Slack decision.",
                )
            )
            if accepted.retrieval_id != context.retrieval_id:
                raise AssertionError("Slack outcome lost retrieval attribution")


class SlackConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pages = _pages()
        self.config = _config()
        self.mapper = SlackMapper(self.config)

    def test_event_pages_map_edits_threads_deletions_and_cursors(self) -> None:
        first_batches = self.mapper.batches(self.pages[0])
        second_batches = self.mapper.batches(self.pages[1])
        first = validate_source_sync(self.mapper.source(), first_batches)
        second = validate_source_sync(self.mapper.source(), second_batches)

        self.assertEqual(first.record_count, 2)
        self.assertEqual(second.record_count, 2)
        self.assertEqual(second.relation_count, 1)
        self.assertEqual(second.tombstone_count, 1)
        self.assertEqual(second.batch_count, 3)
        self.assertEqual(second_batches[0].run.previous_cursor, "event-page:17")
        self.assertEqual(second_batches[0].run.proposed_cursor, "event-page:18")
        self.assertEqual(
            second_batches[0].records[0].ref.external_version,
            "edited:100.0005",
        )
        self.assertEqual(second_batches[1].relations[0].relation_type, "reply_to")
        self.assertEqual(
            second_batches[2].tombstones[0].tombstone_version,
            "deleted:100.0006",
        )

    def test_unknown_channel_fails_closed_before_mapping(self) -> None:
        message = SlackMessage(
            channel_id="C-PRIVATE",
            message_ts="200.0001",
            text="private message",
            author_id="U-PRIVATE",
            observed_at=datetime(2026, 9, 18, 11, 0, tzinfo=UTC),
            occurred_at=datetime(2026, 9, 18, 10, 59, tzinfo=UTC),
        )
        page = replace(
            self.pages[0],
            page_id="private-page",
            messages=(message,),
            deletions=(),
        )

        with self.assertRaisesRegex(ValueError, "not mapped"):
            self.mapper.batches(page)

    def test_page_cursor_chain_and_reply_version_are_strict(self) -> None:
        with self.assertRaisesRegex(ValueError, "contiguous chain"):
            event_pages_from_mapping(
                {
                    "pages": [
                        {
                            "page_id": "one",
                            "previous_cursor": None,
                            "next_cursor": "cursor:one",
                            "received_at": "2026-09-18T10:00:00+00:00",
                            "messages": [
                                {
                                    "channel_id": "C-AUTH",
                                    "message_ts": "1.0",
                                    "text": "one",
                                    "author_id": "U1",
                                    "observed_at": "2026-09-18T10:00:00+00:00",
                                    "occurred_at": "2026-09-18T10:00:00+00:00"
                                }
                            ]
                        },
                        {
                            "page_id": "two",
                            "previous_cursor": "wrong",
                            "next_cursor": "cursor:two",
                            "received_at": "2026-09-18T10:01:00+00:00",
                            "deletions": [
                                {
                                    "channel_id": "C-AUTH",
                                    "message_ts": "1.0",
                                    "event_ts": "2.0",
                                    "observed_at": "2026-09-18T10:01:00+00:00"
                                }
                            ]
                        }
                    ]
                }
            )

        with self.assertRaisesRegex(ValueError, "thread_root_version"):
            SlackMessage(
                channel_id="C-AUTH",
                message_ts="2.0",
                text="reply",
                author_id="U2",
                observed_at=datetime(2026, 9, 18, 10, 0, tzinfo=UTC),
                occurred_at=datetime(2026, 9, 18, 10, 0, tzinfo=UTC),
                thread_ts="1.0",
            )

    def test_sdk_round_trip_serves_edit_and_suppresses_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            _round_trip(
                LocalApiConfig(database_path=Path(directory) / "slack.sqlite3"),
                self.config,
            )

    def test_cli_channel_allowlist_is_explicit(self) -> None:
        args = build_parser().parse_args(
            [
                "events.json",
                "--organization-id",
                "org:acme",
                "--workspace-id",
                "T001",
                "--channel-project",
                "C-AUTH=project:payments",
            ]
        )
        self.assertEqual(
            channel_projects(args.channel_project),
            {"C-AUTH": "project:payments"},
        )
        with self.assertRaisesRegex(ValueError, "conflicting"):
            channel_projects(["C-AUTH=one", "C-AUTH=two"])

    def test_reference_package_does_not_import_cortex_core(self) -> None:
        package = Path(__file__).parents[1] / "src" / "cortex_slack"
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in package.glob("*.py")
        )
        self.assertNotIn("data_retrieval", source)


@unittest.skipUnless(
    os.getenv("DATA_RETRIEVAL_TEST_POSTGRES_DSN"),
    "DATA_RETRIEVAL_TEST_POSTGRES_DSN is not configured",
)
class PostgreSQLSlackConnectorTests(unittest.TestCase):
    def test_sdk_to_postgresql_edit_and_deletion_round_trip(self) -> None:
        _round_trip(
            LocalApiConfig(
                postgres_dsn=os.environ["DATA_RETRIEVAL_TEST_POSTGRES_DSN"]
            ),
            _config(suffix=":c6-postgres"),
        )


if __name__ == "__main__":
    unittest.main()
